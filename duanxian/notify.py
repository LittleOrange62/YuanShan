"""消息推送的发送层：把内容组装成一条可读的消息，交给 apprise 的四个渠道。

分工与纪律（与 `netconfig.py` + `duanxian/jobs.py` 那一套同形）：

- 配置（渠道、计划、留痕、校验、到点判定）只住 `notifyconfig.py`，这里**不写第二份规则**；
- **内容一律复用现成的读数块**，不另起一套口径：
  策略共振 `duanxian/confluence_view.summary()`、条件筛选 `duanxian/screen_view.run_many()`、
  自选明细 `duanxian/watchlist.watchlist()`、个股档案 `duanxian/stock_dossier.dossier()`、
  持仓 `duanxian/positions.report()`、市场概览 `duanxian/qd.sentiment_cycle()/board_ladder()`；
  10-17 又按用户"再具体一点、多一些"接了 11 档，全部走各页那份现成读层：
  倍量 `quant.volume_double()`、突破 `quant.breakout_signal()`、埋伏 `quant.ambush()`、
  象限 `quant.quadrant()`、异动 `abnormal_view.screen()`、冲高 `spike_view.screen()`、
  趋势股 `trend_view.screen()`、板块轮动 `qd.sector_cycle()`、分组分析
  `watchlist.group_analytics()`、快讯 `qd.news()`、分配者 `allocator_view.view()`。
  **每档的参数就是那个接口自己的参数**（`min_count`/`signal_type`/`lookback`…）——
  推送这一侧不新造判据、不重算阈值（要改阈值去那一页改），它只决定"取哪一档、取多少条"。
  这些都是各页已经在用的那份，所以"推送到手机的内容"与"页面上看到的"必然同源 ——
  两处各算一遍的话，第二天一定有人说"手机上和界面对不上"。
- **一块坏了不拖累整条消息**（照 `stock_dossier` 的规矩）：每个场景各自 try，
  失败就在正文里写成一行「这一档没取到：原因」，而不是让整条推送不发或发成空白。
- **起真网络请求的入口收成两个函数**：`_post_json()`（三个群机器人自己 POST）与
  `_apprise()`（只有邮件走 apprise 的 SMTP），测试只打这两个。
  理由与 `_spawn_stage` 同源（10-06 那次 pytest 真起了用户的通达信窗口）：
  它们在 pytest 进程里**硬挡真发** —— 否则跑一次测试就给用户的工作群发一条垃圾消息，
  那种事撤不回来。
- **为什么机器人不再交给 apprise**（10-16 实测，这条很重要）：apprise 2.0.1 的飞书插件
  只判 `r.status_code != 200` 就算发出去了（`plugins/feishu.py` 的 `send()` 末尾直接 `return True`），
  **而飞书/钉钉/企微对无效 token 一律回 HTTP 200 + 一段错误 JSON**。拿一个假 token 实测：
  `POST https://open.feishu.cn/open-apis/bot/v2/hook/<假token>/` 回 `200`、85 字节，
  apprise 打的是 `INFO Sent Feishu notification.`、页面显示「1 个渠道已交付」——
  那是一条**永远不会到账的"成功"**。所以这一格必须自己认回体。
- **密钥不上屏**：日志、记账 detail、异常文本里都只出现渠道键与掩码，绝不出现 URL。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import notifyconfig as NC

#: 每档场景的中文名 —— 面板多选框上写的字，也是正文里的小标题。
#: 长说明（面板上跟在名字后面那句）住 `notifyconfig.SCENE_HINTS`，两边都由测试钉住同集合。
SCENE_LABELS = {
    "confluence": "策略共振榜", "screen": "条件筛选结果", "watchlist": "自选股当日明细",
    "dossier": "个股量化档案", "positions": "持仓与浮动盈亏", "market": "市场概览",
    "voldbl": "倍量形态榜", "breakout": "突破信号", "ambush": "埋伏票榜",
    "quadrant": "四维象限榜", "abnormal": "异动筛查", "spike": "盘中冲高异动",
    "trendstock": "趋势股榜", "sector": "板块轮动", "groupanalytics": "自选分组分析",
    "news": "快讯要闻", "allocator": "分配者视角",
}
#: 默认条数上限：一条飞书/钉钉消息别写成长篇 —— 群消息读的是"今天有没有我要看的票"。
#: ⚠️ 这一格是**每档条数的唯一真源**：面板上的默认值与上限都从这里经 `scene_catalog()` 交出去，
#: 前端不另抄一份（抄的那份第二天就会与这里不一致，用户在界面上调的就不是发出去的长度）。
#: 新加的 11 档默认都压在 8~12：一条计划勾四五档时，1,900 字节（企微那条硬上限）
#: 只够每档十几行 —— 宁缺毋滥，长表让人在网站上看。
DEFAULT_LIMITS = {"confluence": 12, "screen": 12, "watchlist": 30, "dossier": 6,
                  "positions": 20, "market": 4,
                  "voldbl": 10, "breakout": 10, "ambush": 10, "quadrant": 10,
                  "abnormal": 8, "spike": 8, "trendstock": 10, "sector": 8,
                  "groupanalytics": 8, "news": 8, "allocator": 8}
#: 每档允许的上限（`market` 是"连板天梯列几层"，一共也没几层）。
#: 那一格到底在数什么，单位由后端说（自选是"条"、天梯是"层"）—— 让前端猜单位就会说出
#: 「市场概览 2 条」这种看着对其实错的话。
SCENE_LIMIT_UNITS = {"confluence": "只", "screen": "只", "watchlist": "只", "dossier": "只",
                    "positions": "只", "market": "层",
                    "voldbl": "只", "breakout": "只", "ambush": "只", "quadrant": "只",
                    "abnormal": "只", "spike": "只", "trendstock": "只", "sector": "个",
                    "groupanalytics": "组", "news": "条", "allocator": "个"}
#: 上限取得**不超过那一档读层接口自己肯收的值**（`abnormal_view.screen` 的 limit 超过 200 会直接
#: 回一句拒绝、`quant` 那几张榜内部截到 800、`qd.sector_cycle` 每天只留涨幅前 8 个板块）：
#: 超过就是"面板上填得进去、发出来是一句话对不上"。
MAX_LIMITS = {"confluence": 60, "screen": 60, "watchlist": 120, "dossier": 20,
              "positions": 60, "market": 9,
              "voldbl": 60, "breakout": 60, "ambush": 60, "quadrant": 60,
              "abnormal": 60, "spike": 60, "trendstock": 60, "sector": 8,
              "groupanalytics": 60, "news": 50, "allocator": 60}
#: 「个股档案」那一档能带的六块（`dossier.blocks`）。名字与 `_blk_dossier` 里读的
#: `blocks.<键>` 一一对应，面板上那份候选也从 `scene_catalog()` 的这份生成 ——
#: 界面上自己抄一份就会与正文里实际打印的块分叉。
DOSSIER_BLOCK_LABELS = {"oper": "操作分与象限", "volume_double": "倍量", "breakout": "突破",
                        "ambush": "埋伏", "confluence": "共振", "scores": "分项能力分"}
#: 正文长度的**按渠道上限**（字节）。原先是一个全局 3500，那是个错的抽象：
#: **企业微信 text 消息的硬上限是 2048 字节**（超了直接 `errcode 45004 size too large`），
#: 统一 3500 会让企微这一路长期被拒 —— 而拒了我们是能看见的，但没人愿意每天看一条红字。
#: 所以：一条计划发多个渠道时，取**这些渠道里最小的那个**，宁可短，不要发不出去。
BODY_LIMITS = {"wecom": 1900,           # 2048 硬上限，留 148 字节余量（表情/换行都算字节）
               "feishu": 4000,
               "dingtalk": 4000,
               "mail": 20000}           # 邮件没有这个量级的限制，等于不截
MAX_BODY_BYTES = 3500        # 兜底：渠道未知/没配渠道时按这一格
SENT_FILE = "notify/sent.json"
HISTORY_LIMIT = 40


def body_limit(kinds) -> int:
    """这批渠道允许的最长正文（字节）。空/未知一律回 `MAX_BODY_BYTES`，不回 0。"""
    limits = [BODY_LIMITS[k] for k in (kinds or []) if k in BODY_LIMITS]
    return min(limits) if limits else MAX_BODY_BYTES


def sent_path() -> str:
    return os.environ.get("VIBE_NOTIFY_SENT") or str(NC.data_home() / SENT_FILE)


# ---------------------------------------------------------------- 取数（只调现成读层）
def _session_date(date: str = "") -> str:
    """基准日：给了就用给的，否则取库里最近一个已收盘交易日。

    为什么不能让推送按"本机今天"取数：休市日与凌晨都会拿到一个库里根本没有的日子，
    然后报成"没有数据" —— 那是判据错了，不是数据缺。与体检「当日数据到位」同一个来源。
    """
    if date:
        return str(date)[:10]
    from duanxian import pg
    return str(pg.latest_session("market_data.stock_daily") or "")[:10]


def _pct(value) -> str:
    """涨跌一律写成 `+1.23%`；取不到就是 `—`，**不许拿 0 充数**。"""
    if isinstance(value, (int, float)):
        return f"{value:+.2f}%"
    return "—"


def _cnt(value) -> str:
    """个数写成整数：读层有时交 `917.0`（float 列），手机上出现小数点会让人以为是比例。"""
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return "—"


def _lim(params: dict, scene: str) -> int:
    """这一档列几条：面板没填就用 `DEFAULT_LIMITS`，填了也**不越过 `MAX_LIMITS`**。
    截在上限而不是报错，是因为这条消息是定时发出去的 —— 到点发不出去比少列两行更糟。"""
    raw = params.get("limit")
    try:
        value = int(raw) if raw not in (None, "") else DEFAULT_LIMITS[scene]
    except (TypeError, ValueError):
        value = DEFAULT_LIMITS[scene]
    return max(1, min(value, MAX_LIMITS.get(scene, 500)))


def _multi(raw) -> list[str]:
    """多选值 → 干净列表。清单里同时收过 `["dgt"]`、`"dgt,rank"`、`[{"key":…}]` 三种形状
    （面板写回的、人手改配置写的、后端引用对象带出来的），这里一次收口。"""
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else str(raw).replace("，", ",").split(",")
    out = []
    for one in items:
        if isinstance(one, dict):
            one = one.get("key") or one.get("rule_key") or ""
        text = str(one).strip().strip("'\"")
        if text and text not in out:
            out.append(text)
    return out


def _joined(raw) -> str:
    """读层那侧要的是逗号串（`quadrants='Q1,Q2'`、`types='sticky_pull'`）。"""
    return ",".join(_multi(raw))


def _num(params: dict, key: str, default=None):
    """数字格：留空/填坏都回 `default`（执行体不许因为面板上一个空格就崩）。"""
    raw = params.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return int(value) if float(value).is_integer() else value


def _flag(params: dict, key: str) -> bool:
    return str(params.get(key) if params.get(key) is not None else "").strip().lower() in (
        "1", "true", "yes", "on", "勾") or params.get(key) is True


def _avail(res: dict, label: str) -> str:
    """读层回 `available=False` 时那一句"没取到"的**唯一写法**。
    原因原样带出来（`reason` 里已经写清了是"这场库里没有"还是"计算节点没跑"），
    不许兜成空表 —— 空表在手机上读起来像"今天没有一只票符合"，那是句假话。"""
    return f"{label}：没取到（{res.get('reason') or '原因未给出'}）"


def _blk_confluence(params: dict, day: str) -> str:
    from duanxian import confluence_view
    limit = _lim(params, "confluence")
    hits = int(_num(params, "min_hits", 2) or 2)
    sources = _joined(params.get("sources"))
    res = confluence_view.summary(day, min_hits=hits, sources=sources, limit=limit)
    if not res.get("available"):
        return _avail(res, "策略共振榜")
    rows = res.get("rows") or []
    #: `hit_sources` 交回来是 `['dgt','quadrant']` 这种键名 —— 手机上看到这串等于没看。
    #: 同一份响应里的 `sources` 元数据带 label（与页面上那六个榜同名），这里只翻标签，
    #: 认不出的键**原样带出来**而不是丢掉：宁可丑，不能少一只票的一个源。
    labels = {str(s.get("key")): str(s.get("label") or s.get("key"))
              for s in (res.get("sources") or []) if isinstance(s, dict)}

    def human(source_list) -> str:
        if isinstance(source_list, str):
            source_list = [x for x in source_list.replace("[", "").replace("]", "").split(",") if x.strip()]
        names = [labels.get(str(x).strip().strip("'\""), str(x).strip().strip("'\""))
                 for x in (source_list or [])]
        return "、".join(names) if names else "—"

    picked_names = "、".join(labels.get(str(s).strip(), str(s).strip())
                             for s in sources.split(",") if str(s).strip()) if sources else ""
    lines = [f"策略共振榜（{res.get('date') or day}，≥{hits} 个策略同日选中"
             + (f"，且必须有 {picked_names} 之一选中" if picked_names else "")
             + f"；当日共振 {res.get('day_repeat_total')} / 全市场 {res.get('day_total')}）："]
    if not rows:
        lines.append("  今天没有一只票被两个以上策略同时选中（不是取数失败，是真没有）。")
    for r in rows[:limit]:
        lines.append(f"  {r.get('name') or r.get('code')}（{r.get('code')}）"
                     f"命中 {r.get('hits')}：{human(r.get('hit_sources'))}"
                     + (f"｜最好名次 {r.get('best_rank')}@{labels.get(r.get('best_source'), r.get('best_source'))}"
                        if r.get("best_rank") not in (None, "") else ""))
    return "\n".join(lines)


def _blk_screen(params: dict, day: str) -> str:
    """条件筛选：一条规则一次 `run_many`。

    为什么不一次把多条规则丢进去：`run_many` 交的是**合成后的一个结果集**
    （`{available, reason, rows}`，没有 per-rule 的分行），而推送正文要的是
    "哪条规则选出几只" —— 所以逐条跑，代价是每条一次查询，换的是能看懂的正文。
    """
    from duanxian import screen_view
    rules = params.get("rules") or []
    universe = str(params.get("universe") or "watchlist")
    if universe not in ("watchlist", "market"):
        # `screen_view` 自己会拒（"候选集只能是 watchlist / market"），这里先说人话
        return f"条件筛选：票池只能填 watchlist（自选）或 market（全市场），收到 {universe!r}"
    picked = _multi(rules)
    picked = [x for x in picked if x]
    if not picked:
        return "条件筛选：这条计划没选规则（配置校验会先拦住，走到这里说明配置被手改过）"
    limit = _lim(params, "screen")
    lines = [f"条件筛选（{day}，票池 {'自选池' if universe == 'watchlist' else '全市场'}）："]
    for one in rules:
        key = str(one.get("rule_key") if isinstance(one, dict) else one).strip()
        if not key:
            continue
        query = (one.get("params") or {}) if isinstance(one, dict) else {}
        res = screen_view.run_many([{"rule_key": key, "params": query}],
                                   universe=universe, date=day, limit=limit, single=key)
        if not res.get("available"):
            lines.append(f"  {key}：没跑成（{res.get('reason') or '原因未给出'}）")
            continue
        rows = res.get("rows") or []
        # 筛选器的行是**中文列名**（`代码/名称/收盘/涨跌幅/操作分/象限`，与页面上那份表同一形状）：
        # 这里按中文键取，认不出就退回任意一个含"代码"的键 —— 换列名时至少不会全打成 None。
        names = [str(r.get("名称") or r.get("代码") or "") for r in rows[:8]]
        names = [x for x in names if x]
        lines.append(f"  {key}：{len(rows)} 只" + (f" → {'、'.join(names)}" if names else "（零只）"))
    return "\n".join(lines)


def _blk_watchlist(params: dict, day: str) -> str:
    from duanxian import watchlist as wl
    gid = params.get("group_id")
    gid = int(gid) if str(gid or "").strip().isdigit() else None
    limit = _lim(params, "watchlist")
    pct_min = _num(params, "pct_min")
    # 设了涨幅门槛时**先把整组读全再筛**：只读前 `limit` 行然后筛，"筛完一只不剩"很可能只是
    # "后面还没读到的那几只里才有" —— 与 10-13 记过的"筛了个寂寞"同一个形状（那一档的
    # 结论是筛选要在截断之前做）。列几条仍然按面板上的 `limit`。
    read_limit = MAX_LIMITS["watchlist"] if pct_min is not None else limit
    res = wl.watchlist(group_id=gid, date=day, limit=read_limit)
    if not res.get("available"):
        return _avail(res, "自选股当日")
    rows = res.get("rows") or []
    label = (rows[0].get("group_name") if rows else None) or (f"分组 #{gid}" if gid else "全部分组")
    kept = rows if pct_min is None else [r for r in rows
                                         if isinstance(r.get("pct_change"), (int, float))
                                         and r["pct_change"] >= pct_min]
    # 收窄是对**已经读出来的那几行**做的，所以两句都要说：筛完几只、这一组本来几只。
    # 只报前者就会被读成"今天只有 3 只有行情"，而那明明是个筛子。
    lines = [f"自选股当日（{label}，{res.get('date') or day}）共 {res.get('count', len(rows))} 只"
             + (f"，涨幅≥{pct_min}% 的 {len(kept)} 只" if pct_min is not None else "")
             + (f"（只对已读到的 {len(rows)} 只做筛，其余看网站）"
                if pct_min is not None and len(rows) < int(res.get("count") or len(rows)) else "")
             + "："]
    if not rows:
        lines.append("  这个分组里目前没有票。")
    elif not kept:
        lines.append(f"  没有一只涨幅到 {pct_min}%（不是取数失败，是真没有）。")
    for r in kept[:limit]:
        tail = []
        if r.get("oper") not in (None, ""):
            tail.append(f"操作分 {r['oper']}")
        if r.get("quadrant"):
            tail.append(f"象限 {r['quadrant']}")
        if r.get("cost") not in (None, "", 0):
            tail.append(f"成本 {r['cost']}")
        lines.append(f"  {r.get('name') or r.get('security_code')}（{r.get('security_code')}）"
                     f"现价 {r.get('close') if r.get('close') is not None else '—'} "
                     f"{_pct(r.get('pct_change'))}"
                     + ("｜" + "｜".join(map(str, tail)) if tail else ""))
    return "\n".join(lines)


def _blk_dossier(params: dict, day: str) -> str:
    """自选/重点个股的「内容尽量全」那一档：**直接用页面上那份档案**（同一个 dossier）。

    一块取不到只让那一行说"没取到"，整条消息照发 —— 与 `stock_dossier` 本身一个取舍。
    `blocks`（10-17 加）是"这只票那行想带几块"：默认六块全带，嫌长就在面板上少勾两块 ——
    超上限被截掉的那一段是**看不见**的，而少勾是看得见的。
    """
    from duanxian import stock_dossier
    codes = _multi(params.get("codes"))
    limit = _lim(params, "dossier")
    blocks = _multi(params.get("blocks")) or ["oper", "volume_double", "breakout",
                                              "ambush", "confluence", "scores"]
    if not codes:
        # 没点票就发我自己在盯的那几只：这条计划的本意就是"每天看看自选的重点"
        from duanxian import watchlist as wl
        res = wl.watchlist(date=day, limit=limit)
        codes = [str(r.get("security_code")) for r in (res.get("rows") or [])][:limit]
        if not codes:
            return "个股档案：既没指定个股、自选里也没有票可读"
    lines = [f"个股档案（{day}，{len(codes[:limit])} 只，"
             f"每只带 {'/'.join(str(DOSSIER_BLOCK_LABELS.get(b) or b) for b in blocks)}）："]
    for code in codes[:limit]:
        d = stock_dossier.dossier(code, day)
        if not d.get("available"):
            lines.append(f"  {code}：整份没取到（{d.get('reason') or '原因未给出'}）")
            continue
        blk = d.get("blocks") or {}
        parts = []
        if "oper" in blocks:
            q = (blk.get("quadrant") or {}).get("oper") or {}
            if q.get("value") is not None:
                parts.append(f"操作分 {q['value']}"
                             + (f"·{q.get('quadrant_label') or q.get('quadrant')}" if q.get("quadrant") else ""))
        if "volume_double" in blocks:
            vd = blk.get("volume_double") or {}
            if (vd.get("latest") or {}).get("dbl_energy") is not None:
                parts.append(f"近40场加权分 {vd['latest']['dbl_energy']}")
        if "breakout" in blocks:
            bo = blk.get("breakout") or {}
            latest_bo = bo.get("latest") or {}
            if latest_bo.get("signal_type"):
                labels = bo.get("type_labels") or {}
                parts.append(f"突破 {labels.get(latest_bo['signal_type'], latest_bo['signal_type'])}")
        if "ambush" in blocks:
            am = (blk.get("ambush") or {}).get("latest") or {}
            if am.get("weekly_streak") not in (None, "", 0):
                parts.append(f"埋伏 连{am['weekly_streak']}周")
        if "confluence" in blocks:
            cf = blk.get("confluence") or {}
            if cf.get("hits"):
                parts.append(f"共振 {cf['hits']} 源")
        if "scores" in blocks:
            caps = (blk.get("scores") or {}).get("capabilities") or []
            #: 分项的数值字段叫 `value`（不是 score）；`is_stale` 为真时那一格是旧数，
            #: 标出来而不是当现值用 —— 手机上看到一个旧分数比看不到更糟。
            bits = []
            for c in caps[:4]:
                if not isinstance(c, dict) or c.get("value") is None:
                    continue
                bits.append(f"{c.get('capability_label') or c.get('capability_key')}={c['value']}"
                            + ("(旧)" if c.get("is_stale") else ""))
            if bits:
                parts.append("分项 " + "/".join(bits))
        lines.append(f"  {d.get('name') or code}（{code}）现价 {(d.get('quote') or {}).get('close')}"
                     f" {_pct((d.get('quote') or {}).get('pct_change'))}"
                     + ("｜" + "｜".join(parts) if parts else "（这一档勾的块都没数）"))
    return "\n".join(lines)


def _blk_positions(params: dict, day: str) -> str:
    from duanxian import positions
    res = positions.report()
    if not res.get("available"):
        return _avail(res, "持仓")
    hold = res.get("holdings") or []
    limit = _lim(params, "positions")
    only_loss = _flag(params, "only_loss")
    # 取不到行情的那些（`pnl` 是 None）在"只报亏损"时**照样列出来**：
    # 少了这一行就等于替用户断定"这只没亏"，而那正是没法从这份数据里说出口的话。
    listed = ([h for h in hold if not isinstance(h.get("pnl"), (int, float)) or h["pnl"] < 0]
              if only_loss else hold)
    quoted = sum(1 for h in hold if h.get("quote_ok") is not False)
    lines = [f"持仓（{len(hold)} 只，{day}"
             + (f"，其中亏损的 {len(listed)} 只" if only_loss else "") + "）："]
    if not hold:
        lines.append("  " + (str(res.get("note") or "当前没有未平仓持仓").strip()))
        return "\n".join(lines)
    if not listed:
        lines.append(f"  没有一只在亏（{len(hold)} 只里有行情的 {quoted} 只）。")
        return "\n".join(lines)
    for h in listed[:limit]:
        if h.get("quote_ok") is False:
            lines.append(f"  {h.get('name') or h.get('code')}：行情没取到 —— 浮动盈亏不算 0")
            continue
        lines.append(f"  {h.get('name') or h.get('code')}（{h.get('code')}）"
                     f"成本 {h.get('cost')}｜现价 {h.get('price')}"
                     f"｜浮动 {h.get('pnl')}（{_pct(h.get('pnl_pct'))}）")
    return "\n".join(lines)


def _blk_market(params: dict, day: str) -> str:
    from duanxian import qd
    #: 连板天梯列几层（以前写死 4 层，界面上调不动 —— 用户要的是"按各个点配置"）
    tiers_keep = _lim(params, "market")
    lines = [f"市场概览（{day}）："]
    s = qd.sentiment_cycle(day)
    inner = s.get("qd") or {}
    if s.get("available") and inner:
        lines.append(f"  情绪周期：{inner.get('stage_ui') or inner.get('stage_mcp') or '—'}"
                     f"（口径 {s.get('scope')}，票池 {s.get('universe')}）")
        first = (inner.get("stage_reasons") or [{}])[0]
        if first.get("reason"):
            lines.append(f"    依据：{str(first['reason'])[:160]}")
    else:
        lines.append(f"  情绪周期：没取到（{s.get('reason') or '这一场库里没有'}）")
    b = qd.board_ladder(day)
    if b.get("available"):
        tiers = [t for t in (b.get("tiers") or []) if (t.get("stock_count") or 0) > 0][:tiers_keep]
        lines.append("  连板天梯：" + ("、".join(f"{t.get('tier_label')} {t.get('stock_count')}只"
                                              for t in tiers) or "今天没有封上板的层级"))
        leader = b.get("leader") or {}
        if leader.get("leader_stock_name"):
            lines.append(f"  最高板：{leader.get('leader_stock_name')}（{leader.get('leader_board_count')} 板，"
                         f"同高度 {leader.get('leader_count')} 只）")
    else:
        lines.append(f"  连板天梯：没取到（{b.get('reason') or '这一场库里没有'}）")
    return "\n".join(lines)


def _blk_voldbl(params: dict, day: str) -> str:
    from duanxian import quant
    limit = _lim(params, "voldbl")
    # 2026-10-19：倍量榜的主列从硬计数换成加权分，参数跟着改名 `min_count` → `min_energy`。
    # 旧计划里存的 `min_count` 不再被读（等价于"不设门槛"），推送那侧不猜、不折算 ——
    # 折算等于替用户改判据；改了要他在界面上重新填一次。
    min_energy = float(_num(params, "min_energy", 0) or 0)
    quadrants = _multi(params.get("quadrants"))
    res = quant.volume_double(date=day, limit=limit, min_energy=min_energy,
                              quadrants=",".join(quadrants))
    if not res.get("available"):
        return _avail(res, "倍量形态榜")
    rows = res.get("rows") or []
    labels = {str(o.get("value")): str(o.get("label")) for o in (res.get("quadrant_options") or [])}
    counts = res.get("quadrant_counts") or {}
    picked = "、".join(labels.get(q, q) for q in quadrants)
    # "筛完只剩 3 只"不能读成"全场只有 3 只"。读层那份 `quadrant_counts` 的口径是
    # **带加权分门槛、不带象限条件**的每档实有几只（那句 SQL 就 `dbl_energy >= min_energy`），
    # 所以这里要说"加权分≥N 的里面这几档各有几只"，说成"全场"就是另一句像样的假话。
    gate = f"加权分≥{min_energy:g}"
    if len(quadrants) == 1:
        filter_note = (f"，只看「{picked}」象限"
                       f"（{gate} 的票里这一档 {counts.get(quadrants[0], 0) if counts else '没读到'} 只）")
    else:
        whole = "／".join(f"{labels.get(q, q)} {counts.get(q, 0)} 只" for q in quadrants)
        filter_note = (f"，只看 {picked} 这几档"
                       f"（{gate} 的票里分别是 {whole}）" if counts else
                       f"，只看 {picked} 这几档（各档实有几只没读到）")
    lines = [f"倍量形态榜（{res.get('date') or day}，近 40 场{gate}"
             + (filter_note if quadrants else "，不限象限") + "："]
    if not rows:
        lines.append("  这一场没有满足条件的票（不是取数失败，是真没有）。")
    for r in rows[:limit]:
        lines.append(f"  {r.get('name') or r.get('code')}（{r.get('code')}）"
                     f"加权分 {r.get('dbl_energy')}｜贴 MA60 {'是' if r.get('near_ma60') else '否'}"
                     f"｜中位偏离 {r.get('dev_med') if r.get('dev_med') is not None else '—'}%"
                     f"｜今 {_pct(r.get('chg_today'))}｜15 场 {_pct(r.get('chg_15'))}"
                     f"｜象限 {r.get('quadrant_label') or r.get('quadrant') or '—'}")
    return "\n".join(lines)


def _blk_breakout(params: dict, day: str) -> str:
    from duanxian import quant
    limit = _lim(params, "breakout")
    st = str(params.get("signal_type") or "").strip()
    res = quant.breakout_signal(date=day, limit=limit, signal_type=st)
    if not res.get("available"):
        return _avail(res, "突破信号")
    opts = res.get("type_options") or []
    labels = {str(o.get("value")): str(o.get("label")) for o in opts}
    rejected = ((res.get("filter") or {}).get("signal_type_rejected") or [])
    tally = "、".join(f"{o.get('label')} "
                      f"{o.get('count') if o.get('count') is not None else '没读到'} 只"
                      for o in opts)
    lines = [f"突破信号（{res.get('date') or day}"
             + (f"，只要「{labels.get(st) or st}」" if st else "，三类都要")
             + f"；全场 {tally}）："]
    if rejected:
        lines.append(f"  ⚠ 认不了的类型：{'、'.join(map(str, rejected))} —— 这一档按「全部」发了。")
    rows = res.get("rows") or []
    if not rows:
        lines.append("  这一场没有信号（不是取数失败，是真没有）。")
    for r in rows[:limit]:
        lines.append(f"  {r.get('name') or r.get('code')}（{r.get('code')}）"
                     f"{r.get('signal_type_label') or r.get('signal_type')}"
                     f"｜放量 {r.get('break_vol_ratio')}×｜收敛 {r.get('converge_days')} 场"
                     f"｜缩量 {r.get('shrink_days')} 场｜上次放量 {r.get('last_surge_date') or '—'}"
                     f"｜收 {(r.get('break_close'))} / MA60 {(r.get('break_ma60'))}")
    return "\n".join(lines)


def _blk_ambush(params: dict, day: str) -> str:
    from duanxian import quant
    limit = _lim(params, "ambush")
    types = _multi(params.get("types"))
    min_ups = int(_num(params, "min_weekly_ups", 0) or 0)
    strict = _flag(params, "only_strict")
    res = quant.ambush(date=day, limit=limit, types=",".join(types),
                       only_strict=strict, min_weekly_ups=min_ups)
    if not res.get("available"):
        return _avail(res, "埋伏票榜")
    filt = res.get("filter") or {}
    rejected = filt.get("types_rejected") or []
    labels = {str(o.get("value")): str(o.get("label")) for o in (res.get("type_options") or [])}
    lines = [f"埋伏票榜（{res.get('date') or day}"
             + (f"，只要 {'、'.join(labels.get(t, t) for t in types)}" if types else "，五类都要")
             + (f"，近 6 周上涨周数≥{min_ups}" if min_ups else "")
             + ("，只算严格通过" if strict else "")
             + f"；当日 {res.get('day_total') if res.get('day_total') is not None else '—'} 只"
             f"（严格 {res.get('day_strict_total') if res.get('day_strict_total') is not None else '—'} 只））："]
    if rejected:
        lines.append(f"  ⚠ 认不了的类型：{'、'.join(map(str, rejected))} —— 这些不参与筛选。")
    rows = res.get("rows") or []
    if not rows:
        lines.append("  这一场没有符合的票（不是取数失败，是真没有）。")
    for r in rows[:limit]:
        tl = r.get("ambush_type_labels") or r.get("ambush_types") or []
        lines.append(f"  {r.get('name') or r.get('code')}（{r.get('code')}）"
                     f"{'、'.join(map(str, tl)) or '—'}｜均线极差 {r.get('ma_spread_pct')}%"
                     f"｜20 日振幅 {r.get('range20_pct')}%｜连涨 {r.get('weekly_streak')} 周"
                     f"｜分 {r.get('ambush_score')}" + ("｜严格✓" if r.get("strict_pass") else ""))
    return "\n".join(lines)


def _blk_quadrant(params: dict, day: str) -> str:
    from duanxian import quant
    limit = _lim(params, "quadrant")
    min_x = _num(params, "min_x")
    min_y = _num(params, "min_y")
    res = quant.quadrant(date=day, min_x=min_x, min_y=min_y, limit=limit)
    if not res.get("available"):
        return _avail(res, "四维象限榜")
    rows = res.get("rows") or []
    dist = res.get("distribution") or {}
    # 「筛出几只」这句话在这个接口里**说不出来**（读层只交"按操作分取前 N"，没有过滤后的总数），
    # 所以只能说取了前几名，并把**未过滤**的全场四档分布一起带出来 —— 把后者当前者就会说成
    # "全市场只剩 4 只 Q1"（本仓 10-13 记过的"筛了个寂寞"）。
    lines = [f"四维象限榜（{res.get('date') or day}"
             + (f"，X≥{min_x}" if min_x is not None else "")
             + (f"，Y≥{min_y}" if min_y is not None else "") + "）："
             + f"按操作分取前 {len(rows[:limit])} 只"
             + ("｜全场 " + "／".join(f"{q} {v.get('count')} 只"
                                      for q, v in sorted(dist.items()) if isinstance(v, dict))
                if dist else "")]
    if not rows:
        lines.append("  前几名里没有一只过这两道门槛（不代表全场没有 —— 门槛低一档再试，或看网站）。")
    for r in rows[:limit]:
        lines.append(f"  {r.get('name') or r.get('code')}（{r.get('code')}）"
                     f"{r.get('quadrant') or '—'} {(r.get('quadrant_label') or '')}"
                     f"｜操作分 {r.get('oper_value')}｜全场第 {r.get('rank_in_market')} 名"
                     f"｜X {r.get('x_quality')} / Y {r.get('y_env')}"
                     + (f"｜走势 {r.get('quadrant_trend')}" if r.get("quadrant_trend") else "")
                     + ("（旧数）" if r.get("is_stale") else ""))
    return "\n".join(lines)


def _blk_abnormal(params: dict, day: str) -> str:
    from duanxian import abnormal_view as av
    limit = _lim(params, "abnormal")
    recent = int(_num(params, "recent", 10) or 10)
    hot_limit = int(_num(params, "hot_limit", 20) or 20)
    res = av.screen(date=day, recent=recent, hot_limit=hot_limit, limit=limit)
    if not res.get("available"):
        return _avail(res, "异动筛查")
    s = res.get("summary") or {}
    lines = [f"异动筛查（{res.get('date') or day}，母体 {s.get('universe')} 只："
             f"严重异动 {s.get('severe')} 只（下跌方向 {s.get('severe_down')} 只）、"
             f"明日可能触及严重阈值 {s.get('forward')} 只"
             + (f"、{s.get('severe_unknown')} 只判不准" if s.get("severe_unknown") else "") + "）"]

    def cell(row) -> str:
        return (f"  {row.get('name') or row.get('security_code')}（{row.get('security_code')}）"
                f"{_pct(row.get('pct_change'))}｜10 日偏离 {row.get('dev_10')}%"
                f"｜30 日偏离 {row.get('dev_30')}%"
                + (f"｜{row.get('severe_how')}" if row.get("severe_how") else ""))

    hot = ((res.get("hot") or {}).get("rows") or [])[:limit]
    lines.append(f"  偏离值榜（前 {len(hot)} 只）：")
    for r in hot:
        lines.append(cell(r))
    if not hot:
        lines.append("    （这一场没有进入偏离值榜的票）")
    fwd = ((res.get("forward") or {}).get("rows") or [])[:limit]
    lines.append(f"  前瞻：再涨就到严重异动阈值的（{len(fwd)} 只）：")
    for r in fwd:
        lines.append(f"    {r.get('name') or r.get('security_code')}（{r.get('security_code')}）"
                     f"明日涨停价 {r.get('limit_price_next')}｜还差 {r.get('need_dev_to_100')}% 到 100%"
                     f"｜今日 {_pct(r.get('pct_change'))}")
    if not fwd:
        lines.append("    （没有一只处在触价边缘）")
    rec = res.get("recent_severe") or {}
    rrows = (rec.get("rows") or [])[:limit]
    lines.append(f"  近 {rec.get('sessions')} 场累计严重异动 "
                 + (f"{rec.get('total')} 只" if rec.get("total") is not None else "总数没读到")
                 + f"，这里列最近 {len(rrows)} 条"
                 + ("（还没列完）" if rec.get("truncated") or rec.get("total_unread") else "") + "：")
    for r in rrows:
        lines.append(f"    {r.get('trade_date')}｜{r.get('name') or r.get('security_code')}"
                     f"｜{'上涨' if r.get('severe_dir') == 'up' else '下跌'}"
                     f"｜10 日偏离 {r.get('dev_10')}%")
    return "\n".join(lines)


def _blk_spike(params: dict, day: str) -> str:
    from duanxian import spike_view as sv
    limit = _lim(params, "spike")
    tags = _multi(params.get("tags"))
    min_hi = _num(params, "min_hi")
    res = sv.screen(date=day, tags=",".join(tags), min_hi=min_hi, limit=limit)
    if not res.get("available"):
        return _avail(res, "盘中冲高异动")
    if res.get("rejected_tags"):
        return (f"盘中冲高异动：认不了的形态 {'、'.join(map(str, res['rejected_tags']))}"
                "（这一档没发出去，改一下面板上的勾选）")
    lines = [f"盘中冲高异动（{res.get('date') or day}"
             + (f"，形态 {'、'.join(tags)}" if tags else "")
             + (f"，冲高≥{min_hi}%" if min_hi is not None else "")
             + f"：共 {res.get('total')} 只｜触板 {res.get('sealed')} 只、"
               f"炸板 {res.get('blown')} 只"
             + (f"｜{res.get('minute_missing')} 只没分钟线" if res.get("minute_missing") else "") + "）"]
    rows = res.get("rows") or []
    if not rows:
        lines.append("  这一场没有符合的冲高（不是取数失败，是真没有）。")
    for r in rows[:limit]:
        tl = r.get("tag_labels") or []
        lines.append(f"  {r.get('name') or r.get('code')}（{r.get('code')}）"
                     f"最高 {_pct(r.get('hi_pct'))}｜收盘 {_pct(r.get('cl_pct'))}"
                     f"｜冲高回落 {_pct(r.get('fade_pct'))}｜15 分钟速度 {_pct(r.get('speed15_pct'))}"
                     + (f"｜{'、'.join(map(str, tl))}" if tl else ""))
    return "\n".join(lines)


def _blk_trendstock(params: dict, day: str) -> str:
    from duanxian import trend_view as tv
    limit = _lim(params, "trendstock")
    lookback = int(_num(params, "lookback", 5) or 5)
    res = tv.screen(date=day, lookback=lookback, limit=limit)
    if not res.get("available"):
        return _avail(res, "趋势股榜")
    rows = res.get("rows") or []
    lines = [f"趋势股榜（{res.get('date') or day}，连续 {lookback} 天站上 5 日线且今日未封板："
             f"入选 {res.get('selected_total')} 只，母体 {res.get('universe_rows')} 只）："]
    if not rows:
        lines.append("  这一场没有符合的票（不是取数失败，是真没有）。")
    for r in rows[:limit]:
        sectors = "、".join(str(x.get("name")) for x in (r.get("sectors") or [])[:2])
        lines.append(f"  {r.get('name') or r.get('security_code')}（{r.get('security_code')}）"
                     f"连站 {r.get('ma5_streak')} 天｜今 {_pct(r.get('pct_change'))}"
                     f"｜成交 {r.get('amount_yi')} 亿｜流通市值 {r.get('float_mv_yi')} 亿"
                     + (f"｜板块 {sectors}" if sectors else ""))
    return "\n".join(lines)


def _blk_sector(params: dict, day: str) -> str:
    from duanxian import qd
    limit = _lim(params, "sector")
    days = max(1, int(_num(params, "days", 1) or 1))
    res = qd.sector_cycle(day)
    if not res.get("available"):
        return _avail(res, "板块轮动")
    rotation = (res.get("rotation") or [])[:days]
    lines = [f"板块轮动（{res.get('date') or day}，票池 {_cnt(res.get('universe'))} 个板块、"
             f"往回 {len(rotation)} 日，每日列前 {limit} 个 —— 这一份源每天只留涨幅前 8）："]
    for one in rotation:
        items = (one.get("items") or [])[:limit]
        head = f"  {one.get('trade_date')}：" + "、".join(
            f"{it.get('sector_name')} {_pct(it.get('pct_change'))}"
            f"（涨 {it.get('up_count')}/跌 {it.get('down_count')}）" for it in items)
        lines.append(head or f"  {one.get('trade_date')}：没有记录")
    p = res.get("persistence") or {}
    if p.get("current_leader_name"):
        lines.append(f"  当前领涨：{p.get('current_leader_name')} 已连续 {p.get('streak_days')} 日"
                     f"｜近 {p.get('window_days')} 日最强重复：{p.get('strongest_repeat_name') or '—'}"
                     f"（前三重复 {p.get('top_three_appearances')} 次）")
    return "\n".join(lines)


def _blk_groupanalytics(params: dict, day: str) -> str:
    from duanxian import watchlist as wl
    limit = _lim(params, "groupanalytics")
    window = int(_num(params, "window", 10) or 10)
    res = wl.group_analytics(date=day, window=window)
    if not res.get("available"):
        return _avail(res, "自选分组分析")
    rows = res.get("rows") or []
    if _flag(params, "only_focus"):
        rows = [r for r in rows if r.get("is_focus")]
    # 排在这里做、但排的是**全组**（这份读层一次交出所有分组，没有截断），
    # 所以"前几名"是真的前几名 —— 与那些"先截 150 行再在页面里筛"的榜不是一回事。
    rows = sorted(rows, key=lambda r: (r.get("trend_score") is None,
                                       -(r.get("trend_score") or 0)))
    lines = [f"自选分组分析（{res.get('date') or day}，近 {window} 场，"
             f"{'重点分组' if _flag(params, 'only_focus') else '全部分组'} 共 {len(rows)} 组，"
             f"按分组趋势分排）："]
    if not rows:
        lines.append("  没有分组可列（没标重点，或者分组清单没读到）。")
    for r in rows[:limit]:
        lines.append(f"  {r.get('name')}（{r.get('member_count')} 只）"
                     f"趋势 {r.get('trend_score')}｜今日等权 {_pct(r.get('avg_pct'))}"
                     f"｜上涨 {r.get('up_count')}/{r.get('quoted_count')} 只"
                     f"｜加权分 {r.get('w_score')}" + ("｜重点" if r.get("is_focus") else ""))
    return "\n".join(lines)


def _blk_news(params: dict, day: str) -> str:
    from duanxian import qd
    limit = _lim(params, "news")
    keyword = str(params.get("keyword") or "").strip()
    res = qd.news(day, limit=max(limit, min(120, MAX_LIMITS["news"] * 4)))
    items = res.get("items") or []
    if not res.get("available"):
        return _avail(res, "快讯要闻")
    picked = [x for x in items if keyword and keyword in str(x.get("title") or "")] if keyword else items
    lines = [f"快讯要闻（{res.get('date') or day}，当天共 {res.get('count')} 条，"
             f"这里看最近 {len(items)} 条"
             + (f"，标题含「{keyword}」的 {len(picked)} 条" if keyword else "") + "）："]
    if not picked:
        lines.append("  " + (f"最近 {len(items)} 条里没有标题含「{keyword}」的"
                           "（不代表当天没有 —— 换一个关键词或看网站）"
                           if keyword else "今天一条都没采到（不是筛选，是采集那侧没数）"))
    for x in picked[:limit]:
        stamp = str(x.get("published_at") or "")[11:16]
        title = str(x.get("title") or "").strip().replace("\n", " ")
        lines.append(f"  {stamp or '—'}｜{title[:60]}"
                     + (f"（{x.get('source')}）" if x.get("source") else ""))
    return "\n".join(lines)


def _blk_allocator(params: dict, day: str) -> str:
    from duanxian import allocator_view
    limit = _lim(params, "allocator")
    parts = _multi(params.get("parts")) or ["toll", "crowding", "stages"]
    res = allocator_view.view(date=day, limit=limit)
    if not res.get("available"):
        return _avail(res, "分配者视角")
    names = {"toll": "收费站榜", "crowding": "拥挤度榜", "stages": "阶段分布"}
    lines = [f"分配者视角（{res.get('date') or day}，票池 {_cnt(res.get('universe'))} 个板块，"
             f"{'、'.join(names.get(p, p) for p in parts)}）："]
    if "toll" in parts:
        for r in (res.get("rows") or [])[:limit]:
            lines.append(f"  [收费] {r.get('sector_name')}（{r.get('sector_type')}）"
                         f"分 {r.get('toll_score')}｜温度 {r.get('crowding_temp')}"
                         f"｜份额分位 {r.get('share_pctile')}｜{r.get('stage_label') or '—'}")
    if "crowding" in parts:
        for r in (res.get("crowding") or [])[:limit]:
            lines.append(f"  [拥挤] {r.get('sector_name')} 温度 {r.get('crowding_temp')}"
                         f"｜60 场动量 {_pct(r.get('mom60'))}｜{r.get('stage_label') or '—'}")
    if "stages" in parts:
        stages = "、".join(f"{s.get('stage_label') or s.get('stage') or '没判档'} {int(s.get('n') or 0)} 个"
                           for s in (res.get("stages") or []))
        lines.append("  [阶段] " + (stages or "没读到"))
    return "\n".join(lines)


#: 场景 → 取数函数。**这里是唯一的对应处**：`notifyconfig.SCENE_KEYS` 与它必须同集合，
#: 由测试钉住（否则会长出"面板能勾、发出来是空的"那种假功能）。
SCENE_BUILDERS = {"confluence": _blk_confluence, "screen": _blk_screen,
                  "watchlist": _blk_watchlist, "dossier": _blk_dossier,
                  "positions": _blk_positions, "market": _blk_market,
                  "voldbl": _blk_voldbl, "breakout": _blk_breakout, "ambush": _blk_ambush,
                  "quadrant": _blk_quadrant, "abnormal": _blk_abnormal, "spike": _blk_spike,
                  "trendstock": _blk_trendstock, "sector": _blk_sector,
                  "groupanalytics": _blk_groupanalytics, "news": _blk_news,
                  "allocator": _blk_allocator}


def _scene_part(key: str, params: dict, day: str) -> tuple[str, str]:
    """一块内容自己那条文字。返回 `(文字, 状态)`；取不到**写成一行说明取不到**，不抛。"""
    label = SCENE_LABELS.get(key, key)
    builder = SCENE_BUILDERS.get(key)
    if builder is None:
        return f"{label}：本程没有这一档的执行体（配置里有未知项）", "unknown"
    try:
        return builder(params, day), "ok"
    except Exception as exc:  # noqa: BLE001  取数坏了要说出来，但不许带崩整条消息
        return f"{label}：这一档取失败（{type(exc).__name__}: {str(exc)[:90]}）", f"error:{type(exc).__name__}"


def _clip(title: str, body: str, cap: int, *,
          note: str = "\n…（这一条过长已截断，完整请看网站）") -> tuple[str, bool, int]:
    """按**整条消息**（`标题\n正文`，机器人那边是拼在一起送的）切到上限，切了就声明。
    合并与分条两条路共用这一把尺 —— 两把尺就会出现"预览看着完整、实发少一段"那种假话。
    ⚠️ 上限管的是整条而不是正文：只切正文会把标题那几十字节漏在外头
    （企微 2048 那条硬墙实测切到 1900 仍发出去 1927 被拒）。"""
    head = f"{title}\n" if title else ""
    head_bytes = len(head.encode("utf-8"))
    truncated = False
    if head_bytes + len(body.encode("utf-8")) > cap:
        # 声明那一句也占字节：不先扣掉就会切完再加注，结果还是超上限
        room = max(0, cap - head_bytes - len(note.encode("utf-8")))
        body = body.encode("utf-8")[:room].decode("utf-8", "ignore") + note
        truncated = True
    return body, truncated, len((head + body).encode("utf-8"))


def compose_split(scenes: dict, date: str = "", *, title_prefix: str = "",
                  limit: int = 0) -> list[dict]:
    """**分条推送**（用户 2026-10-19：「具体的内容，按照对应的点，分条推送。不要合并推送。」）。

    一档内容 = 一条消息，标题带那一档的名字（手机上看一眼标题就知道是哪块）。
    与 `compose` 共用 `_scene_part` 与 `_clip`，所以两条路的**取数、失败措辞、截断声明**完全一致，
    不存在"合并发得出去、分条发出去是另一个样子"。
    每档各发一条也各占一份渠道上限：企微 2048 那条硬墙在**每一条**上都单独判。
    """
    cap = int(limit or 0) or MAX_BODY_BYTES
    day = _session_date(date)
    out = []
    for key in (scenes or {}):
        label = SCENE_LABELS.get(key, key)
        params = scenes[key] if isinstance(scenes[key], dict) else {}
        text, status = _scene_part(key, params, day)
        title = f"{title_prefix + '·' if title_prefix else ''}{day or '今日'} · {label}"
        body, truncated, wire = _clip(title, text, cap)
        out.append({"scene": key, "label": label, "title": title, "body": body,
                    "status": status, "truncated": truncated, "wire_bytes": wire,
                    "body_limit": cap})
    return out


def compose(scenes: dict, date: str = "", *, title_prefix: str = "",
            limit: int = 0) -> tuple[str, str, dict]:
    """把选中的几档内容拼成一条消息。返回 `(标题, 正文, meta)`。

    每个场景各自 try：**一块取不到只让那一行写"没取到"**，整条照发
    （与 `stock_dossier` 同一个取舍）。全挂才叫失败，meta 里会带每块的状况。

    `limit` = 这批目标渠道里最小的那个正文上限（`body_limit`）；0 = 用兜底值。
    截断必须在**正文里声明**（`meta.truncated` 也记一笔），不能悄悄切掉。
    """
    cap = int(limit or 0) or MAX_BODY_BYTES
    day = _session_date(date)
    parts, meta = [], {}
    for key in (scenes or {}):
        label = SCENE_LABELS.get(key, key)
        params = scenes[key] if isinstance(scenes[key], dict) else {}
        text, status = _scene_part(key, params, day)
        parts.append(text)
        meta[key] = status
    title = f"{title_prefix + '·' if title_prefix else ''}{day or '今日'}推送"
    body = "\n\n".join(parts) if parts else "（这条计划没有勾选任何内容）"
    body, truncated, wire_bytes = _clip(title, body, cap,
                                        note="\n…（内容过长已截断，完整请看网站）")
    if truncated:
        meta["truncated"] = True     # 没截断就**没有这一格**（旧契约：读侧用 `.get("truncated") is None` 判"没切过"）
    meta["body_bytes"] = len(body.encode("utf-8"))
    meta["wire_bytes"] = wire_bytes
    meta["body_limit"] = cap
    return title, body, {"date": day, "limit": cap, "wire_bytes": wire_bytes, "scenes": meta}


def _static_choices() -> dict:
    """`choices_from` 里那些**不查库**的候选清单（判据表自带的标签/档位）。
    读不到就交空，让对应那一格留空 —— 面板上宁可得不了一张下拉，也不要凭空编几个选项。
    """
    from app.compute import ambush_setup, breakout_signal
    from duanxian import abnormal_view, quant, spike_view, trend_view
    labels, _ = quant._labels()
    return {
        "dossier_blocks": [{"value": k, "label": v} for k, v in DOSSIER_BLOCK_LABELS.items()],
        "quadrant_options": [{"value": c, "label": (labels or {}).get(c) or c}
                             for c in quant.QUADRANT_CODES],
        "breakout_types": [{"value": k, "label": v}
                           for k, v in breakout_signal.TYPE_LABELS.items()],
        "ambush_types": [{"value": k, "label": v} for k, v in ambush_setup.TYPE_LABELS.items()],
        "spike_tags": [{"value": k, "label": v} for k, v in spike_view.TAG_LABELS.items()],
        "abnormal_recent_days": [{"value": n, "label": f"往回 {n} 场"} for n in abnormal_view.RECENT_DAYS],
        "trend_lookbacks": [{"value": n, "label": f"连续 {n} 天"} for n in trend_view.LOOKBACKS],
    }


def _fields_for(scene: str, pools: dict, reasons: dict) -> list:
    """把 `notifyconfig.SCENE_FIELDS` 里那几行描述符换成面板能直接渲染的形状：
    候选就地解出来（`choices_from` → 同一张目录里已经读好的那份，不二次查库），
    条数格的上下限/默认/单位从 `{DEFAULT,MAX}_LIMITS` + `SCENE_LIMIT_UNITS` 填进去 ——
    **界面上一格都不抄**。加一档参数 = 后端描述符加一行，界面自动多一个框。
    """
    out = []
    for fd in NC.SCENE_FIELDS.get(scene, ()):
        one = dict(fd)
        if one.get("choices_from"):
            key = one["choices_from"]
            one["choices"] = pools.get(key) or []
            if not one["choices"]:
                reasons.setdefault(key, f"{key} 这份候选没读到 —— 那一格只能留空（＝用后端默认）")
        if one.get("key") == "limit":
            one.update({"type": "int", "min": 1, "max": MAX_LIMITS.get(scene),
                        "default": DEFAULT_LIMITS.get(scene),
                        "unit": SCENE_LIMIT_UNITS.get(scene, "条")})
        if scene == "confluence" and one.get("key") == "min_hits":
            live = len(pools.get("confluence_sources") or [])
            if live:
                one["max"] = live      # 门槛的上限就是"在册策略数"，那一格现读，不写死 6
        out.append(one)
    return out


def scene_catalog(date: str = "") -> dict:
    """面板上那些下拉/勾选的**候选项从哪来**：全部现读，一个都不写死。

    为什么必须现读而不是前端列一份常量：规则会被禁用、分组会被删、共振源会加第六个 ——
    写死的那份清单一旦与库里不同，用户勾的就是个不存在的东西，
    而 apprise 会照发一条空正文，那种"看起来成功了"最难查。
    任何一块读不出来就交空数组 + `reason`，**不交假候选**。

    10-17 起每一档还带一格 `fields`（**这一档能配的那些参数**，描述符住在
    `notifyconfig.SCENE_FIELDS`）：所以"内容再多几档、每一档再具体一点"只动后端
    `SCENE_BUILDERS` + 那张描述符表，界面上的勾选项、参数框、摘要、校验自动跟上。
    """
    out: dict = {"date": _session_date(date), "scenes": [], "reasons": {}}
    pools: dict = {}
    try:
        from duanxian import confluence_view
        rows = [{"key": s, "label": lab} for s, lab in confluence_view.SOURCE_LABELS.items()]
        out["confluence_sources"] = rows
        out["confluence_max_hits"] = len(confluence_view.SOURCE_KEYS)
        # 描述符里的候选要的是 `{value,label}` 那一形状（共振源这份历史上就叫 key/label，
        # 保留原键给老调用点，这里另解一份给 `choices_from` 用）
        pools["confluence_sources"] = [{"value": r["key"], "label": r["label"]} for r in rows]
    except Exception as exc:  # noqa: BLE001
        out["confluence_sources"], out["confluence_max_hits"] = [], None
        out["reasons"]["confluence"] = f"共振源清单没读到（{type(exc).__name__}）"
    try:
        from duanxian import screen_view
        rv = screen_view.rules_view()
        rows = [{"rule_key": r.get("rule_key"), "name": r.get("name"),
                 "enabled": bool(r.get("enabled")),
                 "params": [{"name": q.get("name"), "type": q.get("type"),
                             "default": q.get("default")}
                            for q in (r.get("params") or [])[:6]]}
                for r in (rv.get("rules") or []) if r.get("enabled")]
        out["screen_rules"] = rows
        out["screen_universes"] = rv.get("universes") or ["watchlist", "market"]
        pools["screen_rules"] = [{"value": str(r.get("rule_key")),
                                  "label": r.get("name") or str(r.get("rule_key"))} for r in rows]
        if not rv.get("available"):
            out["reasons"]["screen"] = str(rv.get("reason") or "规则表没读到")
    except Exception as exc:  # noqa: BLE001
        out["screen_rules"], out["screen_universes"] = [], ["watchlist", "market"]
        out["reasons"]["screen"] = f"筛选规则清单没读到（{type(exc).__name__}）"
    try:
        from duanxian import watchlist as wl
        g = wl.groups(date)
        rows = ([{"group_id": x.get("group_id"), "name": x.get("group_name") or x.get("name"),
                  "count": x.get("stock_count") if x.get("stock_count") is not None else x.get("count")}
                 for x in (g.get("rows") or g.get("groups") or [])] if g.get("available") else [])
        out["watchlist_groups"] = rows
        # select 的 `value` 一律字符串：面板那个下拉的选中态比的是字符串
        pools["watchlist_groups"] = [{"value": str(x.get("group_id")),
                                      "label": f"{x.get('name')}（{x.get('count') if x.get('count') is not None else '—'} 只）"}
                                     for x in rows]
        if not g.get("available"):
            out["reasons"]["watchlist"] = str(g.get("reason") or "分组没读到")
    except Exception as exc:  # noqa: BLE001
        out["watchlist_groups"] = []
        out["reasons"]["watchlist"] = f"自选分组没读到（{type(exc).__name__}）"
    try:
        pools.update(_static_choices())
    except Exception as exc:  # noqa: BLE001
        out["reasons"]["labels"] = f"标签表没读到（{type(exc).__name__}: {str(exc)[:60]}）"
    for key in SCENE_BUILDERS:
        out["scenes"].append({"value": key, "label": SCENE_LABELS[key],
                              "hint": NC.SCENE_HINTS.get(key) or SCENE_LABELS[key],
                              "default_limit": DEFAULT_LIMITS.get(key),
                              # 面板那一格的上下限从这里走：加一档内容只动后端那张执行体表，
                              # 这一排就自动多出一个勾选项与一个条数框
                              "max_limit": MAX_LIMITS.get(key),
                              "limit_unit": SCENE_LIMIT_UNITS.get(key, "条"),
                              "fields": _fields_for(key, pools, out["reasons"])})
    # 分条/合并那一排的选项与措辞（面板的这一个下拉就靠它，措辞只住 `notifyconfig.SEND_MODE_MEANING`）
    out["send_mode_choices"] = [{"value": k, "label": NC.SEND_MODE_MEANING[k].split("：")[0],
                                "hint": NC.SEND_MODE_MEANING[k]} for k in NC.SEND_MODES]
    out["body_limit_bytes"] = MAX_BODY_BYTES
    # 面板要说"这一条会切到多长"，得按渠道给表（企微 1900 与飞书 4000 差一倍，
    # 只交一个统一数就会让用户在预览里看到一句假话）
    out["body_limits"] = dict(BODY_LIMITS)
    return out


# ---------------------------------------------------------------- 发送
def _apprise():
    """取 apprise 模块的**唯一口子**（默认实现就是 `import apprise`；只有邮件用它）。

    为什么要留这一层：测试原先只能把整个发送函数打桩掉，而**打桩就盖住了本体里的
    拼写错误** —— 10-16 现场：页面点「发一条测试」回 `NameError: name 'aprise' is not defined`
    （`apprise` 少写一个 p），那几行真代码因为每条用例都打桩，从写下到当天一次都没被执行过。
    有了这个口子，用例可以喂一个形状正确的假货，让本体真跑到底（见
    `tests/test_notify_config.py::test_真发送路径要用假aprise模块跑一遍本体`）。
    """
    import apprise
    return apprise


_APPISE_DEFAULT = _apprise          # 用来认出"测试有没有把这个口子换掉"


#: 一次投递最多等多久。对面不回就是失败 —— 原先交给 apprise，**我们没有超时**，
#: 一条挂住的发送会把整个 60 秒心跳拖长（14 §十记过这条风险）。
SEND_TIMEOUT_SEC = 10
# 三种群机器人的真端点前缀**只有一份**，住在 `notifyconfig.OFFICIAL_URLS`（这里不再抄一遍）。


def _post_json(url: str, payload: dict) -> tuple[int, bytes]:
    """真出站 POST 的**唯一出口**（机器人那一侧）。测试打这个口子。

    默认实现在 pytest 进程里硬挡：跑一次全量测试就给用户的工作群发一条消息，
    与 10-06 那次"pytest 在用户桌面启动通达信"是同一类越界（见《06-自查表》）。
    """
    if os.environ.get("PYTEST_CURRENT_TEST") and _post_json is _POST_DEFAULT:
        raise RuntimeError("pytest 进程里不许真发消息 —— 请 monkeypatch duanxian.notify._post_json")
    import urllib.request
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_SEC) as resp:
        return int(resp.status), (resp.read() or b"")[:400]


_POST_DEFAULT = _post_json           # 用来认出"测试有没有把这个口子换掉"


def _bot_target(kind: str, url: str, title: str, body: str,
                secret: str = "") -> tuple[str | None, dict | None, str | None]:
    """把存下来的地址换成真 HTTP 端点 + 正文载荷（带密钥时按各家口径加签）。

    **两种写法都收**（10-16 用户就是卡在这里）：群机器人设置页复制来的完整
    `https://…` 地址，或内部简写 `feishu://<token>`。端点一律由我们自己按 token 拼出来 ——
    不把用户粘的地址原样拿去发（那等于让一个输入框决定消息发往哪台主机）。

    ⚠️ 两家加签的 HMAC **取法相反**，这是最容易写错的一格：
      · 飞书（官方 `gen_sign`）：`key = "timestamp\\nsecret"`、msg 为空，timestamp **秒**、转字符串，
        `timestamp`/`sign` 写在**请求体**里；
      · 钉钉（官方）：`key = secret`、msg = `"timestamp\\nsecret"`，timestamp **毫秒**，
        `timestamp`/`sign` 拼在**URL 查询串**上。
    企微的群机器人没有加签，只有 key。
    """
    from base64 import b64encode
    from hashlib import sha256
    from hmac import new as hmac_new
    from urllib.parse import parse_qs, quote_plus, urlencode, urlsplit, urlunsplit

    prefix = NC.KINDS.get(kind, ("",))[0]
    if not prefix:
        return None, None, f"不认识这一类渠道：{kind!r}"
    official = NC.OFFICIAL_URLS.get(kind, "")
    token = ""
    query: dict[str, str] = {}
    if url.startswith(f"{prefix}://"):
        rest = url[len(prefix) + 3:]
        head, _, qs = rest.partition("?")
        token = head.strip("/").split("/")[0].split("@")[0].strip()
        query = {k: v[0] for k, v in parse_qs(qs, keep_blank_values=True).items()}
    elif official and url.startswith(official):
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        token = (parts.path.rstrip("/").rsplit("/", 1)[-1] if kind == "feishu"
                 else query.get("access_token") or query.get("key") or "")
    else:
        return None, None, (f"这一类渠道的地址要么是 {prefix}://…，要么直接粘贴群机器人那条 "
                            f"{official}…；收到的是 {url.split('://')[0]!r} 开头")
    if not token:
        return None, None, "地址里没找到 webhook 密钥段（飞书看路径最后一段，钉钉看 access_token=，企微看 key=）"
    secret = (secret or query.get("secret") or "").strip()

    text = f"{title}\n{body}" if title else body
    payload: dict = ({"msg_type": "text", "content": {"text": text}} if kind == "feishu"
                     else {"msgtype": "text", "text": {"content": text}})
    if kind == "feishu":
        target = f"{official}{quote_plus(token)}"
        if secret:
            stamp = str(int(time.time()))
            key = f"{stamp}\n{secret}".encode("utf-8")
            payload["timestamp"] = stamp
            payload["sign"] = b64encode(hmac_new(key, b"", sha256).digest()).decode()
    elif kind == "dingtalk":
        target = f"{official}?{urlencode({'access_token': token})}"
        if secret:
            stamp = str(round(time.time() * 1000))
            sign = b64encode(hmac_new(secret.encode(), f"{stamp}\n{secret}".encode(),
                                      sha256).digest()).decode()
            target += "&" + urlencode({"timestamp": stamp, "sign": sign})
    else:                                    # wecom：只有 key，没有加签
        target = f"{official}?{urlencode({'key': token})}"
    if secret and kind == "wecom":
        return None, None, "企业微信群机器人没有「加签」这一项，密钥那格请留空"
    return target, payload, None


def _verdict(kind: str, status: int, raw: bytes) -> tuple[bool, str]:
    """**这一格才是本次改造的重点**：对面回 200 不等于送达。

    飞书/钉钉/企微对无效 token、被删的机器人、关键词不匹配，全都回 **HTTP 200**
    然后把错误写在 JSON 回体里（`code` / `errcode`）。apprise 的插件不看到这一层，
    所以它会一路"已交付"到用户去群里翻那条根本不存在的消息。
    """
    if status != 200:
        return False, f"HTTP {status}"
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return False, f"回体不是 JSON（前 60 字：{raw[:60].decode('utf-8', 'replace')}）"
    if not isinstance(data, dict):
        return False, f"回体不是对象（{type(data).__name__}）"
    code = data.get("code", data.get("errcode", data.get("StatusCode", 0)))
    if code in (0, "0"):
        return True, "对面确认收到"
    msg = str(data.get("msg") or data.get("errmsg") or "").strip()
    return False, f"回体说没收到（code={code}{f'，{msg}' if msg else ''}）"


def deliver(targets: list[dict], title: str, body: str) -> dict:
    """向若干渠道投递。返回 `{"ok","detail","sent_to"}`，**逐渠道各判一次**。

    - 机器人（飞书/钉钉/企微）：自己 POST 并按回体判；
    - 邮件：交给 apprise（SMTP 握手不在这里重写）；
    - 一条坏了不影响其余，但**只要有一条没成，整体就不算成** —— "三个渠道里有一个没到"
      绝不能被写成一句"已交付"。
    """
    out = {"ok": True, "sent_to": [], "detail": "", "failed": []}
    if not targets:
        return {"ok": False, "sent_to": [], "detail": "没有可用渠道，未发送", "failed": []}
    notes = []
    for t in targets:
        kind, key, url = t.get("kind"), str(t.get("key")), t.get("url")
        try:
            if kind == "mail":
                ok, note = _mail_via_apprise(url, title, body)
            else:
                target, payload, why = _bot_target(kind, url, title, body, t.get("secret") or "")
                if target is None:
                    ok, note = False, why
                else:
                    status, raw = _post_json(target, payload)
                    ok, note = _verdict(kind, status, raw)
        except Exception as exc:  # noqa: BLE001  一条渠道坏了不许带走其余渠道
            ok, note = False, f"{type(exc).__name__}: {str(exc)[:100]}"
        if ok:
            out["sent_to"].append(key)
        else:
            out["failed"].append(key)
            notes.append(f"{key}：{note}")
        out["ok"] = out["ok"] and ok
    out["detail"] = ("；".join(notes) if notes else
                     f"{len(out['sent_to'])} 个渠道都确认收到")
    return out


def _mail_via_apprise(url: str, title: str, body: str) -> tuple[bool, str]:
    """邮件这一条仍然走 apprise（SMTP 握手、TLS、多收件人不该在这里重写一遍）。

    ⚠️ 打的是 `_apprise()` 这个取模块的口子，**不是本函数** —— 打整函数就等于没测到本体
    （10-16 那条 `aprise` 拼写错误就是这么活下来的）。
    """
    if os.environ.get("PYTEST_CURRENT_TEST") and _apprise is _APPISE_DEFAULT:
        raise RuntimeError("pytest 进程里不许真发邮件 —— 请 monkeypatch duanxian.notify._apprise")
    try:
        apprise = _apprise()
    except Exception as exc:  # noqa: BLE001
        return False, f"apprise 没装上或起不来（{type(exc).__name__}: {str(exc)[:80]}）—— 跑 scripts/setup"
    ap = apprise.Apprise()
    try:
        if not ap.add(url):
            return False, "apprise 认不下这条 mailto://（前缀或参数不对）"
    except Exception as exc:  # noqa: BLE001
        return False, f"apprise 认不下这条 mailto://（{type(exc).__name__}）"
    try:
        res = ap.notify(title=title, body=body, notify_type=apprise.NotifyType.INFO)
    except Exception as exc:  # noqa: BLE001
        return False, f"发送异常（{type(exc).__name__}: {str(exc)[:120]}）"
    return (True, "邮件已交给 SMTP（对面回执由 apprise 判）") if bool(res) else \
        (False, "SMTP 没收下")


def resolve_channels(keys: list | None = None, cfg: dict | None = None) -> tuple[list[dict], list[str]]:
    """把渠道键翻成可用渠道 + 不可用的原因列表。

    **不可用必须逐条出声**：一条计划指着三个渠道、其中两个的密钥变量不见了，
    只发成功的那一个而不说另外两个，用户就会以为"三条都送到了"。
    """
    cfg = cfg or NC.load()
    by = {c.get("key"): c for c in cfg["channels"]}
    # `plan.channels` 有两种来路：配置里存的是**键的字符串**，而 `plan_view` 交出去的是
    # 带 `exists/usable` 的**引用对象**。面板上点「预览正文」把后者原样传回来，
    # 按字符串处理就会印出 `{'key': 'panel-check', ...}：配置里没有这条渠道（可能被删了）`
    # —— 一个原始 dict 上屏 + 一条明明可用的渠道被判成不存在（10-16 浏览器实测抓到）。
    wanted = [str(k.get("key") if isinstance(k, dict) else k) for k in (keys or [])]
    usable, problems = [], []
    for key in wanted:
        ch = by.get(key)
        if ch is None:
            problems.append(f"{key}：配置里没有这条渠道（可能被删了）")
            continue
        view = NC.masked_channel(ch)
        if not view["enabled"]:
            problems.append(f"{key}：这条渠道被停用了")
            continue
        if not view["has_secret"]:
            problems.append(f"{key}：{view['missing_reason']}")
            continue
        if view["invalid_reason"]:
            problems.append(f"{key}：{view['invalid_reason']}")
            continue
        usable.append({"key": key, "url": NC.real_url(ch), "label": view["label"],
                       "kind": ch.get("kind"),
                       # 加签密钥单独带一格（同样只在这条路上出现，从不进读数、不进日志）
                       "secret": NC.real_secret(ch)})
    return usable, problems


def send_to(keys: list, title: str, body: str, *, cfg: dict | None = None) -> dict:
    """向指定渠道发一条。返回逐渠道的结果，**失败原因带着渠道键**（不带 URL）。

    ⚠️ 整体 `ok` 是**所有渠道都确认收到**才真 —— 一条没到就不是"已交付"。
    """
    usable, problems = resolve_channels(keys, cfg)
    if not usable:
        return {"ok": False, "sent_to": [], "skipped": problems, "detail": "没有可用渠道，未发送"}
    res = deliver(usable, title, body)
    return {"ok": res["ok"], "sent_to": res["sent_to"], "skipped": problems,
            "detail": res["detail"], "failed": res.get("failed") or []}


# ---------------------------------------------------------------- 去重与留痕
def _sent_map() -> dict:
    try:
        raw = json.loads(Path(sent_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def already_sent(plan_id: str, day: str) -> bool:
    return bool(_sent_map().get(f"{plan_id}:{day}"))


def mark_sent(plan_id: str, day: str, *, ok: bool) -> None:
    """记下"这一条今天发过了"。

    **失败也记**，但记的是"试过了、结果失败"：不记就会在同一分钟内重试成一串，
    而记死了又不给手动重发留口子也不行 —— 所以手动触发（`force=True`）绕过这道闸。
    真故障要重试的口子交给面板上那个「立刻发一次」，不交给循环自己猜。
    """
    key = f"{plan_id}:{day}"
    data = _sent_map()
    data[key] = {"at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"), "ok": bool(ok)}
    # 只留最近 60 天，防这个文件无限长
    cut = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    data = {k: v for k, v in data.items() if str(k).rsplit(":", 1)[-1] >= cut or k == key}
    path = Path(sent_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def log_history(row: dict) -> None:
    """每次发送追加一行 jsonl（成败都记）。**只记渠道键与字数，不记 URL。**"""
    path = NC.data_home() / NC.HISTORY_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass                       # 留痕写不下去不该把发送本身弄成异常，但下面会记账


def history(limit: int = HISTORY_LIMIT) -> list[dict]:
    path = NC.data_home() / NC.HISTORY_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- 一条计划的执行
def run_plan(plan: dict, *, now: datetime | None = None, force: bool = False,
             date: str = "") -> dict:
    """执行一条计划：判该不该发 → 组内容 → 发 → 记账 + 历史。

    `force=True` 是面板上的「立刻发一次」：绕过"今天发过了"，但**不绕过**
    渠道不可用的判定（没配好就是不许发，那正是要让人看见的失败）。
    """
    now = now or datetime.now().astimezone()
    day = _session_date(date)
    pid = str(plan.get("id"))
    out = {"plan": pid, "name": plan.get("name"), "date": day, "forced": force,
           "status": "skip", "reason": "", "channels": plan.get("channels") or []}
    if not plan.get("enabled", True) and not force:
        out["reason"] = "这条计划是关着的"
        return out
    why = NC.validate_plan(plan)
    if why:
        out["reason"] = f"配置本身不合法：{why}"
        out["status"] = "bad_config"
    elif not force and already_sent(pid, day):
        out["reason"] = f"{day} 已经发过（要重发用「立刻发一次」）"
        return out
    # 先解渠道再组正文：正文要切到**这批渠道里最小的那个上限**（企微 2048 是硬拒），
    # 所以顺序不能反 —— 先组再解就只能赌一个统一长度。
    targets, blocked = resolve_channels(plan.get("channels") or [])
    cap = body_limit([t.get("kind") for t in targets])
    prefix = str(plan.get("name") or "")
    # **分条还是合并**（用户 2026-10-19：「按照对应的点，分条推送。不要合并推送。」）。
    # **后端缺省是 `merged`**（10-19 之前唯一的形状，旧计划不动它就应该照旧发一条）；
    # 「分条」由面板上新建/编辑那一份表单默认勾上（`notifyPlanForm.ts::emptyForm`），
    # 所以用户新加的计划一上来就是 per_scene，而既有计划与测试看到的还是老行为。
    # 两档共用 `_scene_part` / `_clip`，所以同一次勾选在两条路上取到的数、说的失败话是一样的。
    mode = str(plan.get("send_mode") or "merged")
    if mode == "per_scene":
        msgs = compose_split(plan.get("scenes") or {}, day, title_prefix=prefix, limit=cap)
        if not msgs:
            out.update({"status": "bad_config", "reason": "这条计划没勾选任何内容 ⇒ 分条也就无条可发"})
            return out
        sends = [deliver(targets, m["title"], m["body"]) for m in msgs]
        res = {"ok": all(s["ok"] for s in sends),
               "detail": "；".join(f"「{m['label']}」{s['detail']}"
                                   for m, s in zip(msgs, sends))[:400],
               "sent_to": sorted({k for s in sends for k in (s.get("sent_to") or [])}),
               "skipped": sorted({k for s in sends for k in (s.get("skipped") or [])})}
        meta = {"scenes": {m["scene"]: m["status"] for m in msgs},
                "wire_bytes": sum(m["wire_bytes"] for m in msgs),
                "body_limit": cap, "truncated": any(m["truncated"] for m in msgs)}
        out["messages"] = [{"label": m["label"], "bytes": m["wire_bytes"],
                            "truncated": m["truncated"], "status": m["status"]} for m in msgs]
    else:
        title, body, meta = compose(plan.get("scenes") or {}, day,
                                    title_prefix=prefix, limit=cap)
        res = deliver(targets, title, body)
    res["skipped"] = list(blocked) + list(res.get("skipped") or [])
    out.update({"status": "ok" if res["ok"] else "fail", "detail": res["detail"],
                "sent_to": res["sent_to"], "skipped_channels": res["skipped"],
                "bytes": meta["scenes"].get("wire_bytes"),      # 记**整条**的字节（手机上看到的就是这个）
                "body_limit": meta["scenes"].get("body_limit"),
                "truncated": bool(meta["scenes"].get("truncated")),
                "scenes": meta.get("scenes")})
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    log_history({"at": stamp, "plan": pid, "name": plan.get("name"), "date": day,
                 "status": out["status"], "detail": res["detail"],
                 "sent_to": res["sent_to"], "skipped": res["skipped"], "bytes": out["bytes"],
                 # 手机上收到的是半截时，这两格是唯一的解释（不记就只剩"消息好短"这个现象）
                 "body_limit": out.get("body_limit"), "truncated": out.get("truncated"),
                 "forced": force})
    mark_sent(pid, day, ok=res["ok"])
    _journal(out)
    return out


def _journal(out: dict) -> None:
    """往 `jobs/state.json` 记一笔（键 `notify_push`）—— 体检与面板都从那儿读。

    归因文字只认 `reason` 键（系统配置定的约定）。**失败必须记成 not-ok**，
    否则"计划跑了但一个渠道都没通"在页面上就只剩一片绿。
    """
    try:
        from duanxian import jobs
        ok = out.get("status") == "ok"
        jobs.record("notify_push", ok=ok, detail={
            "reason": (out.get("detail") or out.get("reason") or "")[:200],
            "plan": out.get("plan"), "status": out.get("status"),
            "sent_to": out.get("sent_to") or [], "skipped": out.get("skipped_channels") or []})
    except Exception:  # noqa: BLE001  记账坏了不该把发送弄成异常，但也不能反过来说发送没坏
        pass


def due_plans(now: datetime | None = None, cfg: dict | None = None) -> list[dict]:
    """到点的计划列表（宿主每轮调这个，判据只在这里有一份）。"""
    now = now or datetime.now().astimezone()
    cfg = cfg or NC.load()
    if not cfg.get("enabled", True):
        return []
    day = now.strftime("%Y-%m-%d")
    out = []
    for plan in cfg["plans"]:
        if not NC.due_minute(plan, now):
            continue
        if already_sent(str(plan.get("id")), _session_date()):
            continue
        if plan.get("trade_days_only", True) and not _trade_day(day):
            continue
        out.append(plan)
    return out


def trade_day_or_none(day: str) -> bool | None:
    """这一天是不是大 A 交易日，**交三态**（`None` = 判不了）。
    发送侧不用它（那边要的是"判不了照发"），读数侧用它 —— 面板把"判不了"显示成"今天不跑"
    就是拿猜测冒充读数。"""
    try:
        from duanxian import pg
        return pg.trade_day_open(day)
    except Exception:  # noqa: BLE001
        return None


def _trade_day(day: str) -> bool:
    """今天是不是大 A 交易日。**判不了就返回 True（照发）** ——
    与 `fundflow` 同一读法：日历缺一天不该让人一份日报都没有，而页面会按 warnings 出声。
    """
    got = trade_day_or_none(day)
    return True if got is None else bool(got)


def preview(plan: dict, date: str = "") -> dict:
    """不发送，只把这条计划会发出的正文交出来。

    这是"发之前先看一眼"那格：面板上多选内容之后，光看勾是看不出对错的，
    必须能读到将要发出去的那段字。正文里的 URL 一个都没有（本来也不在这里）。
    """
    day = _session_date(date)
    # 与 `run_plan` 同一顺序：先解渠道、再按这批渠道里最小的上限切正文，
    # 否则「预览看到的」与「实际发出去的」会是两份长度（那还不如不预览）。
    usable, problems = resolve_channels(plan.get("channels") or [])
    cap = body_limit([t.get("kind") for t in usable])
    prefix = str(plan.get("name") or "")
    if str(plan.get("send_mode") or "merged") == "per_scene":
        # 预览也必须按**将要各发一条**的样子交：把 N 条并排印出来（每条带上它自己的标题），
        # 而不是给一份合并正文 —— 否则"预览看着是一条、手机收到六条"，预览就成了骗人的。
        msgs = compose_split(plan.get("scenes") or {}, day, title_prefix=prefix, limit=cap)
        joined = "\n\n".join(f"—— 第 {i + 1}/{len(msgs)} 条 ——\n{m['title']}\n{m['body']}"
                             for i, m in enumerate(msgs)) or "（这条计划没有勾选任何内容）"
        meta = {"date": day, "limit": cap, "wire_bytes": sum(m["wire_bytes"] for m in msgs),
                "scenes": {**{m["scene"]: m["status"] for m in msgs},
                           "body_limit": cap,
                           "truncated": any(m["truncated"] for m in msgs),
                           "split": [{"label": m["label"], "bytes": m["wire_bytes"],
                                      "truncated": m["truncated"]} for m in msgs]}}
        return {"title": f"{day or '今日'}推送（分 {len(msgs)} 条发）",
                "body": joined, "date": day, "meta": meta,
                "messages": [{"title": m["title"], "body": m["body"], "label": m["label"],
                              "bytes": m["wire_bytes"], "truncated": m["truncated"]} for m in msgs],
                "send_mode": "per_scene",
                "would_send_to": [c["key"] for c in usable], "blocked": problems,
                "invalid_reason": NC.validate_plan(plan) if plan.get("id") else None}
    title, body, meta = compose(plan.get("scenes") or {}, day,
                                title_prefix=prefix, limit=cap)
    return {"title": title, "body": body, "date": day, "meta": meta,
            "would_send_to": [c["key"] for c in usable], "blocked": problems,
            "invalid_reason": NC.validate_plan(plan) if plan.get("id") else None}
