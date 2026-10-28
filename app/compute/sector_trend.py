# -*- coding: utf-8 -*-
"""
板块热度的多窗口趋势。

两步，两张表，各只有一个意思：

1. `analytics.sector_heat_daily` —— 板块级日热度。**2026-08-30 起直接从
   `market_data.sector_daily` 算板块级**（`app/compute/sector_heat_hist.py`，`source='sector'`），
   不再从个股评分聚回来：旧写法摊下去用"唯一主板块"、聚回来用"全部归属"，
   把别的板块的热度记到每个板块头上，而且历史被评分卡在 23 个交易日。
   现在全历史 1,231 个交易日 / 653 个板块都有。
2. `analytics.sector_trend` —— 每个板块每天每个窗口一行斜率。**长表不是宽表**：

   窗口天数是配置（`config.setting['sector_trend.windows']`），加一个 13 日趋势
   只要改那个数组；做成 `trend_3/trend_5/...` 一堆列，每加一个窗口都要改 DDL、
   改查询、改页面。

**趋势值 = 最小二乘斜率，单位「分/天」，向上为正、向下为负、0 为走平。**
为什么不是"(今日热度 − N 日前热度) ÷ N"：那个式子只用到首尾两个点，中间怎么走
全丢了 —— 一根插针就能把趋势判反。斜率用上窗口内全部 N 个点，且和「自选组热度」
那边的`趋势分数`口径一致（那边是斜率 × 10），同一个系统里"趋势"只该有一种算法。

`window_days=1` 是特例：一个点拟合不出斜率，所以它存的是**日趋势分值**
（今日热度 − 昨日热度），数学上等于两点斜率，`point_count` 如实记 2。
其余窗口 `window_days=N` 用 N 个点。**点数不足不补齐、不置 0**，只记 `point_count` ——
3 个点也能算出一条斜率，但那不是 21 日趋势，把它当 21 日趋势用会看错。
"""
from __future__ import annotations

import datetime as dt

from app.db import engine
from app.db.repo import registry as reg_repo

# 用户点名要的七个窗口 + 日趋势分值（window_days=1）。改这里只是改默认值，
# 实际生效的是 config.setting['sector_trend.windows']。
DEFAULT_WINDOWS: tuple[int, ...] = (1, 3, 5, 7, 9, 11, 15, 21)

SETTING_KEY = 'sector_trend.windows'


def windows() -> list[int]:
    """当前生效的窗口列表。非法值直接挡掉 —— 这个数会拼进 SQL 的窗口帧。"""
    raw = reg_repo.setting(SETTING_KEY, list(DEFAULT_WINDOWS))
    out = []
    for v in raw if isinstance(raw, (list, tuple)) else ():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 250:
            out.append(n)
    return sorted(set(out)) or list(DEFAULT_WINDOWS)


def refresh_heat(dates: list[dt.date], *, task_id: int | None = None) -> int:
    """
    刷新板块级日热度。**2026-08-30 换口径**：直接从 `market_data.sector_daily` 算板块级，
    委托给 `sector_heat_hist.run()`。

    原来这里是"把 `analytics.score[sector_heat]` 按 `reference_data.sector_member` 聚回板块"，
    那个写法有两个问题，第二个是硬伤：

    1. 历史被个股评分卡住 —— 实测只有 23 个交易日，`analytics.sector_trend` 的 21 日
       窗口连一个完整窗口都填不满，更别提启动点要的 60/120 日分位；
    2. **摊下去和聚回来用的不是同一个映射**。`ctx.broadcast()` 用
       `indicators.sector_map()` 给每只票的唯一主板块，聚回来却用
       `reference_data.sector_member` 的全部归属（一只票平均属 8.76 个板块，最多 169 个）。
       于是每个板块的 heat 成了"成员股各自**主**板块热度的平均"。实测
       `TDX_880082`（「板块趋势」，风格板块，3 只成分）2026-08-12 旧值 35.56，
       而那 3 只票的主板块是 880540/880599/880645 —— 与这个板块自己
       （`pct_change=-0.22%`、1 涨 2 跌）没有关系。

    板块级直算没有这个问题：四个分项脚本读的本来就只有 `market_data.sector_daily`。
    """
    if not dates:
        return 0
    from app.compute import sector_heat_hist
    return sector_heat_hist.run(min(dates), max(dates),
                                task_id=task_id).get('rows', 0)



