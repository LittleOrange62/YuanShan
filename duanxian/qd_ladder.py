"""连板天梯 / 龙头 / 板高度 —— quantdash 口径，在 YuanShan 内从只读日线现算。

算法契约：`docs/数据加工工作指南/09-quantdash逻辑规格.md` §4.2。
逻辑来源 quantdash-ai-stock-main（PolyForm Noncommercial）—— 只重实现口径，未复制源码。

共享的池成员 / 连板推进 / 明细取数都来自 `qd_calc`，判据也共用同一份，
这样"同一个交易日给出两个涨停家数"这种事不会在本项目内部重演。
"""

from __future__ import annotations

from .pg_sources import _INDUSTRY_JOIN
from .qd_calc import (GEM, MAIN, _board_streaks, _details, _is_bj, _pool_membership,
                      _r, _tier, next_session_of)

TURNOVER_BOARD_PCT = 5.0      # 本轨扩展阈值（quantdash 全库无"换手板"这个概念）

TIER_LABEL = {7: "七板以上", 6: "六连板", 5: "五连板", 4: "四连板", 3: "三连板",
              2: "二连板", 1: "首板"}

# 逐条写明"我们和 quantdash 原实现哪里不一样"，界面要原样透出可展开。
DEVIATION = [
    {"code": "D-12", "note": "补了 tier=1 首板。原实现找不到对应行时把首板塞进「七板以上」"
                             "（TS 版则静默丢弃），两端行为相反且都错"},
    {"code": "D-13", "note": "行业/概念缺失交 NULL，不填「市场热点」「连板」这类编造分类"},
    {"code": "D-14", "note": "涨跌幅档位由当日涨停价与前收盘反推，不按代码前缀硬推"},
    {"code": "D-15", "note": "close/amount 取真实值，不写 0 与 \"-\" 占位常量"},
    {"code": "D-16", "note": "次高板无值时交 NULL，不写 0（0 与「真有一板」不可区分）"},
    {"code": "D-17", "note": "并列龙头原按未归一化的封板时间字符串比较（\"142500\"<\"93000\"），"
                             "判定本身就是错的"},
    {"code": "D-18", "note": "下一交易日走交易日历，不静默前移到"
                             "数据里最近的一条"},
    {"code": "D-19", "note": "板高度补了科创板与北交所两列；原实现的前缀白名单把它们整个忽略了"},
    {"code": "D-28", "note": "封板时间取不到就交 NULL；原实现用代码哈希编造时分秒，"
                            "而且那假时间还会污染龙头并列判定"},
]


def _prefix(code: str) -> str:
    return code.split(".")[0]


def _regime(row):
    """涨跌幅档位（%）—— 由当日涨停价与前收盘反推。

    不走两条捷径：按代码前缀猜会漏北交所 30 档（D-14）；读 `is_st` 会踩过期快照 ——
    `reference_data.security.is_st` 停在 08-27，把早已解除风险警示的票按 5% 档算涨停价，
    正是 `analytics.market_breadth` 涨停数虚高 +5 的根因。涨停价本身带着当天的档位信息。
    """
    if row is None or row.limit_up is None or not row.pre_close:
        return None
    try:
        pct = (float(row.limit_up) / float(row.pre_close) - 1) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if pct > 40:                      # 新股首日等无涨跌幅限制，不属于任何档
        return None
    return min((10, 20, 30, 5), key=lambda r: abs(r - pct))


def _one_word(row) -> bool:
    """一字板：四价相等、容差 0.001（照搬 quantdash 判据）。"""
    if row is None:
        return False
    v = [row.open, row.high, row.low, row.close]
    if any(x is None for x in v):
        return False
    v = [float(x) for x in v]
    return all(abs(a - b) < 0.001 for a, b in zip(v, v[1:]))


