# -*- coding: utf-8 -*-
"""
全市场情绪读数（`fct.market_breadth`）—— 短线复盘真正看的那几个数。

**为什么要单独一张表**：这些是**市场层面**的标量（涨停家数、连板高度、晋级率、
炸板率…），不是个股属性。摊给每只票存一份（`market_mood` 那种做法）在评分体系里
说得通，但"明日验证表"要拿它们逐日比对，长表里取一个标量得先 DISTINCT 一次，
而且没法给"梯队分布"这种一天多列的读数留位置。

**它是「明日验证表」的数据源**（见 `app/compute/verification.py`）：昨天定下
"预期涨停家数上升"，今天拿这张表逐条打勾。所以这里的每个数都必须**可复算**，
口径写死在 SQL 里、不允许展示层再算一遍。

---- 口径（三条容易搞错的） ----

1. **涨停用 `limit_up` 价格判，不用"涨幅 > 9.8%"**。库里 `md.stock_daily.limit_up`
   是**涨停价**（numeric，不是布尔标记）——实测 08-28 有 5,544/5,547 行非空。
   用涨幅阈值会把创业板 20%、北交所 30% 的涨停全漏掉，也会把 ST 的 5% 算错。
   容差 0.01 元：收盘价与回算涨停价之间有分位舍入。
2. **炸板是"盘中摸到涨停价、收盘没封住"**：`high >= limit_up - 0.01 AND
   close < limit_up - 0.01`。**不是近似** —— 有涨停价就能精确判，
   实测 08-28 涨停 87 家、炸板 28 家 → 炸板率 24.3%。
3. **`pct_chg IS NULL` 的行全部排除**：新股首日、`pre_close` 缺失的行（08-28 有
   3 行）算不出涨跌幅，留着会把"上涨家数占比"的分母悄悄放大。
"""
from __future__ import annotations

import datetime as dt

from app.db import engine

# 涨停价与收盘价的容差（元）。回算涨停价按分位四舍五入，差一分钱不算没封住。
EPS_YUAN = 0.01

# 连板高度往前看几个交易日。20 个足够 —— A 股历史最高连板不到 20，
# 往前扫太多只是白扫分区。
BOARD_LOOKBACK = 20

# 每个读数的口径说明。**页面说明从这里读**，改算法必须同批改这里
# （约定同 app/compute/builtin/*.py 的 EXPLAIN，由 tests/test_explain.py 兜住）。
EXPLAIN = {
    'basis': '全市场情绪的 11 个读数，全部由 `md.stock_daily` 当日截面 + '
             '最近 20 个交易日的涨停序列算出，**不含任何个股观点**',
    'terms': (
        {'name': '涨停家数', 'key': 'limit_up_count',
         'expr': "count(close >= limit_up - 0.01)",
         'note': '情绪的赚钱意愿。用涨停价判而不是涨幅阈值，'
                 '否则创业板 20%、北交所 30%、ST 5% 全部判错'},
        {'name': '跌停家数', 'key': 'limit_down_count',
         'expr': "count(close <= limit_down + 0.01)",
         'note': '恐慌的直接读数。和涨停家数一起看才有意义'},
        {'name': '触板家数', 'key': 'touched_count',
         'expr': "count(high >= limit_up - 0.01)",
         'note': '炸板率的分母：盘中摸到涨停价的家数（含最终封住的）'},
        {'name': '炸板家数', 'key': 'broken_count',
         'expr': "count(high >= limit_up - 0.01 AND close < limit_up - 0.01)",
         'note': '摸到涨停又没封住。这是**精确值**不是近似 —— 库里有涨停价'},
        {'name': '炸板率', 'key': 'broken_rate',
         'expr': '炸板家数 / 触板家数 × 100',
         'note': '情绪转弱最灵敏的一项。升 = 承接变差'},
        {'name': '最高连板高度', 'key': 'highest_board',
         'expr': '末尾连续涨停天数的最大值（往前看 20 个交易日）',
         'note': '情绪的天花板。高度被压缩通常意味着周期见顶'},
        {'name': '连板梯队', 'key': 'board2_count / board3_count / board4plus_count',
         'expr': '按连板天数分档计数（2 板 / 3 板 / 4 板及以上）',
         'note': '**断层比高度更重要**：只有一个高标、中间断层，说明接力资金没了'},
        {'name': '晋级率', 'key': 'promotion_rate',
         'expr': '昨日涨停且今日继续涨停 / 昨日涨停 × 100',
         'note': '接力意愿。低于 30% 通常是退潮'},
        {'name': '昨涨停红盘率', 'key': 'red_rate',
         'expr': '昨日涨停且今日收红 / 昨日涨停 × 100',
         'note': '比晋级率宽的一档判据：没封板但没砸，说明还有人接'},
        {'name': '中位涨幅', 'key': 'median_pct',
         'expr': 'percentile_cont(0.5) WITHIN GROUP (ORDER BY pct_chg)',
         'note': '用中位数不用均值：几只妖股拉不动中位数'},
        {'name': '上涨家数占比', 'key': 'up_ratio',
         'expr': 'count(pct_chg > 0) / count(*) × 100',
         'note': '赚钱效应的面。和中位涨幅同向才算真普涨'},
    ),
    'caveat': 'pct_chg 为空的行（新股首日 / pre_close 缺失）全部排除，'
              '否则分母被悄悄放大；`stocks` 列如实记参与统计的票数。',
}