def refresh_trend(dates: list[dt.date], wins: list[int] | None = None,
                  *, task_id: int | None = None) -> dict[int, int]:
    """
    算每个窗口的斜率并落库，返回 {窗口: 写入行数}。

    一个窗口一条 SQL：窗口帧的偏移量必须是常量表达式，参数化不了，所以
    `window_days` 是**整数化之后**拼进语句的（`windows()` 已经把范围卡在 1~250，
    这里再 `int()` 一次 —— 拼进 SQL 的东西不能只靠上游校验）。
    """
    if not dates:
        return {}
    wins = wins or windows()
    dmax, need = max(dates), 0
    for w in wins:
        need = max(need, int(w))
    # 目标日往前多取 need 天，否则窗口前半段拿不到历史点
    span = need + len(dates) + 5
    out: dict[int, int] = {}
    for w in wins:
        n = int(w)
        if n == 1:
            expr = 'h.heat - lag(h.heat) OVER w'
            cnt = ('CASE WHEN lag(h.heat) OVER w IS NULL AND h.heat IS NULL '
                   'THEN 0 WHEN lag(h.heat) OVER w IS NULL THEN 1 ELSE 2 END')
            frame = 'PARTITION BY h.sector_code ORDER BY d.rn'
        else:
            expr = 'regr_slope(h.heat, d.rn) OVER w'
            cnt = 'count(h.heat) OVER w'
            frame = (f'PARTITION BY h.sector_code ORDER BY d.rn '
                     f'ROWS BETWEEN {n - 1} PRECEDING AND CURRENT ROW')
        out[n] = engine.execute(
            f"""
            WITH d AS (
                SELECT trade_date,
                       row_number() OVER (ORDER BY trade_date) AS rn
                  FROM (SELECT DISTINCT trade_date
                          FROM analytics.sector_heat_daily
                         WHERE trade_date <= %(dmax)s
                           AND source = 'sector'
                         ORDER BY trade_date DESC
                         LIMIT %(span)s) t
            ),
            calc AS (
                SELECT h.sector_code, d.trade_date,
                       {expr} AS slope,
                       {cnt}  AS point_count
                  FROM analytics.sector_heat_daily h
                  JOIN d ON d.trade_date = h.trade_date
                 WHERE h.source = 'sector'
                WINDOW w AS ({frame})
            )

            INSERT INTO analytics.sector_trend
                   (sector_code, trade_date, window_days, slope, point_count, task_id)
            SELECT sector_code, trade_date, {n},
                   round(slope::numeric, 4), point_count, %(task)s
              FROM calc
             WHERE trade_date = ANY(%(dates)s)
               AND slope IS NOT NULL
            ON CONFLICT (sector_code, trade_date, window_days) DO UPDATE SET
                slope         = EXCLUDED.slope,
                point_count      = EXCLUDED.point_count,
                task_id       = EXCLUDED.task_id,
                calculated_at = now()
            """, {'dmax': dmax, 'span': span, 'dates': dates,
                  'task': task_id}, role='app')
    return out


def run(dates: list[dt.date], *, task_id: int | None = None,
        wins: list[int] | None = None) -> dict:
    """一次算完两张表。`dates` 是要刷新的交易日。"""
    heat = refresh_heat(dates, task_id=task_id)
    trend = refresh_trend(dates, wins, task_id=task_id)
    return {'heat_rows': heat, 'trend_rows': trend,
            'windows': wins or windows()}


def coverage(trade_date: dt.date) -> list[dict]:
    """
    某一天各窗口的覆盖情况：几个板块算出来了、其中几个点数是足的。

    做这个是因为热度历史只有二十几个交易日的时候，21 日趋势那一列**看着有数**、
    实际只用了 3 个点。页面和体检都要能看出"这一列现在不可信"。

    只统计现行口径（`source='sector'`）的板块。表里还留着 122 个 `TDX_SP_*`
    板块的旧行（那些板块在 `market_data.sector_daily` 里没有日线，新口径算不出来），
    算进来会让"板块数"莫名比板块日线还多 —— 实测过一次：652 个板块的那天
    打出 774。
    """
    return engine.query(
        """
        SELECT t.window_days,
               count(*)                                     AS sectors,
               count(*) FILTER (WHERE t.point_count >= t.window_days)   AS full_window,
               round(avg(t.point_count), 1)                     AS avg_points,
               round(min(t.slope), 2)                        AS min_slope,
               round(max(t.slope), 2)                        AS max_slope
          FROM analytics.sector_trend t
          JOIN analytics.sector_heat_daily h ON h.sector_code = t.sector_code
                                      AND h.trade_date = t.trade_date
                                      AND h.source = 'sector'
         WHERE t.trade_date = %s
         GROUP BY t.window_days
         ORDER BY t.window_days
        """, (trade_date,))