def _seal_times(date: str, codes: list) -> dict:
    """首次触及涨停价的分钟时刻。只有 `market_data.stock_minute` 覆盖到的日期才有。

    实测该表只有 2026-09-22 一天 → 其余全部取不到，返回空 dict，由界面显示
    "封板时间不可得"。绝不编一个时分秒上去。
    """
    if not codes:
        return {}
    from . import pg
    df = pg.fetch_df(
        """
        select m.security_code, min(m.dt)::time as t
        from market_data.stock_minute m
        join market_data.stock_daily d
          on d.security_code = m.security_code and d.trade_date = m.dt::date
        where m.dt::date = %s and m.period = 1
          and m.security_code = any(%s)
          and d.limit_up is not null and m.high >= d.limit_up - 0.005
        group by 1
        """,
        (date, codes))
    if df is None:
        return {}
    return {r.security_code: r.t.strftime("%H:%M") for r in df.itertuples() if r.t}


def _industries(date: str, codes: list) -> dict:
    from . import pg
    df = pg.fetch_df(
        f"""select sd.security_code, hy.name
            from market_data.stock_daily sd
            {_INDUSTRY_JOIN}
            where sd.trade_date = %s and sd.security_code = any(%s)
              and hy.name is not null""",
        (date, codes))
    if df is None:
        return {}
    return dict(zip(df.security_code, df.name))


def compute(date: str):
    """某场的天梯 + 龙头 + 板高度。该日无涨停 → tiers 空、leader None。"""
    from . import qd_calc
    start, _, _ = qd_calc._window(date)
    members = _pool_membership(start, date)
    if date not in members:
        return None
    cur = _board_streaks(members)[date]
    codes = sorted(cur)
    details = _details(set(codes), [date])
    seal = _seal_times(date, codes)
    industry = _industries(date, codes)

    rows = []
    for c in codes:
        row = details.get((date, c))
        turn = _r(row.turnover_rate_percent, 4) if row and row.turnover_rate_percent is not None else None
        ow = _one_word(row)
        rows.append({
            "security_code": c,
            "stock_name": getattr(row, "name", None),
            "board_tier": _tier(cur[c]), "board_count": cur[c],
            "industry_name": industry.get(c),
            "concept_name": None,
            "limit_up_pct_regime": _regime(row),
            "pct_change_actual": _r(row.pct_change, 2) if row and row.pct_change is not None else None,
            "is_one_word": ow,
            # 换手板是本轨扩展 → 界面要标注"这不是 quantdash 的口径"
            "is_turnover_board": None if turn is None else (not ow and turn >= TURNOVER_BOARD_PCT),
            "turnover_rate_pct": turn,
            "first_seal_time": seal.get(c),
            "seal_time_available": c in seal,
            "price_close": _r(row.close, 3) if row and row.close is not None else None,
            "amount_yuan": None if row is None or row.amount_yuan is None else float(row.amount_yuan),
        })

    height = max(cur.values())
    tiers = []
    for t in range(7, 0, -1):
        in_t = [m for m in rows if (m["board_count"] >= 7 if t == 7 else m["board_count"] == t)]
        tiers.append({"board_tier": t, "tier_label": TIER_LABEL[t],
                      "stock_count": len(in_t), "is_highest_tier": _tier(height) == t,
                      "members": in_t})

    return {
        "trade_date": date, "tiers": tiers,
        "leader": _leader(date, cur, rows, details, members),
        "board_height": _height(rows),
        "seal_time_available": any(m["seal_time_available"] for m in rows),
        "spec_version": "qd-1", "source": "quantdash-logic+local",
        "deviation": DEVIATION,
    }


