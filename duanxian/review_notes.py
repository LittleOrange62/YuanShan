"""每日复盘笔记与关注提醒（`ys.review_note` / `ys.reminder`）。

用户 2026-09-28 提的三件事里的两件半：笔记要入库（原先写在桌面 Excel「做与直觉相反的事」
的 2026 sheet 里，132 行真实记录），提醒要能定"几个**交易日**之后"并排成时间轴。

三条规矩是这个模块的全部难点，破坏任何一条都会让这份记录失去意义：

1. **列结构只有一个真相源**，就是本文件的 `FIELDS`。建表 SQL（`app/db/sql/19_review_note.sql`）、
   接口交出的字段元数据、界面渲染的输入框，三处都从这张表来。界面里不许再抄一份中文列名 ——
   那正是 `heat`/`score` 两套判据教训过的样子。
2. **机器默认值不许盖掉人写的字**。`machine_baseline` 只在第一次保存时按列写入，之后只增不改；
   界面拿"当前值 == baseline"区分「机器填入·未改」与「初始为机器填入·你已改」。
   重算默认值只填**当前还空着**的格子，绝不覆盖已有内容。
3. **默认值只走本机已入库的数据与已落盘的缓存，一次联网都不做**（判据见 `_network_free()`）。
   要涨停原因题材串（问财）与三池明细（akshare）时，只读已经躺在 `cache/` 里的那一份；
   没有就如实写"本机没有落盘"，绝不为了填一格子去发请求。

与交易日志同一条红线：**这些内容不接入任何 AI prompt**（`journal.py:9` 立的规矩）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Any, Optional

TABLE_NOTE = "ys.review_note"
TABLE_REMINDER = "ys.reminder"

SESSION_MIN, SESSION_MAX = 1, 250
REMINDER_KINDS = ("stock", "theme", "free")
REMINDER_STATUS = ("open", "done", "cancelled")

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _field(label: str, group: str, question: str = "", note: str = "",
           no_default: str = "") -> dict[str, str]:
    return {"label": label, "group": group, "question": question, "note": note,
            "no_default": no_default}


#: 列名 → 中文。顺序就是界面上从上看到下的顺序，与桌面 Excel 的列序一致。
#: `question` 是用户列的七个自问（2026-09-28），placeholder 直接用它，比另写一句引导语实在。
FIELDS: dict[str, dict[str, str]] = {
    "env": _field("大环境", "当天背景",
                  note="机器给的是本机快讯当日标题（来源原话），不是结论；外围市场隔夜涨跌本机读不到（没有外盘行情入库）"),
    "plan_market": _field("对大盘、板块", "自己判断（日期前一天情况）",
                          question="明天预期是什么？",
                          note="开盘**前**写的预判，所以机器给的是**前一交易日**的读数；写的时候这一天还没收盘"),
    "plan_stock": _field("对个股定计划", "自己判断（日期前一天情况）",
                         question="趋势还是连板？",
                         note="这一格是你的计划。机器只把上一场的涨停梯队与连板高度摆出来，不替你选票"),
    "reflect": _field("反思复盘", "反思复盘", no_default="这一列没有对应的本机事实源，只能你写"),
    "view_market": _field("对大盘、板块", "市场看法和实际走势（日期当天情况）",
                          question="今天的赚钱效应在哪里？放量还是缩量？哪些板块涨的好？",
                          note="派生情绪指标 + 涨跌家数 + 成交额 + 板块涨幅前列，全部取自库里已入库的那一场"),
    "view_news": _field("政策、消息", "市场看法和实际走势（日期当天情况）",
                        question="涨的好的原因是什么？",
                        note="本机快讯当日标题 + 已落盘的涨停题材串前列。题材串是来源归因，不是公司确认事实"),
    "view_stock": _field("对个股", "市场看法和实际走势（日期当天情况）",
                         question="连板的话，分歧还是一致？",
                         note="封板质量（一次没开过的比例、炸板率）+ 晋级率 + 梯队，这些只是「分歧/一致」的证据，结论由你下"),
    "view_bigv": _field("关注的主要大V观点", "市场看法和实际走势（日期当天情况）",
                        no_default="本机没有任何大V观点数据源（没有社媒接入、没有观点库），这一格永远只能是空的。"
                                   "如果它出现了「机器填入」的标记，那是缺陷，不是便利"),
    "exec_review": _field("策略的执行复盘、反思", "策略的执行复盘、反思",
                          note="机器只把本机交易日志里**当天录入的那几笔**摆出来（打法、是否按计划、当时写的备注），不评分"),
    "keep_doing": _field("继续做（Continue doing）", "复盘", no_default="要跨日才总结得出来，单日没有默认值可给"),
    "stop_doing": _field("停止做（Stop doing）", "复盘", no_default="要跨日才总结得出来，单日没有默认值可给"),
    "start_doing": _field("开始做（Start to do）", "复盘", no_default="要跨日才总结得出来，单日没有默认值可给"),
    "pattern": _field("规律总结", "规律总结", no_default="要跨日才总结得出来，单日没有默认值可给"),
}
FIELD_KEYS = list(FIELDS)


class NoteError(ValueError):
    """字段名不认识、日期不是 YYYY-MM-DD 这一类**调用方的错**（接口按 400 交回）。"""


def _dash(date: str) -> str:
    d = (date or "").strip().replace("/", "-")[:10]
    if not _DATE.match(d):
        raise NoteError(f"日期要写成 YYYY-MM-DD，收到的是「{date}」")
    return d


# --------------------------------------------------------------------------- 日历
def _calendar_horizon() -> str:
    """本机交易日历覆盖到哪天（YYYY-MM-DD）。查不到交回空串，让调用方按"没有日历"处理。"""
    from app.db import engine

    row = engine.query_one(
        "select max(calendar_date)::text as d from reference_data.trade_calendar where is_open")
    return (row or {}).get("d") or ""


def due_on(base_date: str, sessions: int) -> dict[str, Any]:
    """`base_date` **之后**第 `sessions` 个交易日（不含当天）。

    按交易日而不是自然日算（用户 2026-09-28 选定口径）：中间隔着周末和长假，
    自然日会把"第 7 个交易日"落在一个根本没有行情的日子。

    日历覆盖不到时**不冒充**：`calendar_covered=False`，日期改用"跳过周末"的估计值，
    并把话说清 —— 这个估计值不含节假日，长假会把它推后，所以它不是交易日。
    """
    from duanxian import pg_sources, trade_calendar as cal

    base = _dash(base_date)
    n = int(sessions)
    if not SESSION_MIN <= n <= SESSION_MAX:
        raise NoteError(f"提醒的交易日数要在 {SESSION_MIN}~{SESSION_MAX} 之间，收到 {sessions}")

    horizon = _calendar_horizon()
    # 起算日落在周末/假日时，从它的下一个交易日往后数；否则就是"这一天之后"的第 n 个
    start = cal.next_trade_date(base) or base
    span_end = (dt.date.fromisoformat(start) + dt.timedelta(days=n * 2 + 40)).isoformat()
    dates = (pg_sources.trade_dates(start, span_end) or []) if horizon else []
    if len(dates) >= n and dates[n - 1] <= horizon:
        return {"due_date": dates[n - 1], "calendar_covered": True, "sessions": n,
                "explain": f"{base} 之后第 {n} 个交易日"}

    d = dt.date.fromisoformat(start)
    steps = 0
    while steps < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            steps += 1
    return {"due_date": d.isoformat(), "calendar_covered": False, "sessions": n,
            "explain": f"交易日历只覆盖到 {horizon or '（读不到）'}，这个日期是**跳过周末**估的，"
                       "不含节假日顺延，不是交易日"}


# --------------------------------------------------------------------------- 笔记读写
def fields_meta() -> list[dict[str, str]]:
    """交出字段清单（列名/中文/分组/自问/默认值说明）。界面据此渲染，自己不写死任何列名。"""
    return [{"key": k, **v} for k, v in FIELDS.items()]


def note_of(date: str) -> Optional[dict[str, Any]]:
    from app.db import engine

    row = engine.query_one(f"select * from {TABLE_NOTE} where trade_date = %s", (_dash(date),))
    if not row:
        return None
    row["trade_date"] = str(row["trade_date"])
    for c in ("created_at", "updated_at", "machine_generated_at"):
        if row.get(c) is not None:
            row[c] = str(row[c])
    base = row.get("machine_baseline")
    if isinstance(base, str):
        try:
            base = json.loads(base)
        except ValueError:
            base = {}
    row["machine_baseline"] = base or {}
    # `values` 单独交一份：界面拿到 13 个正文列不用自己按列名去这一堆键里刨
    # （那些键里还混着 trade_date / updated_at / machine_* 这些非正文列）。
    row["values"] = {k: row.get(k) or "" for k in FIELD_KEYS}
    return row


def list_notes(limit: int = 120) -> list[dict[str, Any]]:
    """已有笔记的日期清单，带"哪几格有字、一共多少字"，一眼看出哪天是空壳。

    长度用 `length()` 在库里算，而不是把正文拉回 Python 再数：一份笔记最长那一格实测
    写过 6186 字，130 份全拉回来是几百 KB 只为数个数。
    """
    from app.db import engine

    lens = ", ".join(f"coalesce(length({k}), 0) as len_{k}" for k in FIELD_KEYS)
    rows = engine.query(
        f"""select trade_date::text as trade_date, updated_at::text as updated_at,
                   machine_generated_at::text as machine_generated_at, {lens}
            from {TABLE_NOTE} order by trade_date desc limit %s""", (int(limit),)) or []
    out = []
    for r in rows:
        lens_r = {k: int(r.pop(f"len_{k}", 0) or 0) for k in FIELD_KEYS}
        r["chars"] = sum(lens_r.values())
        r["filled"] = sum(1 for v in lens_r.values() if v > 0)
        r["field_chars"] = lens_r
        out.append(r)
    return out


def _merge_baseline(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """只补**还没有**的键，已有的一个字都不改（文件头第 2 条规矩落在这里）。"""
    out = dict(existing or {})
    for k, v in (incoming or {}).items():
        if k in FIELD_KEYS and k not in out:
            out[k] = v
    return out


def save_note(date: str, values: dict[str, Any], machine_baseline: Optional[dict[str, Any]] = None,
              machine_generated_at: Optional[str] = None) -> dict[str, Any]:
    """按行情日 upsert 一份笔记。认不出的字段名一律 400 点名 —— 静默丢掉一栏，
    用户会以为"存过了"，而那一栏其实从来没进库。校验放在连库之前：
    认不出字段这种错没必要先惊动数据库。"""
    d = _dash(date)
    bad = [k for k in (values or {}) if k not in FIELDS]
    if bad:
        raise NoteError("这些字段本表没有：" + "、".join(sorted(bad)) + "；能用的是 " + "、".join(FIELD_KEYS))
    clean = {k: ("" if values.get(k) is None else str(values.get(k))) for k in FIELD_KEYS}
    current = note_of(d)
    old_baseline = (current or {}).get("machine_baseline") or {}
    # 只有"存进去的内容与机器给的那一份**一字不差**"才配留下 baseline。
    # 用户改过一字的格子，baseline 就不再等于当前值 —— 界面据此显示「初始为机器填入·你已改」。
    incoming = {k: v for k, v in (machine_baseline or {}).items()
                if k in FIELDS and isinstance(v, str) and v and clean.get(k) == v}
    baseline = _merge_baseline(old_baseline, incoming)

    from app.db import engine

    cols = ", ".join(FIELD_KEYS)
    vals = [clean[k] for k in FIELD_KEYS]
    engine.execute(
        f"""insert into {TABLE_NOTE} (trade_date, {cols}, machine_baseline, machine_generated_at)
            values (%s, {", ".join(["%s"] * len(FIELD_KEYS))}, %s::jsonb, %s)
            on conflict (trade_date) do update set
              {", ".join(f"{k} = excluded.{k}" for k in FIELD_KEYS)},
              machine_baseline = excluded.machine_baseline,
              machine_generated_at = coalesce({TABLE_NOTE}.machine_generated_at, excluded.machine_generated_at),
              updated_at = now()
        """,
        (d, *vals, json.dumps(baseline, ensure_ascii=False), machine_generated_at), role="app")
    row = note_of(d)
    if not row:
        raise RuntimeError("保存后读不回这一份，不假定已生效")
    return row


# --------------------------------------------------------------------------- 只读已落盘的缓存
def _disk_json(path: str, want_date: str, schema_keys: tuple[str, ...]) -> Optional[dict]:
    """读一个按日落盘的缓存信封；日期或版本对不上就当没有（宁可空着，不拿别场的东西顶）。"""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            env = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(env, dict) or env.get("date") != want_date:
        return None
    if not all(k in env for k in schema_keys):
        return None
    return env


def cached_reasons(date: str) -> tuple[dict[str, str], str]:
    """这一场的涨停题材串，**只读 `cache/zt_reasons/`，不发请求**（判据见 `_network_free()`）。"""
    from duanxian import theme_tree

    d = _dash(date)
    path = os.path.join(theme_tree._CACHE_DIR, f"{d}.json")
    env = _disk_json(path, d, ("schema", "reasons"))
    if env is None or env.get("schema") != theme_tree._SCHEMA:
        return {}, "这一场的涨停题材串本机没有落盘（问财要联网，默认值不联网）"
    return {str(k).zfill(6): str(v) for k, v in (env.get("reasons") or {}).items()}, "已落盘缓存"


def cached_pools(date: str) -> Optional[dict]:
    """这一场的三池明细，只在**已定稿且缓存新鲜**时给，否则给 None（`market_facts.pools()` 无缓存会联网）。"""
    from duanxian import market_facts, trade_calendar as cal

    d = _dash(date)
    if not cal.is_settled(d):
        return None
    path = os.path.join(market_facts._CACHE_DIR, f"{d}.json")
    env = _disk_json(path, d, ("pools",))
    if env is None or env.get("schema") not in market_facts._FACTS_SCHEMA_READABLE:
        return None
    return env["pools"]


#: 草稿本身也落盘（只给已定稿的日子）。判据是 `emotion_metrics` 里那两个冷启动读数：
#: 实测 `money_effect` 冷 2612ms / 热 171ms，`promotion_rates` 冷 2767ms —— 一个**新进程**
#: 里凑齐一份草稿要 ~10 秒，而这一屏每天要开好几次。定稿日子的输入不会再变（库里那一场
#: 已经落定），所以算过一次就存下来；未定稿的当天**不落盘**，因为落盘会把当天缺数据的样子留到明天。
_DEFAULTS_DIR = None
_DEFAULTS_SCHEMA = 1
#: **草稿的算法版本**。落盘缓存按 `日期 + 这一版` 命中，改 `defaults_for` 里任何措辞或分桶
#: 判据都要在这里 +1。理由不是洁癖：09-28 实测把「巴西央行行长」从国内桶挪到外盘桶之后，
#: 不带版本号的缓存仍然把旧的那份发回来 —— 代码改了、界面还是旧的，而且看不出来。
_DEFAULTS_BUILDER = "rb-3"


def _defaults_dir() -> str:
    global _DEFAULTS_DIR
    if _DEFAULTS_DIR is None:
        from duanxian.paths import data_path

        _DEFAULTS_DIR = data_path("cache/review_defaults")
    return _DEFAULTS_DIR


def _read_defaults_cache(date: str) -> Optional[dict[str, Any]]:
    path = os.path.join(_defaults_dir(), f"{date}.json")
    env = _disk_json(path, date, ("schema", "payload"))
    if env is None or env.get("schema") != _DEFAULTS_SCHEMA or env.get("builder") != _DEFAULTS_BUILDER:
        return None
    payload = env["payload"]
    payload["from_cache"] = True
    payload["cache_written_at"] = env.get("written_at")
    return payload


def _write_defaults_cache(date: str, payload: dict[str, Any]) -> None:
    from duanxian.util import atomic_write_json

    body = {k: v for k, v in payload.items() if k != "from_cache"}
    atomic_write_json(os.path.join(_defaults_dir(), f"{date}.json"),
                      {"schema": _DEFAULTS_SCHEMA, "builder": _DEFAULTS_BUILDER, "date": date,
                       "written_at": body.get("generated_at"), "payload": body})


_NETWORK_FREE = True


def _network_free() -> bool:
    """为什么默认值一律不许联网：

    这一屏是**每天开盘前后都要打开**的。任何一个字段挂了联网取数（涨停原因题材串走问财、
    三池明细在没有落盘缓存时走 akshare），都会让"记一笔"变成"等几秒、还可能失败"。
    更糟的是失败形态：网络一抖那一格就空着，而它空着和"这一天确实没有涨停"长得一模一样 ——
    这正是本项目在别处反复栽过的"取不到 ≠ 没有"。所以只允许本机已有的东西进来，
    取不到就在 `missing` 里点名是哪一源、为什么。
    """
    return True


# --------------------------------------------------------------------------- 默认值
def _fmt_pct(v: Optional[float], digits: int = 2) -> str:
    return "—" if v is None else f"{v:+.{digits}f}%"


def _join(lines: list[str]) -> str:
    return "\n".join(x for x in lines if x)


def _line(label: str, value: Any) -> str:
    return "" if value is None or value == "" else f"· {label}：{value}"


def _prev(date: str) -> Optional[str]:
    from duanxian import trade_calendar as cal
    return cal.prev_trade_date(date)


def _rate(v: Optional[float]) -> str:
    return "—" if v is None else f"{round(v * 100)}%"


def _news_bucket(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """把本机快讯按标题关键词粗分成三桶。

    ⚠️ 这是**关键词粗筛，不是分类结论**，所以第三桶「其余」必须连同条数一起交回界面：
    只交前两个桶会让人以为 244 条都被归好类了。分桶的目的是让「大环境」那一格
    拿到外围/宏观（Excel 里用户写的"美伊战争，美国攻打伊朗"就是这个层次），
    「政策、消息」那一格拿到国内政策与产业消息。

    ⚠️ **先判外盘、再判国内**，顺序不能倒：实测 09-24 那 244 条里，
    「洲际交易所（ICE）数据显示荷兰近月批发天然气上涨」这种句子，若先按国内词表筛，
    会因为"交易所"三个字被塞进「政策、消息」，而它讲的是荷兰天然气。
    国内那一桶因此只用**具体机构名与 A 股专名**，不用"合同/规则/会议/公告"这类到处都有的词。

    第二次实测又抓到一条：「巴西央行行长：不会被数据冲昏头脑」被"央行"捞进了国内桶。
    所以**标题里出现境外地名的，一律先归外盘**，哪怕它同时说了央行/证监会/药监这类机构词 ——
    「政策、消息」那一格对用户的意思是"中国的政策"。
    """
    places = ("美国", "美元", "美债", "美联储", "俄罗斯", "乌克兰", "伊朗", "以色列",
              "中东", "英国", "欧洲", "欧盟", "欧元区", "德国", "法国", "意大利", "西班牙", "荷兰",
              "波兰", "瑞士", "瑞典", "挪威", "丹麦", "日本", "日经", "韩国", "朝鲜", "印度", "巴基斯坦",
              "加拿大", "澳大利亚", "新西兰", "智利", "秘鲁", "巴西", "阿根廷", "墨西哥", "委内瑞拉",
              "土耳其", "南非", "埃及", "沙特", "阿联酋", "卡塔尔", "科威特", "伊拉克", "缅甸", "柬埔寨",
              "老挝", "越南", "泰国", "马来西亚", "新加坡", "印尼", "菲律宾", "蒙古", "哈萨克斯坦",
              "港股", "香港", "台湾", "华尔街", "欧佩克", "OPEC", "LME", "ICE", "纳指", "道指", "标普",
              "白宫", "外国", "海外", "境外")
    commodity = ("原油", "天然气", "黄金", "白银", "伦铜", "期货", "美联储", "白宫")
    foreign = places + commodity
    #: 国内那一桶**只认具体机构名与 A 股专名**。第三版实测把"政策/通知/规划/补贴/增持/回购"
    #: 这类词删掉了：它们到处都出现（「不可抗力通知」「加纳央行」「花旗增持百度」都会命中），
    #: 命中了就把外国公司新闻塞进「政策、消息」这一格。草稿宁可少几行，也不能是错的 ——
    #: 用户一旦发现这一格里混进不相干的东西，就整格都不看了。少掉的那些条数在
    #: 「其余未归类」里如实交出，想看全部去「资讯雷达」。
    domestic = ("中国证监会", "证监会", "国务院", "中国人民银行", "人民银行", "发改委", "国家发改委",
                "工信部", "财政部", "商务部", "人社部", "国资委", "金融监管总局", "国家药监局", "药监局",
                "上交所", "深交所", "北交所", "沪深交易所", "中国结算", "中证协",
                "沪指", "深指", "A股", "A 股", "港股通", "沪深两市", "两市", "上市公司",
                "专项债", "批文", "注册生效", "免税", "科创板", "创业板", "北证", "LPR", "MLF")

    def hit(x: dict[str, Any], words: tuple[str, ...]) -> bool:
        text = str(x.get("title") or "")
        return any(w in text for w in words)

    out: dict[str, list[dict[str, Any]]] = {"macro": [], "domestic": [], "other": []}
    for x in items:
        if hit(x, foreign):
            out["macro"].append(x)
        elif hit(x, domestic):
            out["domestic"].append(x)
        else:
            out["other"].append(x)
    return out


def defaults_for(date: str, refresh: bool = False) -> dict[str, Any]:
    """这一天的机器草稿：每个字段一段文本 + 这段话是从哪些读数拼出来的。

    交给界面的结构：
      `fields`  {列名: {"text": 草稿, "from": [读数名…]}}   —— 只给**有事实可说**的列
      `empty`   {列名: 为什么这一格机器给不出}             —— `FIELDS` 里标了 no_default 的列
      `missing` {列名: 这次哪个源没读到、为什么}           —— "该有事实却读空"与"本来就没有"是两件事
      `notes`   全局说明（日历、题材串来源、耗时）
      `from_cache`/`cache_written_at` 已定稿的日子第二次读的是落盘草稿，交回"那是什么时候算的"
    """
    from duanxian import breadth, emotion_metrics, market_facts, qd, trade_calendar as cal
    from duanxian import journal, pg

    d = _dash(date)
    # 落盘的条件不是"这一天收盘了"，而是**库里已经有这一场的日线**。
    # 09-28 实测踩到的正是差的那一层：15:40 时 `is_settled('2026-09-28')` 已经是 True，
    # 而 `stock_daily` 还没有 09-28 —— 按"收盘了"落盘，等于把"数据还没进来"的样子存下来，
    # 明天再进来就永远读的是那份空草稿。
    latest = expected_date()
    settled = cal.is_settled(d)
    has_data = bool(latest) and d <= latest
    cacheable = settled and has_data
    if cacheable and not refresh:
        hit = _read_defaults_cache(d)
        if hit:
            return hit
    got: dict[str, dict[str, Any]] = {}
    missing: dict[str, str] = {}
    notes: list[str] = []
    started = dt.datetime.now()

    def put(key: str, text: str, src: list[str], why: str = "") -> None:
        text = (text or "").strip()
        if text:
            got[key] = {"text": text, "from": src}
        elif key not in missing:
            missing[key] = why or "本机这一场的读数为空（这不等于「这一格不该有内容」）"

    # ---- 快讯：本机 jsonl。同一份文件按关键词粗分成「大环境」与「政策、消息」两格
    items: list[dict[str, Any]] = []
    try:
        nw = qd.news(d, limit=400)
        if nw.get("available"):
            items = nw.get("items") or []
        else:
            notes.append(f"本机快讯这一天没有文件：{nw.get('reason') or '（没给原因）'}")
    except Exception as e:  # noqa: BLE001
        notes.append(f"本机快讯读取失败：{e}")
    bucket = _news_bucket(items)

    def titles(rows: list[dict[str, Any]], n: int, with_time: bool = True) -> list[str]:
        return [f"  {' '.join(x for x in [str(x.get('published_at') or '')[11:16] if with_time else '',
                                          str(x.get('source') or ''), str(x.get('title') or '')[:62]] if x)}"
                for x in rows[:n]]

    put("env", _join([
        f"【本机快讯 {len(items)} 条，按关键词粗筛出外围/宏观 {len(bucket['macro'])} 条，列前 {min(len(bucket['macro']), 6)} 条】"
        if bucket["macro"] else "",
        *titles(bucket["macro"], 6),
        "· 外围指数与商品的**隔夜涨跌数字**：本机没有外盘行情入库，这一条机器给不出，只有快讯标题",
    ]), ["news/newswire jsonl（关键词粗筛）"],
        "本机这一天没有快讯文件，所以「大环境」没有可引的标题")

    # ---- ①② 开盘前的预判：一律用**前一交易日**的读数（这一天的盘还没开）
    prev = _prev(d)
    if prev:
        try:
            m = emotion_metrics.build_metrics(prev, with_cycle=False)
            put("plan_market", _join([f"【上一场 {prev} 的派生读数 —— 写在这一场开盘前】",
                                      emotion_metrics.render_metrics(m)]),
                ["emotion_metrics.build_metrics(prev)", "render_metrics"])
        except Exception as e:  # noqa: BLE001
            missing["plan_market"] = f"上一场情绪指标取数失败：{e}"
        try:
            ladder = qd.board_ladder(prev) or {}
            height = (ladder.get("board_height") or {}).get("main_board_highest")
            tiers = ladder.get("tiers") or []
            gap = emotion_metrics.ladder_gap(prev) or {}
            put("plan_stock", _join([
                f"【上一场 {prev} 的连板结构】",
                _line("主板最高板", f"{height} 板" if height else None),
                _line("梯队", "、".join(f"{t.get('tier_label')} {t.get('stock_count')} 家" for t in tiers) or None),
                _line("断层", ("连续，无断层" if gap.get("continuous") else
                              "、".join(map(str, gap.get("gaps") or [])) or "看不出") if gap.get("available") else None),
            ]), ["qd.board_ladder(prev)", "emotion_metrics.ladder_gap(prev)"])
        except Exception as e:  # noqa: BLE001
            missing["plan_stock"] = f"上一场梯队取数失败：{e}"
    else:
        missing["plan_market"] = missing["plan_stock"] = "日历里找不到这一天的前一场"

    # ---- ③ 当天已发生的客观读数（情绪指标 + 涨跌家数 + 量能 + 板块涨幅前列）
    rendered = ""
    try:
        m = emotion_metrics.build_metrics(d, with_cycle=False)
        rendered = emotion_metrics.render_metrics(m) if m.get("date") else ""
    except Exception as e:  # noqa: BLE001
        notes.append(f"派生情绪指标取数失败：{e}")

    br: dict[str, Any] = {}
    try:
        br = breadth.market_breadth(d) or {}
    except Exception as e:  # noqa: BLE001
        notes.append(f"涨跌家数取数失败：{e}")

    cyc: dict[str, Any] = {}
    try:
        cyc = (qd.sentiment_cycle(d) or {}).get("qd") or {}
    except Exception as e:  # noqa: BLE001
        notes.append(f"情绪周期读数取数失败：{e}")
    met = cyc.get("metrics") or {}

    rot: list[str] = []
    try:
        rotation = (qd.sector_cycle(d) or {}).get("rotation") or []
        for x in ((rotation[0] if rotation else {}) or {}).get("items") or []:
            rot.append(f"  {x.get('sector_name')} {_fmt_pct(x.get('pct_change'))}"
                       f"（{x.get('up_count')} 涨 / {x.get('down_count')} 跌，成分 {x.get('member_count')} 只）")
            if len(rot) >= 6:
                break
    except Exception as e:  # noqa: BLE001
        notes.append(f"板块涨幅前列取数失败：{e}")

    put("view_market", _join([
        "【这一场已经发生的客观读数。看法由你写】",
        rendered,
        _line("涨跌家数", (f"涨 {br.get('up')} / 跌 {br.get('down')} / 平 {br.get('flat')}"
                          f"，跌超 5% {br.get('deep_down_5')} 家") if br.get("available") else None),
        _line("全市场成交额", (f"{br.get('amount_yi')} 亿"
                             + (f"，较上一场 {met.get('amount_change_pct'):+.2f}%"
                                if met.get("amount_change_pct") is not None else ""))
              if br.get("amount_yi") else None),
        _line("量能状态", cyc.get("volume_state")),
        _line("情绪阶段", cyc.get("stage_ui")),
        "· 涨幅前列板块（板块当日涨幅，库内口径）：" if rot else "",
        *rot,
    ]), ["emotion_metrics.render_metrics", "breadth.market_breadth", "qd.sentiment_cycle", "qd.sector_cycle"],
        "这一场库里还没有已入库行情（数据没落到位，不是没有行情）")

    # ---- ④ 政策、消息 + 涨得好的原因（题材串只读已落盘缓存）
    reasons, reason_note = cached_reasons(d)
    if reason_note != "已落盘缓存":
        notes.append(f"涨停题材串：{reason_note}")
    tag_lines: list[str] = []
    if reasons:
        try:
            from collections import Counter

            from duanxian.theme_tree import _tags     # 复用它的切分与属性词过滤，不在这里再抄一份词表

            cnt: Counter[str] = Counter()
            for r in reasons.values():
                cnt.update(t for t in _tags(r) if t)
            tag_lines = [f"  {t} ×{n} 只" for t, n in cnt.most_common(10)]
        except Exception as e:  # noqa: BLE001
            notes.append(f"题材串切分失败（不影响其它格）：{e}")

    put("view_news", _join([
        f"【本机快讯按关键词粗筛：国内政策/产业 {len(bucket['domestic'])} 条、"
        f"外围宏观 {len(bucket['macro'])} 条、其余未归类 {len(bucket['other'])} 条；"
        f"下面列国内那一批的前 {min(len(bucket['domestic']), 10)} 条】" if bucket["domestic"] else "",
        *titles(bucket["domestic"], 10, with_time=False),
        _line("涨停家数里出现最多的题材词", f"{len(reasons)} 只票有题材串" if reasons else None),
        "· 题材串前列（问财按当日归因，**不是公司确认事实**）：" if tag_lines else "",
        *tag_lines,
    ]), ["news/newswire jsonl（关键词粗筛）", "cache/zt_reasons"],
        "这一天既没有本机快讯文件，也没有落盘的涨停题材串")

    # ---- ⑤ 分歧还是一致的证据（涨停池只在有落盘缓存时才读）
    pools = cached_pools(d)
    if pools is None:
        missing["view_stock"] = "这一场的涨停池本机没有落盘缓存（现查要联网，默认值不联网）；晋级率那一档仍然给"
    try:
        promo = emotion_metrics.promotion_rates(d) or {}
        t12 = (promo.get("tiers") or {}).get("1进2") or {}
        overall = promo.get("overall") or {}
        seal_bits: list[str] = []
        if pools is not None:
            try:
                seal = market_facts.seal_quality(d) or {}
                if seal.get("available"):
                    seal_bits = [
                        _line("涨停 / 炸板 / 跌停", f"{seal.get('total')} / {seal.get('broken_pool')} / "
                                                   f"{(market_facts.loss_effect(d) or {}).get('limit_down_count')}"),
                        _line("一次没开过", f"{seal.get('never_broken')} 家（{_rate(seal.get('never_broken_rate'))}）"),
                        _line("开盘 5 分钟内首封 / 14:30 后才封住",
                              f"{seal.get('opening_seconds')} / {seal.get('late_seal')} 家"),
                        _line("炸板率", _rate(seal.get("broken_rate"))),
                    ]
            except Exception as e:  # noqa: BLE001
                notes.append(f"封板质量取数失败：{e}")
        put("view_stock", _join([
            "【分歧还是一致只看证据：一次没开过的越多越一致、晋级率越低越分歧。结论由你下】",
            *seal_bits,
            _line("1 进 2 晋级", f"{t12.get('promoted')}/{t12.get('base')}（{_rate(t12.get('rate'))}）"),
            _line("整体晋级", f"{overall.get('promoted')}/{overall.get('base')}（{_rate(overall.get('rate'))}）"
                             f"，昨涨停 {promo.get('prev_limit_up_count')} 家") if promo.get("available") else None,
        ]), ["market_facts.seal_quality（仅落盘缓存）", "emotion_metrics.promotion_rates"])
    except Exception as e:  # noqa: BLE001
        missing["view_stock"] = f"晋级率取数失败：{e}"

    # ---- ⑥ 执行复盘：本机交易日志里这一天录入的那几笔
    try:
        trades = [t for t in (journal.all_trades() or []) if str(t.get("date")) == d]
        off = [t for t in trades if t.get("as_planned") is False]
        put("exec_review", _join([
            f"【本机交易日志：这一天记了 {len(trades)} 笔，其中标了「计划外」{len(off)} 笔】" if trades else "",
            *["  " + "｜".join(x for x in [
                str(t.get("name") or t.get("code")), str(t.get("playbook") or ""),
                ("计划外" if t.get("as_planned") is False else ("按计划" if t.get("as_planned") is True else "未标")),
                str(t.get("note") or "")[:50]] if x) for t in trades[:10]],
        ]), ["journal trades(date)"], "本机交易日志这一天一笔都没有（那是没记，不是没交易）")
    except Exception as e:  # noqa: BLE001
        missing["exec_review"] = f"本机交易日志读不到：{e}"

    payload = {
        "date": d, "prev_date": prev,
        "is_trade_day": pg.trade_day_open(d), "settled": settled,
        "has_data": has_data, "cacheable": cacheable, "latest_data_date": latest,
        "news_counts": {k: len(v) for k, v in bucket.items()},
        "fields": got,
        "empty": {k: v["no_default"] for k, v in FIELDS.items() if v.get("no_default")},
        "missing": missing,
        "notes": notes,
        "generated_at": started.astimezone().isoformat(timespec="seconds"),
        "elapsed_ms": int((dt.datetime.now() - started).total_seconds() * 1000),
        "network_free": _NETWORK_FREE,
        "hint": "默认值只是草稿：不覆盖你已经写过的字，重算也只补空着的那几格。",
    }
    if cacheable:
        try:
            _write_defaults_cache(d, payload)
        except Exception as e:  # noqa: BLE001  缓存写不进不影响正确性，下一次现算就是了
            payload["notes"] = notes + [f"草稿缓存没能落盘（下次进来会重算）：{e}"]
    return payload


# --------------------------------------------------------------------------- 界面用的整份返回
def expected_date() -> Optional[str]:
    """默认打开哪一天：库里**实际有数**的最近一场。

    规则只有 `trade_calendar.latest_data_session()` 一处实现 —— 复盘页、首板页、这一页
    必须给出同一个日子，否则会出现"笔记让你写 09-28、盘面说数据只到 09-24"。
    """
    from duanxian import trade_calendar as cal

    date, _gap = cal.latest_data_session()
    return date


def day_bundle(date: str, refresh: bool = False) -> dict[str, Any]:
    """一次给全这一屏要的东西：字段清单 + 已存的那份 + 机器草稿。

    草稿**每次进页面都算**（不缓存时冷启动 ~10 秒、命中落盘缓存 0 毫秒）：
    已定稿的日子草稿落盘了，所以第二次是瞬时的；当天那份不落盘（会把"数据还没进来"
    的样子留到明天），所以每次现算 —— 那种慢是应该慢，界面要如实说"正在算草稿"。
    """
    note = note_of(date)
    saved = note is not None
    return {"fields": fields_meta(), "date": _dash(date), "note": note, "saved": saved,
            "defaults": defaults_for(date, refresh=refresh),
            "expected_date": expected_date()}


# --------------------------------------------------------------------------- 提醒
def add_reminder(base_date: str, sessions: int, note: str = "", reason: str = "",
                 kind: str = "stock", security_code: str = "", security_name: str = "") -> dict[str, Any]:
    from app.db import engine

    if kind not in REMINDER_KINDS:
        raise NoteError(f"提醒类型只有 {'/'.join(REMINDER_KINDS)}，收到「{kind}」")
    code = (security_code or "").strip()
    if kind == "stock" and not re.match(r"^\d{6}$", code):
        raise NoteError(f"个股提醒要 6 位代码，收到「{security_code}」；不想带标的请把类型选成「自由提醒」")
    due = due_on(base_date, int(sessions))
    engine.execute(
        f"""insert into {TABLE_REMINDER}
              (kind, security_code, security_name, note, reason, base_date, sessions, due_date, calendar_covered)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (kind, code or None, (security_name or "").strip() or None,
         (note or "").strip(), (reason or "").strip(), _dash(base_date), int(sessions),
         due["due_date"], due["calendar_covered"]), role="app")
    return list_reminders()