# 一天一行。参数只有 `%(d)s`（基准交易日）。
#
# 三处刻意的写法：
# 1. 涨停/跌停/触板/炸板**都在同一次扫描里用 FILTER 算完**，不是四个子查询 ——
#    同一天的截面扫一遍就够。
# 2. 连板序列单独一个 CTE 并**限定 20 个交易日**：不限的话会顺着分区一路扫到 2015 年。
# 3. 昨日涨停那两个比率用 `prev` CTE 取"上一个有数据的交易日"，
#    不用 `d - 1 day`：周末和长假会把分母算成 0。
CORE_SQL = """
        WITH prev AS (
            SELECT max(trade_date) AS d
              FROM md.stock_daily
             WHERE trade_date < %(d)s
        ),
        today AS (
            SELECT ts_code, close, high, pre_close, pct_chg, limit_up, limit_down
              FROM md.stock_daily
             WHERE trade_date = %(d)s
               AND pct_chg IS NOT NULL
        ),
        cut AS (
            -- 连板要用的交易日下界：往前 BOARD_LOOKBACK 个**有数据的**交易日
            SELECT min(trade_date) AS d
              FROM (SELECT DISTINCT trade_date
                      FROM md.stock_daily
                     WHERE trade_date <= %(d)s
                     ORDER BY trade_date DESC
                     LIMIT %(look)s) z
        ),
        seq AS (
            SELECT ts_code, trade_date,
                   (close >= limit_up - %(eps)s) AS is_lu,
                   row_number() OVER (PARTITION BY ts_code
                                      ORDER BY trade_date DESC) AS rn
              FROM md.stock_daily
             WHERE trade_date <= %(d)s
               AND trade_date >= (SELECT d FROM cut)
               AND limit_up IS NOT NULL AND close IS NOT NULL
        ),
        streak AS (
            -- 连板天数 = 第一个"不涨停"的名次 − 1。全窗口都涨停的票没有
            -- "不涨停"那一行，用窗口内行数兜底（这种票现实里没有，但不兜就是 NULL）
            SELECT s.ts_code,
                   coalesce(min(s.rn) FILTER (WHERE NOT s.is_lu) - 1,
                            max(s.rn)) AS boards
              FROM seq s
             GROUP BY 1
        ),
        prev_lu AS (
            SELECT p.ts_code
              FROM md.stock_daily p
             WHERE p.trade_date = (SELECT d FROM prev)
               AND p.limit_up IS NOT NULL
               AND p.close >= p.limit_up - %(eps)s
        )
        SELECT
            (SELECT d FROM prev)                                  AS prev_date,
            count(*)                                              AS stocks,
            count(*) FILTER (WHERE t.close >= t.limit_up - %(eps)s)
                                                                  AS limit_up_count,
            count(*) FILTER (WHERE t.close <= t.limit_down + %(eps)s)
                                                                  AS limit_down_count,
            count(*) FILTER (WHERE t.high >= t.limit_up - %(eps)s) AS touched_count,
            count(*) FILTER (WHERE t.high >= t.limit_up - %(eps)s
                               AND t.close < t.limit_up - %(eps)s) AS broken_count,
            count(*) FILTER (WHERE t.pct_chg > 0)                  AS up_count,
            count(*) FILTER (WHERE t.pct_chg < 0)                  AS down_count,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY t.pct_chg) AS median_pct,
            (SELECT max(boards) FROM streak)                       AS highest_board,
            (SELECT count(*) FROM streak WHERE boards = 2)         AS board2_count,
            (SELECT count(*) FROM streak WHERE boards = 3)         AS board3_count,
            (SELECT count(*) FROM streak WHERE boards >= 4)        AS board4plus_count,
            (SELECT count(*) FROM prev_lu)                         AS prev_limit_up,
            (SELECT count(*) FROM prev_lu l JOIN today x ON x.ts_code = l.ts_code
                       WHERE x.close >= x.limit_up - %(eps)s)      AS promoted,
            (SELECT count(*) FROM prev_lu l JOIN today x ON x.ts_code = l.ts_code
                       WHERE x.pct_chg > 0)                        AS stayed_red
          FROM today t
"""