def _leader(date, cur, rows, details, members):
    height = max(cur.values())
    lead = [m for m in rows if m["board_count"] == height]
    if not lead:
        return None

    with_seal = [m for m in lead if m["first_seal_time"]]
    if with_seal:                                   # quantdash 的原口径：最早封板者胜
        chosen, tb = min(with_seal, key=lambda m: m["first_seal_time"]), "seal_time"
    else:
        cand = sorted(lead, key=lambda m: (-(m["amount_yuan"] or -1), m["security_code"]))
        chosen = cand[0]
        tb = "code_asc" if (len(cand) > 1 and cand[0]["amount_yuan"] == cand[1]["amount_yuan"]) \
            else "amount_no_seal_time"

    nxt = next_session_of(date)
    ns = _streaks_at(nxt)
    continued = None if ns is None else ns.get(chosen["security_code"], 0) > height
    nclose = nopct = None
    if nxt:
        row = _details({chosen["security_code"]}, [nxt]).get((nxt, chosen["security_code"]))
        c0 = details.get((date, chosen["security_code"]))
        if row and c0 and c0.close:
            nclose = _r((float(row.close) - float(c0.close)) / float(c0.close) * 100, 2)
            if row.open is not None:
                nopct = _r((float(row.open) - float(c0.close)) / float(c0.close) * 100, 2)

    lower = [m["board_count"] for m in rows if m["board_count"] < height]
    cont = bool(continued)
    label, code = _status(cont, chosen["is_one_word"], nclose, len(lead), height)
    return {
        "trade_date": date,
        "leader_security_code": chosen["security_code"], "leader_stock_name": chosen["stock_name"],
        "leader_board_count": height, "leader_count": len(lead),
        "second_highest_board": max(lower) if lower else None,
        "three_plus_count": sum(1 for b in cur.values() if b >= 3),
        "continued_count": (sum(1 for c, b in ns.items() if b > height) if ns else None),
        "leader_tie_break": tb, "leader_is_one_word": chosen["is_one_word"],
        "leader_continued": continued,
        "leader_next_open_pct": nopct, "leader_next_close_pct": nclose,
        "leader_status_label": label, "leader_status_code": code,
        "deviation": DEVIATION,
    }


def _streaks_at(date):
    """某日各票连板数；取不到（未收盘/无数据）交 None，不当 0。"""
    if not date:
        return None
    from . import qd_calc
    start, _, _ = qd_calc._window(date)
    m = _pool_membership(start, date)
    return _board_streaks(m).get(date) if date in m else None


def _status(continued, one_word, nclose, lead_n, height):
    """龙头状态 9 值，if-elif 顺序短路（09 §4.2）。"""
    if nclose is None:
        if lead_n >= 2 and height >= 4:
            return "高标抱团", "high_leader_cluster"
        if one_word:
            return "一字观察", "one_word_watch"
        return "待次日确认", "await_confirmation"
    if one_word and continued:
        return "一字加速", "one_word_acceleration"
    if continued and nclose >= 0:
        return "强势晋级", "strong_continuation"
    if nclose >= 3:
        return "断板承接", "accepted_after_break"
    if nclose >= 0:
        return "高位分歧", "high_level_divergence"
    if nclose > -5:
        return "分歧转弱", "weakening"
    return "退潮承压", "retreat_pressure"


def _height(rows):
    """板高度分市场。原实现只看主板与创业板，科创板/北交所整个被忽略（D-19）。"""
    def top(pred):
        bs = [m["board_count"] for m in rows if pred(_prefix(m["security_code"]))]
        if not bs:
            return None, [], []
        hi = max(bs)
        hit = [m for m in rows if m["board_count"] == hi and pred(_prefix(m["security_code"]))]
        return hi, [m["stock_name"] for m in hit], [m["security_code"] for m in hit]

    main_hi, main_names, main_codes = top(lambda p: p.startswith(MAIN))
    gem = top(lambda p: p.startswith(GEM))
    star = top(lambda p: p.startswith("688") or p.startswith("689"))
    bj = top(_is_bj)
    lower = [m["board_count"] for m in rows
             if _prefix(m["security_code"]).startswith(MAIN) and m["board_count"] != main_hi]
    return {
        "main_board_highest": main_hi, "main_board_highest_names": main_names,
        "main_board_highest_codes": main_codes,
        "main_board_second_highest": max(lower) if lower else None,
        "chinext_highest": gem[0], "chinext_highest_names": gem[1],
        "star_highest": star[0], "star_highest_names": star[1],
        "bj_highest": bj[0], "bj_highest_names": bj[1],
        "deviation": DEVIATION,
    }
