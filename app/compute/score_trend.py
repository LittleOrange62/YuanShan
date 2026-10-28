# -*- coding: utf-8 -*-
"""
个股操作分的窗口趋势 —— `/scores` 的「四象限趋势分」那张卡的数据来源。

**为什么落库**：现算一次 1.0s，而 `/scores` 是服务端一次性渲染的，这 1.0s 直接
加在首屏上（实测首屏 3.6s，这张卡占 1.0s）。趋势是**当天收盘后就定死的历史值**，
每次进页面重算一遍是纯浪费 —— 与 `analytics.sector_trend` 同一个理由、同一个做法。

**只有一份 SQL**：`CORE_SQL` 既被这里的 `refresh()` 用来落库，也被
`queries.q_quadrant_trend()` 在库里没有那天时用作现算回退。两处各写一份的话，
落库值和现算值会慢慢漂开，而页面上两条路看起来一模一样 —— 这种错发现不了。

**存斜率不存"累计变化分"**：表里 `slope` 的单位是分/天，与 `analytics.sector_trend`
一致；页面上的 `趋势分数` = `slope × window_days` 是展示层换算。换窗口长度不必重刷历史，
也不会出现"同一个数在两张表里量纲不同"。

**存 r 不存 r²**：`corr_coef` 的符号是信息（和 `slope` 同号才自洽），一开方就丢了。
"""
from __future__ import annotations

import datetime as dt

from app.db import engine

# 页面默认窗口。改这里只是改默认值，卡片实际用的是
# `config.page_layout.options.params.window_days`（注册表驱动）。
DEFAULT_WIN = 10

# 一次窗口计算的全部重活：序列、X+Y 最高值、斜率/截距/相关、连升天数、迷你走势。
# 输出一行一只票，列名与 `analytics.score_trend` 对齐，所以落库和现算能共用。
#
# 参数：`%(d)s` 基准日（含）、`%(window_days)s` 窗口长度（有评分的交易日数）。
#
# 三处性能取舍（都是实测出来的，别"顺手整理"回去）：
#
# 1. 序列 CTE **只扫 oper_score 一个 capability_key**。原来把三个 capability_key 用条件聚合
#    摊平在同一个 CTE 里，输入 166,308 行、GROUP BY 要外部归并排序（临时文件
#    9.6MB），整条 1.34s；拆开后只扫 55,436 行。
# 2. 日期上下界**取自窗口自身**（`min/max(trade_date) FROM seq`）。JOIN 条件推不
#    进扫描层，不写下界就顺着分区扫过去，实测多扫 20 万行。
# 3. X+Y 的最高值**不用自连接**（`x JOIN y`）：那是 55k × 55k 的归并连接，单这
#    一步 640ms。X 和 Y 是同一张表的两个 capability_key，按 (票, 日) 分组求
#    `sum(value)` 就是 X+Y。`HAVING count(*) = 2` 是必须的 —— 只有 X 没有 Y 的
#    那天 sum 出来是"半个 X+Y"，会凭空拉高或拉低最高值。
CORE_SQL = """
        WITH seq AS (
            -- 最近 window_days 个**有评分的交易日**。日历日减法圈不出固定条数：
            -- 长假一压，10 个自然日里可能只剩 5 个交易日，斜率量纲就变了。
            SELECT trade_date,
                   row_number() OVER (ORDER BY trade_date) AS rn
              FROM (SELECT DISTINCT trade_date
                      FROM analytics.score
                     WHERE capability_key = 'oper_score' AND trade_date <= %(d)s
                     ORDER BY trade_date DESC
                     LIMIT %(window_days)s) z
        ),
        ser AS (
            SELECT s.security_code, q.rn, s.trade_date, s.quadrant,
                   s.value AS oper
              FROM analytics.score s
              JOIN seq q ON q.trade_date = s.trade_date
             WHERE s.capability_key = 'oper_score' AND s.value IS NOT NULL
               AND s.trade_date >= (SELECT min(trade_date) FROM seq)
               AND s.trade_date <= (SELECT max(trade_date) FROM seq)
        ),
        xyw AS (
            SELECT security_code,
                   max(xy)                                      AS x_plus_y_max,
                   (array_agg(trade_date ORDER BY xy DESC,
                              trade_date DESC))[1]              AS x_plus_y_max_date
              FROM (
                    SELECT s.security_code, s.trade_date, sum(s.value) AS xy
                      FROM analytics.score s
                      JOIN seq q ON q.trade_date = s.trade_date
                     WHERE s.capability_key IN ('stock_quality', 'env_resonance')
                       AND s.trade_date >= (SELECT min(trade_date) FROM seq)
                       AND s.trade_date <= (SELECT max(trade_date) FROM seq)
                     GROUP BY 1, 2
                    HAVING count(*) = 2
                   ) t
             GROUP BY 1
        ),
        norm AS (
            SELECT s.*,
                   min(oper) OVER w AS mn, max(oper) OVER w AS mx,
                   oper - lag(oper) OVER (PARTITION BY security_code ORDER BY rn)
                       AS dif
              FROM ser s
            WINDOW w AS (PARTITION BY security_code)
        ),
        agg AS (
            SELECT security_code, count(*) AS point_count,
                   regr_slope(oper, rn)     AS slope,
                   regr_intercept(oper, rn) AS intercept,
                   corr(oper, rn)           AS corr_coef,
                   (array_agg(quadrant ORDER BY rn))[1]    AS first_quadrant,
                   -- 连升天数＝末尾连续上行的天数。窗口第一天 dif 是 NULL，
                   -- 所以 FILTER 里那一半永远有行，不会算出 NULL。
                   max(rn) - max(rn) FILTER (WHERE dif IS NULL OR dif <= 0)
                       AS up_streak_days,
                   -- 迷你走势：在自身 min~max 之间归一化到 7 档块字符。
                   -- 全窗口同一个值时 mx = mn，除法会炸，直接给中间那一档。
                   string_agg(
                       CASE WHEN mx <= mn THEN '▄'
                            ELSE (ARRAY['▁','▂','▃','▄','▅','▆','▇'])[
                                   1 + least(6, floor((oper - mn)
                                                      / (mx - mn) * 6))::int]
                       END, '' ORDER BY rn)                AS sparkline
              FROM norm
             GROUP BY 1
        )
        SELECT g.security_code, g.point_count, g.slope, g.intercept, g.corr_coef,
               w.x_plus_y_max, w.x_plus_y_max_date, g.up_streak_days, g.first_quadrant, g.sparkline
          FROM agg g
          JOIN xyw w ON w.security_code = g.security_code
"""