def trade_days(end: dt.date | None = None, recent: int = 1) -> list[dt.date]:
    """
    最近 `recent` 个**有日线数据**的交易日（升序）。

    判据是 `md.stock_daily` 而不是交易日历：按日历取会把还没采集的日子带进来，
    算出来是一行 0，页面上分不清"当天真的没涨停"还是"数据没到"。
    """
    rows = engine.query(
        """
        SELECT DISTINCT trade_date FROM md.stock_daily
         WHERE (%(end)s::date IS NULL OR trade_date <= %(end)s::date)
         ORDER BY trade_date DESC
         LIMIT %(lim)s
        """, {'end': end, 'lim': max(1, int(recent))})
    return sorted(r['trade_date'] for r in rows)


def compute(trade_date: dt.date) -> dict | None:
    """算一天，返回原始读数（不落库）。给测试和 `--dry-run` 用。"""
    return engine.query_one(CORE_SQL, {'d': trade_date, 'eps': EPS_YUAN,
                                       'look': BOARD_LOOKBACK})


def refresh(dates: list[dt.date], *, task_id: int | None = None) -> int:
    """
    把每一天的读数算出来落库，返回写入行数。

    比率列（炸板率/晋级率/红盘率/上涨占比）在 SQL 里用 `NULLIF` 兜分母：
    没有票触板的那天炸板率是 **NULL 而不是 0** —— 0% 的意思是"都封住了"，
    和"今天没人摸涨停"是两回事，混成同一个值会让验证表判错。
    """
    rows = 0
    for d in dates:
        rows += engine.execute(
            """
            INSERT INTO fct.market_breadth
                   (trade_date, stocks, limit_up_count, limit_down_count,
                    touched_count, broken_count, broken_rate,
                    highest_board, board2_count, board3_count, board4plus_count,
                    prev_limit_up, promotion_rate, red_rate,
                    up_count, down_count, up_ratio, median_pct, task_id)
            SELECT %(d)s, c.stocks, c.limit_up_count, c.limit_down_count,
                   c.touched_count, c.broken_count,
                   round(100.0 * c.broken_count
                         / NULLIF(c.touched_count, 0), 2),
                   c.highest_board, c.board2_count, c.board3_count,
                   c.board4plus_count, c.prev_limit_up,
                   round(100.0 * c.promoted / NULLIF(c.prev_limit_up, 0), 2),
                   round(100.0 * c.stayed_red / NULLIF(c.prev_limit_up, 0), 2),
                   c.up_count, c.down_count,
                   round(100.0 * c.up_count / NULLIF(c.stocks, 0), 2),
                   round(c.median_pct::numeric, 4), %(task)s
              FROM (""" + CORE_SQL + """) c
            ON CONFLICT (trade_date) DO UPDATE SET
                stocks           = EXCLUDED.stocks,
                limit_up_count   = EXCLUDED.limit_up_count,
                limit_down_count = EXCLUDED.limit_down_count,
                touched_count    = EXCLUDED.touched_count,
                broken_count     = EXCLUDED.broken_count,
                broken_rate      = EXCLUDED.broken_rate,
                highest_board    = EXCLUDED.highest_board,
                board2_count     = EXCLUDED.board2_count,
                board3_count     = EXCLUDED.board3_count,
                board4plus_count = EXCLUDED.board4plus_count,
                prev_limit_up    = EXCLUDED.prev_limit_up,
                promotion_rate   = EXCLUDED.promotion_rate,
                red_rate         = EXCLUDED.red_rate,
                up_count         = EXCLUDED.up_count,
                down_count       = EXCLUDED.down_count,
                up_ratio         = EXCLUDED.up_ratio,
                median_pct       = EXCLUDED.median_pct,
                task_id          = EXCLUDED.task_id,
                calculated_at    = now()
            """, {'d': d, 'eps': EPS_YUAN, 'look': BOARD_LOOKBACK,
                  'task': task_id}, role='app')
    return rows


def run(dates: list[dt.date], *, task_id: int | None = None) -> dict:
    return {'rows': refresh(dates, task_id=task_id), 'days': len(dates)}


def latest(n: int = 2) -> list[dict]:
    """最近 n 天的读数（升序）。验证表拿它做"昨天 → 今天"的比对。"""
    rows = engine.query(
        'SELECT * FROM fct.market_breadth ORDER BY trade_date DESC LIMIT %s',
        (max(1, int(n)),))
    return sorted(rows, key=lambda r: r['trade_date'])


def on(trade_date: dt.date) -> dict | None:
    return engine.query_one(
        'SELECT * FROM fct.market_breadth WHERE trade_date = %s', (trade_date,))