def _reminder_rows(status: Optional[str] = None) -> list[dict[str, Any]]:
    """按到期日升序取提醒行（`open` 的排在最前）。单独一处是为了时间轴的排序只有一套。"""
    from app.db import engine

    where = "where status = %s" if status else ""
    params = (status,) if status else ()
    return engine.query(
        f"""select id, kind, security_code, security_name, note, reason,
                   base_date::text as base_date, sessions, due_date::text as due_date,
                   calendar_covered, status, done_at::text as done_at,
                   created_at::text as created_at, updated_at::text as updated_at
            from {TABLE_REMINDER} {where}
            order by (status = 'open') desc, due_date asc, id asc""", params) or []


def list_reminders(status: Optional[str] = None, today: Optional[str] = None) -> dict[str, Any]:
    """时间轴：`next` = 今天或以后最近的那条未处理提醒；过期条目带着 `overdue_days` 一起交出。

    排序只认**到期日**：用户问的是"当前往后最近的一条是什么"，创建早晚与那条没关系。
    过期而没处理的不许被过滤掉 —— 只交"未来"那一条会让人误以为过期就等于完成。
    """
    rows = _reminder_rows(status)
    now = _dash(today or dt.date.today().isoformat())
    for r in rows:
        delta = (dt.date.fromisoformat(r["due_date"]) - dt.date.fromisoformat(now)).days
        r["days_to_due"] = delta
        r["overdue_days"] = -delta if delta < 0 and r["status"] == "open" else 0
        r["due_label"] = ("今天到期" if delta == 0 else f"{delta} 天后" if delta > 0
                          else f"已过期 {-delta} 天")
        if not r["calendar_covered"]:
            r["due_label"] += "（日历未覆盖，按跳周末估）"
    open_rows = [r for r in rows if r["status"] == "open"]
    return {"rows": rows,
            "next": next((r for r in open_rows if r["days_to_due"] >= 0), None),
            "overdue": [r for r in open_rows if r["days_to_due"] < 0],
            "open_count": len(open_rows), "today": now,
            "calendar_horizon": _calendar_horizon()}


def set_reminder_status(rid: int, status: str) -> dict[str, Any]:
    from app.db import engine

    if status not in REMINDER_STATUS:
        raise NoteError(f"状态只有 {'/'.join(REMINDER_STATUS)}，收到「{status}」")
    n = engine.execute(
        f"update {TABLE_REMINDER} set status = %s,"
        f" done_at = case when %s = 'done' then now() else null end, updated_at = now() where id = %s",
        (status, status, int(rid)), role="app")
    if not n:
        raise KeyError(rid)
    return list_reminders()