def scored_days(end: dt.date | None = None, recent: int = 1) -> list[dt.date]:
    """
    最近 `recent` 个**有评分**的交易日（升序）。

    判据是 `analytics.score` 而不是交易日历：按日历取会把评分还没算的日子也带进来，
    那些日子落出来的趋势是空的，页面上看不出是"没算"还是"真的没趋势"。
    """
    rows = engine.query(
        """
        SELECT DISTINCT trade_date FROM analytics.score
         WHERE capability_key = 'oper_score'
           AND (%(end)s::date IS NULL OR trade_date <= %(end)s::date)
         ORDER BY trade_date DESC
         LIMIT %(lim)s
        """, {'end': end, 'lim': max(1, int(recent))})
    return sorted(r['trade_date'] for r in rows)


def refresh(dates: list[dt.date], window_days: int = DEFAULT_WIN,
            *, task_id: int | None = None) -> int:
    """
    把 `dates` 每一天的窗口趋势算出来落库，返回写入行数。

    一天一条语句：窗口取的是"该日往前 window_days 个有评分的交易日"，多天合并成一条
    就得为每一天各开一个窗口帧，反而更慢也更难看懂。

    `window_days` 会拼进 `LIMIT`，所以先 `int()` 并卡范围 —— 拼进 SQL 的东西不能只靠
    上游校验。
    """
    n = int(window_days)
    if n < 2 or n > 250:
        raise ValueError(f'window_days 必须在 2~250 之间，收到 {window_days}')
    rows = 0
    for d in dates:
        rows += engine.execute(
            """
            INSERT INTO analytics.score_trend
                   (security_code, trade_date, window_days, point_count, slope, intercept, corr_coef,
                    x_plus_y_max, x_plus_y_max_date, up_streak_days, first_quadrant, sparkline, task_id)
            SELECT c.security_code, %(d)s, %(window_days)s,
                   c.point_count,
                   round(c.slope::numeric, 6),
                   round(c.intercept::numeric, 6),
                   round(c.corr_coef::numeric, 6),
                   round(c.x_plus_y_max, 4), c.x_plus_y_max_date,
                   c.up_streak_days, c.first_quadrant, c.sparkline, %(task)s
              FROM (""" + CORE_SQL + """) c
            ON CONFLICT (security_code, trade_date, window_days) DO UPDATE SET
                point_count      = EXCLUDED.point_count,
                slope         = EXCLUDED.slope,
                intercept     = EXCLUDED.intercept,
corr_coef     = EXCLUDED.corr_coef,
                x_plus_y_max        = EXCLUDED.x_plus_y_max,
                x_plus_y_max_date   = EXCLUDED.x_plus_y_max_date,
                up_streak_days     = EXCLUDED.up_streak_days,
                first_quadrant    = EXCLUDED.first_quadrant,
                sparkline         = EXCLUDED.sparkline,
                task_id       = EXCLUDED.task_id,
                calculated_at = now()
            """, {'d': d, 'window_days': n, 'task': task_id}, role='app')
    return rows


def run(dates: list[dt.date], window_days: int = DEFAULT_WIN,
        *, task_id: int | None = None) -> dict:
    return {'rows': refresh(dates, window_days, task_id=task_id),
            'days': len(dates), 'window_days': int(window_days)}


def coverage(trade_date: dt.date, window_days: int = DEFAULT_WIN) -> dict | None:
    """
    某一天的覆盖情况：几只票算出来了、其中几只窗口是满的。

    窗口比评分历史还长的时候，那一列**看着有数**、其实只用了几个点 ——
    不把 `point_count` 摆出来没人会去查。
    """
    return engine.query_one(
        """
        SELECT count(*)                                   AS stocks,
               count(*) FILTER (WHERE point_count >= %(w)s)  AS full_window,
               round(avg(point_count), 1)                    AS avg_points,
               round(min(slope), 4)                       AS min_slope,
               round(max(slope), 4)                       AS max_slope
          FROM analytics.score_trend
         WHERE trade_date = %(d)s AND window_days = %(w)s
        """, {'d': trade_date, 'w': int(window_days)})
