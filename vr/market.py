"""市场总览数据层 —— 市场情绪 + 板块资金流（板块/大盘级公开数据，不涉个股推荐）。

省流量：全站共享一份缓存（TTL 默认 5 分钟），多个用户/多次打开只抓一次；
盘中 5 分钟刷新足够，非交易时段数据本就不变。数据源全免费、无 key。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone, timedelta

import astock
import gstock

from duanxian import ttlcache

BEIJING = timezone(timedelta(hours=8))
_TTL = ttlcache.DEFAULT_TTL             # 成功结果：5 分钟；全站共享，省数据源压力
_FAIL_TTL = ttlcache.DEFAULT_FAIL_TTL   # 空/失败：60 秒内不再重复出网（三条理由见 ttlcache 模块头）


def _cached(key: str, fn, valid=bool):
    """出网取数的 TTL 缓存。**实现只住 `duanxian/ttlcache.py` 一份**，这里只加 `market:` 键前缀。

    ⚠️ 不要再在这里写第二套缓存：首页两处组件各要一遍同一个接口
    （overview 就被 `SummaryBoard` 与 `MarketPanorama` 各打一次），两份实现迟早分叉成"两边 TTL 不一样"。
    """
    return ttlcache.cached('market:' + key, fn, valid=valid)


def cache_status(key: str) -> dict:
    """这一路外网数据的最近一次真实情况（`ok` / 是不是空 / 原因 / 时刻 / 缓存多久）。

    读侧要老实说"这次没读到"就用它 —— 空数组直接交给界面等于把"取不到"说成"今天没有"。
    """
    return ttlcache.status('market:' + key)


def _num(v) -> int:
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return 0


def _sentiment() -> dict:
    """市场情绪：涨跌家数/涨停跌停/活跃度 + 大盘宽度、题材投机（客观数据机械分档）。

    ⚠️ 分档判据**不在这里写**：唯一的实现在 `app/compute/sentiment_daily.py`
    （`breadth_level` / `theme_level`），落库与页面现算共用同一份。
    原先这里有一份独立的 if 链，那就是"页面说今天偏强、折线上同一天是中性"的成因 ——
    阈值一改只改一处，另一处不会跟着改，而这种不一致在图上看不出来。
    """
    try:
        # akshare 惰性导入（同 astock 模式）：未装时降级返回空，不挡整个服务启动
        df = astock._akshare().stock_market_activity_legu()
        d = {row["item"]: row["value"] for _, row in df.iterrows()}
    except Exception:
        return {}
    from app.compute import sentiment_daily as sd

    up, down, flat = _num(d.get("上涨")), _num(d.get("下跌")), _num(d.get("平盘"))
    zt, zt_real = _num(d.get("涨停")), _num(d.get("真实涨停"))
    dt_, dt_real = _num(d.get("跌停")), _num(d.get("真实跌停"))
    breadth, breadth_score = sd.breadth_level(up, down)
    speculation, theme_score = sd.theme_level(zt_real)
    return {
        "up": up, "down": down, "flat": flat,
        "zt": zt, "zt_real": zt_real, "dt": dt_, "dt_real": dt_real,
        "active": str(d.get("活跃度", "")),
        "breadth": breadth, "speculation": speculation,
        "breadth_score": breadth_score, "theme_score": theme_score,
        "date": str(d.get("统计日期", "")),
    }


#: 折线要多少场（用户 2026-10-06 定的："20 日差不多吧"）。多取 10 场余量给"锐化趋势"算斜率。
SERIES_SESSIONS = 30


def _sentiment_series() -> dict:
    """近若干场的两档序列（读 `analytics.market_sentiment_daily`，**不现抓接口**）。

    现抓只有"此刻"一个点，画不出折线 —— 这张表是 2026-10-06 才建的，
    之前的场次由 `market_breadth` 回算（`source='local'`，涨停数是单边判据，
    与乐咕的「真实涨停」会有几只出入）。每个点都带着 `source`，图上要能看出来。
    """
    from app.db import engine as sql_engine
    from app.compute import sentiment_daily as sd

    try:
        rows = sql_engine.query(
            """select trade_date, up_count, down_count, ratio, limit_up_real,
                      breadth_level, breadth_score, theme_level, theme_score, source
                 from analytics.market_sentiment_daily
                where spec_version = %s
                order by trade_date desc limit %s""", (sd.SPEC_VERSION, SERIES_SESSIONS))
    except Exception as exc:  # noqa: BLE001  读不出序列就说读不出，不给空数组当成"这几天没情绪"
        return {"available": False,
                "reason": "折线数据读不出来：%s: %s" % (type(exc).__name__, str(exc)[:120])}
    rows = list(reversed(rows))
    if not rows:
        return {"available": False,
                "reason": "`analytics.market_sentiment_daily` 里还没有任何一场 —— "
                          "跑一次 `python -m app.compute.sentiment_daily --backfill` 建历史"}
    return {
        "available": True,
        "points": [{"date": str(r["trade_date"]), "up_count": r["up_count"],
                    "down_count": r["down_count"], "ratio": _f(r["ratio"]),
                    "limit_up_real": r["limit_up_real"],
                    "breadth_level": r["breadth_level"], "breadth_score": _f(r["breadth_score"]),
                    "theme_level": r["theme_level"], "theme_score": _f(r["theme_score"]),
                    "source": r["source"]} for r in rows],
        "basis": ("0 轴是用户指定的：大盘宽度以「中性=0」，题材投机以「普通与活跃的中间=0」"
                  "（编码 ±0.5/±1.5）。档位判据只有一份实现（app/compute/sentiment_daily.py），"
                  "页面现算的今日值与折线共用它。"),
        "latest_date": str(rows[-1]["trade_date"]),
        "sources": {"legu": sum(1 for r in rows if r["source"] == "legu"),
                    "local": sum(1 for r in rows if r["source"] == "local")},
    }


def _f(v):
    return None if v is None else float(v)


def _sector_flow_stored() -> dict:
    """首页「板块资金趋势榜」的**逐日累计**那一格（读库，判据只在 `duanxian/sector_flow.py` 一份）。

    与 `_sectors()`（现抓的今日即时榜）分开的理由写在 `live_is_stored()` 里：
    一个是盘中截面，一个是逐日收盘落库，混成同一列等于把两个时刻的数加起来。
    """
    try:
        from duanxian import sector_flow
        out = dict(sector_flow.aggregate())
        out['live_stored_today'] = sector_flow.live_is_stored()
        out['rows'] = out.get('rows') or []
        return out
    except Exception as exc:  # noqa: BLE001  取不出累计就明说，不许回退成"没有资金流"
        return {"available": False, "rows": [],
                "reason": "逐日累计读不出来：%s: %s" % (type(exc).__name__, str(exc)[:140])}


def _sectors() -> list[dict]:
    """行业资金流（按净额降序）。不含领涨股等个股字段。"""
    try:
        f = astock._akshare().stock_fund_flow_industry(symbol="即时")
        f = f.sort_values("净额", ascending=False)
    except Exception:
        return []
    out = []
    for _, row in f.iterrows():
        out.append({
            "name": str(row["行业"]),
            "pct": round(float(row.get("行业-涨跌幅", 0) or 0), 2),
            "net": round(float(row.get("净额", 0) or 0), 2),
            "inflow": round(float(row.get("流入资金", 0) or 0), 2),
            "outflow": round(float(row.get("流出资金", 0) or 0), 2),
            "firms": _num(row.get("公司家数")),
        })
    return out


def get_overview() -> dict:
    """市场情绪（含近几场折线）+ 板块资金（含逐日累计）。资金轮动由前端从 sectors 头尾取。

    `sentiment_series` 读库（`analytics.market_sentiment_daily`），其余是现抓接口 ——
    分开的理由：**现抓的那部分关掉页面就没了**，画不出趋势。
    """
    def build():
        return {
            "sentiment": _sentiment(),
            "sentiment_series": _sentiment_series(),
            "sectors": _sectors(),
            "sector_flow": _sector_flow_stored(),
            "updated": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
        }
    return _cached("overview", build, valid=lambda v: bool(v.get("sentiment") or v.get("sectors")))


def _emotion() -> dict:
    """短线情绪：连板梯队 / 最高连板 / 炸板率 / 封板率 / 晋级率 / 涨跌停家数 + 2 板以上清单。

    母体＝**本地库** `analytics.limit_up_pool_daily`（判据只住写侧
    `app/compute/limit_up_pool.py`，读侧 `duanxian/limit_up_board.py` 只读行）。
    2026-10-26 之前这一块的母体是每次请求现抓东财 push2ex 四个池子。
    库里没有这一场的行就如实说"读不到、该跑哪一步"，**不回退去抓在线池子顶数** ——
    回退等于"加工没跑也看着有数"，而这一屏的数是给复盘用的。
    """
    # 目标日两步定，顺序不能反（与首板页共用 firstboard.data_session，规则只有一处答案）：
    #   ① 日历上"最近一个已收盘交易日"  ② 库里实际有日线的那一场 → 显示②，缺的那场说出来。
    # ⚠️ 以前这里是"从今天往前回溯 8 天，第一个非空的池子算谁"。2026-09-26 实测东财池子
    #   对 09-23/09-24/**09-25（中秋休市）**/09-26（周六）四天都回 51~52 家、首封时间一律
    #   09:25 —— 它根本不校验日期有效性，所以"第一个非空"必然把休市日照单贴上去。
    #   改成过日历之后母体又落进了库里，这条判据现在有两条腿：**日历判哪天、库判有没有数**。
    import firstboard  # 函数内导入：firstboard 顶部 import market，避免循环依赖

    want, gap = firstboard.data_session()
    if not want:
        return {}
    from duanxian import limit_up_board
    board = limit_up_board.board_on(want)
    if not board.get("available"):
        # 库里这一场没有行（加工段没跑 / 这一天的日线还没到）：把"该跑哪一步"交出去，
        # 不拿别的日子顶、也不回头抓在线池子。`lianban_stocks` 交空数组，页面按 available 分支念。
        return {"date": want, "available": False,
                "reason": board.get("reason") or "库里这一场读不到",
                "caliber_note": gap or "", "lianban_stocks": []}

    sealed = board["sealed"]                              # 收盘封住的那些行（母体的一半）
    boards = [r["consec"] or 1 for r in sealed]           # 每只连板数（本地日线数出来的）
    lianban = [b for b in boards if b >= 2]               # 2 板及以上（连板）
    # 连板梯队：2/3/4/5+ 各多少家（5 代表 5 板及以上），只保留有家数的档
    tiers = Counter(min(b, 5) for b in lianban)
    ladder = [{"boards": b, "count": tiers[b], "plus": b >= 5} for b in sorted(tiers)]

    # 顺序（用户 2026-10-09 点名改）：**先按连板数降序，同连板按首次封板时间从先到后**。
    # 原来是"同连板按成交额降序"，那只会把大盘子顶到前面，回答不了"今天是谁先封住的"。
    # 首封缺数（这一场没有分钟线、源站那一份也没有）排到本档最后，**不拿 0 当成"最早"**；
    # 第一键用的是**分钟级**首封（显示到分钟，看着并列的两行按自算那根分钟的真实先后走），
    # 真同一分钟的（集合竞价那批常见）第三键给强度分 —— 也让同一场刷新两次不会因行序抖动而互换。
    # 产品定位（2026-07-05）：客观榜单，不推荐 / 不预测 / 不评分。
    from duanxian import seal_strength

    # 「概念」这一列读本地库（reference_data.sector_member，板块类型=概念，窄题材在前）：
    # 这一列的名字一直叫「概念」，而原来是东财池的行业板块名（如"照明设备"）—— 名实不符，
    # 现在两处都从库里出，取不到交 None，界面显示 —，不等于"这只票没有题材"（北证基本无行）。
    from duanxian import pg_sources
    concepts = pg_sources.concepts_by_code([r["code"] for r in board["lianban"]])

    rows = []
    for p in board["lianban"]:
        st = seal_strength.seal_strength(p.get("order_amount"), p.get("float_cap"),
                                         p.get("reopen_count"), p.get("seal_raw"))
        parts = st["parts"]
        con = concepts.get(str(p["code"])) or {}
        note = ("封单占流通 %s%% → %.2f｜开板 %s 次 → %.2f｜首封 %s → %.2f%s%s" % (
            parts.get("fund", {}).get("ratio_pct", "缺"), parts.get("fund", {}).get("band", 0),
            parts.get("reopen", {}).get("opens", "缺"), parts.get("reopen", {}).get("band", 0),
            p.get("seal_time") or "缺", parts.get("first", {}).get("band", 0),
            "｜这一场没有分钟线，首封取的是源站秒级值" if p.get("seal_basis") == "source" else "",
            "｜缺的项目：只按有数的几项归一" if st["missing"] else ""))
        rows.append({
            "code": p["code"], "name": p["name"],
            "boards": p["consec"] or 1,
            "price": p.get("close"),                        # 样本日（不是今天）的收盘价
            "pct": p.get("pct"),                             # 样本日涨幅 %
            "amount": p.get("amount"),                       # 成交额,元
            "float_cap": p.get("float_cap"),                 # 流通市值,元
            "float_cap_basis": p.get("float_cap_basis"),     # 源站原值 / 当日日线反推
            "industry": con.get("concepts"),
            "concept_count": con.get("concept_count"),
            # 首次封板：库里自算（1 分钟线第一根还封在档位上的分钟）。**这一场没有分钟线时
            # 退回源站秒级值并在悬停里标出来**；两边都没有就是空串（页面 —），0 不是 00:00。
            "seal_time": p.get("seal_time") or "",
            "seal_raw": p.get("seal_raw"),                   # 排序用的原始 HHMMSS（页面不显示）
            "seal_basis": p.get("seal_basis"),
            "src_seal_time": p.get("src_seal_time"),         # 源站那一份，只用来对照
            "src_seal_raw": p.get("src_seal_raw"),
            "open_times": p.get("reopen_count"),             # 自算开板次数（强度输入之一）
            "src_reopen": p.get("src_reopen"),               # 源站开板次数：两把尺，一起给
            "at_limit_minutes": p.get("at_limit_minutes"),   # 封在档位上的分钟根数
            "minute_bars": p.get("minute_bars"),
            "board_kind": p.get("board_kind"),
            "streak_start": p.get("streak_start"),
            "seal_score": st["score"],                       # 封板强度 0~100；有数的少于两项 → None
            "seal_note": note,
            # 源站「几天几板」原文：本地那一份是「几天几板」那一列（board_span，另一把尺），
            # 这里留着只用来当面比，不覆盖本地的板数（与首板分析同一处理方式）。
            "src_high_days": p.get("src_high_days"),
            "reason": p.get("reason") or "",                 # 涨停原因题材串（源站编辑归因）
        })
    lianban_stocks = sorted(rows, key=lambda x: (-x["boards"], x["seal_raw"] or 99999999,
                                                  -(x["seal_score"] if x["seal_score"] is not None else -1)))
    # 「几天几板」：本地日线数出来的涨停统计（用户 2026-10-09 指定回溯 30 个交易日）。
    # 判据与 SQL 只住 `duanxian/board_span.py`，这里只做接法与"两边对不上时点名"。
    from duanxian import board_span
    bs = board_span.boards_by_code([r["code"] for r in lianban_stocks], want)
    missing_daily = []
    for r in lianban_stocks:
        got = bs.get(str(r["code"])[:6]) or {}
        r["days_boards"] = got.get("label")            # "5天4板"；None = 这一轮没数出来（不是 0）
        r["board_days"] = got.get("days")
        r["board_ct"] = got.get("boards")
        r["board_dates"] = got.get("sealed_dates")
        if got.get("boards") is None and got.get("rows") is not None:
            # 母体判成封住（半分容差），board_span 那一把更严（0.001）却没数到 ⇒ 两把容差在这只票上分叉
            missing_daily.append(f'{r["name"]}({r["code"]})')
    boards_warn = ''
    if missing_daily:
        boards_warn = ('这几天几板没数出来：' + '、'.join(missing_daily[:6])
                       + ('…' if len(missing_daily) > 6 else '')
                       + ' —— 母体判成今天封住了，按「几天几板」那把更严的容差却没算进这一轮。'
                         '别把它读成"没有板"。')
    elif not bs:
        boards_warn = '本地日线读不到（库没连或这场没数）：这一列整列空着，不是这批票都没板。'

    # 「首板日大盘情绪」（用户 2026-10-10 点名）：这一轮最早那个封板日 —— 就是上面
    # `board_dates[0]` —— 那天大盘是什么档。判据、口径文字与取数只住
    # `duanxian/pg_sources.py`（`sentiment_on_dates` + `first_board_view`），这里只做接法。
    fb_days = [r["board_dates"][0] for r in lianban_stocks if r.get("board_dates")]
    fb_sent = pg_sources.sentiment_on_dates(fb_days) if fb_days else {}
    fb_failed = bool(fb_sent.get("failed"))
    no_span, no_row = [], []
    for r in lianban_stocks:
        d0 = (r.get("board_dates") or [None])[0]
        cell = pg_sources.first_board_view(d0, None if d0 is None else fb_sent.get(d0))
        r.update(cell)
        if d0 is None:
            no_span.append(f'{r["name"]}({r["code"]})')
        elif fb_failed or d0 not in fb_sent:
            no_row.append(d0)
    fb_warn = ''
    if fb_failed:
        fb_warn = '大盘情绪整列没读到：库不通或那条查询坏了（不是这批票的首板日都没情绪）。'
    elif no_row:
        fb_warn = ('这些首板日在 analytics.market_sentiment_daily 里没有行：'
                   + '、'.join(sorted(set(no_row))[:6]) + ('…' if len(set(no_row)) > 6 else ''))
    elif no_span:
        # 封板日没数出来 ⇒ 首板日就无从定位；这一格空着的原因在「几天几板」那一列，不重复报
        fb_warn = ''

    # 「节点分析」：与首板页读的是同一份共用块（`duanxian/node_view.py`），口径文字与"判不了"
    # 那句说明都由那一份交出，这里只做接法 —— 两处各写一遍就会出现"同一格两种说法"。
    from duanxian import node_view
    node_meta = node_view.attach(rows, want)
    node_warn = node_meta.pop('warn', '')

    zt_count, zb_count = board["sealed_count"], board["broken_count"]
    attempts = zt_count + zb_count                       # 尝试涨停 = 封住 + 触板没封住
    seal_rate = round(zt_count / attempts, 3) if attempts else None      # 封板率
    break_rate = round(zb_count / attempts, 3) if attempts else None     # 炸板率
    # 晋级率＝今日 2 板+（＝昨封今又封）÷ 上一场封住家数。分母读**同一张表**的上一场，
    # 不拿东财"昨涨停池"顶：顶上来的就是两套口径混进一个比率里。
    prev_n = board.get("prev_sealed")
    promotion_rate = round(len(lianban) / prev_n, 3) if prev_n else None

    notes = [gap] if gap else []
    if board.get("seal_backfilled"):
        notes.append(f'{board["seal_backfilled"]} 只的首封取的是源站秒级值：这一场本地分钟线没读到'
                     '（2026-08-14 之前的场次、以及没采到分钟线的那几天）—— 同一个列名底下两把尺，'
                     '所以逐行标了出处')
    if board.get("seal_none"):
        notes.append(f'{board["seal_none"]} 只算不出首封时刻：分钟线里没有任何一根收盘封在档位上，'
                     '源站那一份也没有 ⇒ 显示 —，不是 00:00')

    return {
        "date": want,
        "available": True,
        "zt_count": zt_count,
        # 家数与比率的母体从 2026-10-26 起就是这张库表，"在线池 vs 库里"那句对照作废：
        # 库里就是唯一出处，两侧数的差写在 caliber 那段实测里（含 2026-10-23 的两组数）。
        "caliber_note": "；".join(x for x in notes if x),
        "caliber": board.get("caliber"),
        "seal_rule": board.get("seal_rule"),
        "coverage": board.get("coverage"),
        "gap_texts": board.get("gap_texts"),
        "spec_version": board.get("spec_version"),
        "rows_total": len(board.get("rows") or []),
        "dt_count": board.get("dt_count"),
        "zb_count": zb_count,
        "prev_date": board.get("prev_date"),
        "prev_sealed": prev_n,
        "max_boards": max(boards) if boards else 0,
        "lianban_count": len(lianban),
        "ladder": ladder,
        "lianban_stocks": lianban_stocks,
        # 「封板强度」这一列的算法说明与口径版本：**判据只住 `duanxian/seal_strength.py`，
        # 文字由那一份现拼**，页面悬停直接显示后端给的这段（前端不许另写一套分档）。
        "seal_spec": seal_strength.spec_text(),
        "seal_spec_version": seal_strength.SPEC_VERSION,
        # 「几天几板」：口径文字与窗口都由 `duanxian/board_span.py` 交出，页面不许自己复述规则
        "days_boards_rule": board_span.RULE_NOTE,
        "days_boards_window": board_span.WINDOW_SESSIONS,
        "days_boards_break_days": board_span.BREAK_DAYS,
        "days_boards_warn": boards_warn,
        # 「首板日大盘情绪」：口径文字与缺档说明都由 `pg_sources` 交，页面不自己复述（同上面两列）
        "first_board_rule": pg_sources.FIRST_BOARD_RULE_NOTE,
        "first_board_warn": fb_warn,
        "first_board_no_span": len(no_span),
        # 「节点分析」：判据只住 `app/compute/board_node.py`，读法与措辞只住 `duanxian/node_view.py`
        "node": node_meta,
        "node_rule": node_view.RULE_NOTE,
        "node_warn": node_warn,
        "seal_rate": seal_rate,
        "break_rate": break_rate,
        "promotion_rate": promotion_rate,
    }


def get_short_term_emotion() -> dict:
    """短线情绪（含缓存，5 分钟）。"""
    return _cached("emotion", _emotion)


def get_turnover_top() -> dict:
    """全市场**换手率**榜 Top20（客观公开榜单，含缓存 5 分钟）。

    09-27 用户裁定：这一屏原来是成交额榜，改成"全市场换手率 TOP20"并加 换手率 / 概念板块 两列。
    两件事必须一起改对：
    1. **排序键由取数侧决定**（`fid=f8`）。先按成交额取前 20 名、再在前端按换手率重排，
       得到的是"成交额前 20 里的换手率榜"，根本不是换手率榜 —— 这类错不报错、看着还挺像。
    2. 概念板块来自本地参考数据（`pg_sources.concepts_by_code`），与行情源无关；
       取不到的票交 `None`，界面显示 `—`，不等于"这只票没有题材"（北交所实测基本无概念行）。
    """
    def build():
        import math
        from duanxian import pg_sources
        rows = astock.market_turnover_rank(20, fid="f8")
        rows = [r for r in rows if isinstance(r.get("turnover"), (int, float))
                and math.isfinite(r["turnover"]) and r["turnover"] > 0]
        rows.sort(key=lambda r: r["turnover"], reverse=True)
        con = pg_sources.concepts_by_code([r.get("code") for r in rows])
        for r in rows:
            c = con.get(str(r.get("code"))) or {}
            r["concepts"] = c.get("concepts")
            r["concept_count"] = c.get("concept_count")
        missing = sum(1 for r in rows if not r.get("concepts"))
        return {
            "reason": ("" if quote_day == datetime.now(BEIJING).strftime("%Y-%m-%d") else f"参考行情日为 {quote_day}，请勿当作今日排行") if rows else "尚无有效换手率，暂不排名",
            "quote_date": quote_day,
            "stocks": rows,
            "rank_basis": "turnover",
            "concept_missing": missing,
            "concept_note": ("概念板块取自本地 reference_data.sector_member（板块类型=概念，按名字去重），"
                             "一只票可属多个概念，列前 3 个（成员数少的在前＝窄题材优先）并给出总数；"
                             "显示 `—` 是库里没有这一档，不等于它不属于任何题材。"
                             + (f" 本榜 {missing} 只没有概念行。" if missing else "")),
            "updated": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
        }
    from duanxian import trade_calendar
    quote_day = trade_calendar.quote_trade_day()
    phase = trade_calendar.session_phase(datetime.now(BEIJING), quote_day)
    if phase["phase_key"] in {"auction", "wait", "unknown"}:
        # 这句话原来不管三七二十一都写"竞价阶段"，夜里/非交易日打开就成了一句假原因
        # （用户看到的是一句和时钟对不上的解释）。把后端判定的那一段一起交出去。
        return {"stocks": [], "updated": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
                "reason": f"现在是「{phase.get('label') or phase['phase_key']}」，"
                          "这张榜按当日累计换手率排名，只在连续竞价时段展示（不拿上一场硬排）"}
    return _cached("turnover_top:" + str(quote_day) + ":" + phase["phase_key"], build, valid=lambda v: bool(v.get("stocks")))


def get_global_indices() -> list[dict]:
    """全球指数快照（美股 / 港股）。成功缓存 5 分钟，空或炸只缓存 60 秒（见 `_cached`）。

    ⚠️ 2026-10-25 实测：这一路对端不通时每次要 12.4 秒、只回 11 字节的空数组，
    而旧写法"空结果不缓存"把它变成**每个访客每一发都重付一遍超时**（首页常年空块 + 占连接）。
    界面要知道"这次是没读到，不是今天没有"，读 `cache_status('global_indices')`。
    """
    return _cached("global_indices", gstock.global_indices, valid=bool)
