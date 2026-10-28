"""板块周期 —— quantdash 口径，在 YuanShan 内从只读板块日线现算。

契约：`docs/数据加工工作指南/09-quantdash逻辑规格.md` §4.3。
逻辑来源 quantdash-ai-stock-main（PolyForm Noncommercial）—— 只重实现口径，未复制源码。

⚠️ 这套**不是**本轨 `analytics.ignition` 的五档生命周期阶段。quantdash 的"板块周期"
根本没有阶段枚举，它回答的是"领涨板块换了几手、谁连庄最久、强度在增还是减"。
两块各自独立呈现，不许合并成一张表。
"""

from __future__ import annotations

from . import pg
from .qd_calc import _r

TOP_N = 24          # 参与排名的板块数（quantdash Python/TS 版口径 24）
MATRIX_KEEP = 8     # 矩阵每行只留当日涨幅前 8
WINDOW = 5          # 持续性统计窗口（交易日）

DEVIATION = [
    {"code": "D-20", "note": "取样统一 24 个、排序键 (涨幅 desc, 名称 asc)。原实现自己的 "
                             "MCP 版取 20 且无名称 tie-break，与它的 Python/TS 版不一致"},
    {"code": "D-21", "note": "前三甲出现次数缺数据记 0，不记 1（原实现默认 1，把无数据"
                             "当成「出现过一天」）"},
    {"code": "D-22", "note": "最长连庄板块算不出时交 NULL，界面显示「无连庄板块」；"
                             "原实现回落成当前领涨板块，等于编造「最强连庄」"},
    {"code": "D-41", "note": "单板块取数失败不静默 continue 后照样出榜；universe 异常时整场判不可用"},
]


def _ranking(dates: list) -> dict:
    """{date: [ {sector_code, sector_name, sector_type, day_rank, pct_change,
                up_count, down_count, member_count, universe} ]}，只留前 MATRIX_KEEP。"""
    df = pg.fetch_df(
        f"""
        with ranked as (
          select sd.trade_date::text as d, sd.sector_code, si.name as sector_name,
                 si.sector_type, sd.pct_change, sd.up_count, sd.down_count, sd.member_count,
                 row_number() over (partition by sd.trade_date
                                    order by sd.pct_change desc nulls last, si.name asc) as rk,
                 count(*) over (partition by sd.trade_date) as universe
          from market_data.sector_daily sd
          join reference_data.sector_info si using (sector_code)
          where sd.trade_date = any(%s::date[])
            and si.sector_type in ('概念', '行业')
            -- 880xxx 那批通达信指数式板块在 sector_info 里 name 直接就是代码（无名占位）。
            -- 不滤掉的话"领涨板块"会显示成 `880046` 这种字符串，看着像数据、其实是垃圾。
            and si.name is not null and btrim(si.name) <> '' and si.name <> si.sector_code
            and si.name <> split_part(si.sector_code, '_', 2)
        )
        select * from ranked where rk <= {MATRIX_KEEP} order by d desc, rk asc
        """,
        (dates,))
    if df is None:
        return {}
    out: dict = {}
    for r in df.itertuples():
        out.setdefault(r.d, []).append({
            "sector_code": r.sector_code, "sector_name": r.sector_name,
            "sector_type": r.sector_type, "day_rank": r.rk,
            "pct_change": _r(r.pct_change, 2), "up_count": r.up_count,
            "down_count": r.down_count, "member_count": r.member_count,
            "universe": r.universe,
        })
    return out


def compute(date: str):
    """某场的轮动矩阵（含最近 WINDOW 个场次）+ 持续性统计。"""
    cal = pg.fetch_df(
        """select calendar_date::text as d from reference_data.trade_calendar
           where is_open and calendar_date <= %s order by 1 desc limit %s""",
        (date, WINDOW))
    if cal is None or not len(cal):
        return None
    dates = list(cal.d)
    ranked = _ranking(dates)
    if date not in ranked or not ranked[date]:
        return None

    # universe 突变 = 上游那一场少了一整类板块。拿它跟别的日子比排名就是拿
    # 两个不同母体算"连庄"，宁缺毋假 —— 整场判不可用，而不是硬算一个看着可信的数。
    sizes = {d: {r["universe"] for r in ranked.get(d, [])} for d in dates}
    today_u = sorted(sizes[date])
    if len(today_u) != 1:
        return None
    drift = [d for d in dates if ranked.get(d) and sorted(sizes[d]) != today_u]
    if drift:
        return {"trade_date": date, "available": False, "drift_dates": drift,
                "universe": today_u[0], "deviation": DEVIATION,
                "reason": (f"最近 {WINDOW} 场里有 {len(drift)} 场的板块母体与本场不一致"
                           f"（{', '.join(drift)}）：排名与连庄天数跨场不可比，宁可不给")}

    matrix = [{"trade_date": d, "universe": today_u[0],
               "sector_types": sorted({r["sector_type"] for r in ranked[d]}),
               "items": ranked[d]} for d in dates if ranked.get(d)]

    return {"trade_date": date, "available": True, "matrix": matrix,
            "persistence": _persistence(dates, ranked),
            "universe": today_u[0],
            "spec_version": "qd-1", "source": "quantdash-logic+local",
            "deviation": DEVIATION}


def _persistence(dates, ranked):
    """`dates[0]` 是最新一场。窗口 = 日期升序的末 WINDOW 天。"""
    asc = sorted(dates)
    win = asc[-WINDOW:]
    latest = ranked[dates[0]][0]
    name = latest["sector_name"]

    streak = 0
    for d in reversed(win):                      # 自最新日向前回溯
        rows = ranked.get(d) or []
        if rows and rows[0]["sector_name"] == name:
            streak += 1
        else:
            break

    appear: dict = {}
    for d in win:
        for r in (ranked.get(d) or [])[:3]:
            appear[r["sector_name"]] = appear.get(r["sector_name"], 0) + 1

    prev = ranked.get(win[-2])[0] if len(win) > 1 and ranked.get(win[-2]) else None
    delta = None
    if prev and prev["sector_name"] == name and prev["pct_change"] is not None \
            and latest["pct_change"] is not None:
        # 只有两日领涨是同一个板块时才有"强度变化"这个含义；否则 NULL →「新晋领涨」
        delta = _r(latest["pct_change"] - prev["pct_change"], 2)

    strongest = None
    if appear:
        best = max(appear.values())
        strongest = sorted(n for n, c in appear.items() if c == best)[0]

    return {
        "trade_date": dates[0],
        "current_leader_name": name, "current_leader_code": latest["sector_code"],
        "streak_days": max(streak, 1),
        "top_three_appearances": appear.get(name, 0),   # 0 是真值，不是缺数据
        "strength_delta_pct": delta,
        "strongest_repeat_name": strongest,
        "strongest_repeat_code": next((r["sector_code"] for r in
                                       (ranked.get(dates[0]) or [])
                                       if r["sector_name"] == strongest), None),
        "window_days": len(win), "window_start": win[0] if win else None,
    }
