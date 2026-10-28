# -*- coding: utf-8 -*-
"""
形态扫描：连板窗口计数、断板后走箱体并跌破均线。

**为什么不做成 `config.screen_rule` 的条件模板**：那个引擎存的是 WHERE 片段
（见 `app/compute/screener.py` 的模块说明），而这里两个形态都要窗口函数
（gaps-and-islands 数连板）和"窗口内最后一个涨停日"这种游标逻辑，塞不进片段。
历史上那条 `consecutive` 模板就是因为写不对被删掉的（`05_seed.sql` 里还留着
那段注释）。所以这里写成两个取数函数，页面照旧只认命名查询。

**涨停判据用 `md.stock_daily.limit_up` 真实涨停价，不用涨幅阈值**：
`close >= limit_up - 0.005`。用"涨幅 > 9.8%"会把创业板 20%、北交所 30%、
主板 ST 5% 全判错 —— 而且判错的方向是**漏掉**（ST 涨 5% 就是涨停，
阈值 9.8% 一个都不认），这种错在页面上表现为"这票明明涨停了却没筛出来"。
`limit_up` 为空的老数据才退回按板块的涨幅阈值（`_ZT_EXPR` 里的第二个分支）。

⚠️ **但 `limit_up` 本身是推导值，ST 那一档已知是错的**（2026-09-01 下午实测
推翻了当天上午的结论）：`app/db/repo/quote.py` 的 `recompute_limits()` 用
`pre_close × ref.board_limit_ratio(board, is_st)` 现算，ST 一律给 ±5%；而近
20 个交易日每天有 6~21 行**收盘价直接越过了算出来的涨跌停价**，全是 ST
（`*ST西旅 000610` 09-01 涨 10.07%，推导涨停价只给到 ±5%）。成交价站在通达信
`ZTPrice` 那一边。所以：北交所那半（库里四舍五入正确）站得住，**ST 那半反了**。
后果是 ST 票会在涨 5% 上下被判成涨停 —— 这两个扫描对 ST 的结果**不可信**，
要修得先定用哪个数据源当涨跌停价的权威（见 `tests/test_pattern_scan.py::
test_limit_price_is_derived_and_its_error_stays_bounded` 那个哨兵）。


窗口一律按 `ref.trade_calendar` 的交易日数，不按自然日 —— 往前推 7 个自然日
遇上长假只剩 3 个交易日，"前 3 到前 7"就整段错位。
"""
from __future__ import annotations

import datetime as dt

from app.db import engine

MAX_LIMIT = 2000

#: 均线只允许这几条，值是窗口长度。**均线是现算的，不读 fct.indicator_value**：
#: 2026-09-01 实测两者逐票完全相等（5,544 只，最大差 0），而现算不依赖 d1 有没有
#: 跑到基准日 —— 因子只算到昨天时，读表的写法会安静地返回 0 行，
#: 而 0 行在页面上跟"今天真没有这种票"长得一模一样。
MA_WINDOWS = {'ma5': 5, 'ma10': 10, 'ma20': 20}


#: 破线用哪个价：收盘价还是当日最低。列名要拼进 SQL，同样走白名单。
PRICE_COLS = {'close': '收盘', 'low': '最低'}

#: 次数比较符白名单
OPS = ('>=', '=', '<=')

#: 涨停判据。`x` 是 md.stock_daily 的别名，`s` 是 ref.security。
#: 第二个分支是 limit_up 为空时的兜底，阈值按板块给，**比真实涨停幅低 0.2**
#: （9.8 / 19.8 / 29.8 / 4.8）：一字板的收盘价偶尔差一分钱，卡死在 10.0
#: 会把真涨停判掉。
_ZT_EXPR = """
    CASE
        WHEN x.limit_up IS NOT NULL AND x.close >= x.limit_up - 0.005 THEN 1
        WHEN x.limit_up IS NULL AND x.pre_close > 0 AND
             (x.close - x.pre_close) / x.pre_close * 100 >=
             CASE WHEN s.is_st                     THEN 4.8
                  WHEN x.ts_code LIKE '9%%'        THEN 29.8
                  WHEN x.ts_code LIKE '30%%'
                    OR x.ts_code LIKE '68%%'       THEN 19.8
                  ELSE 9.8 END                                THEN 1
        ELSE 0
    END
"""

_POOLS = {
    'market': "s.asset_type = 'stock'",
    # 自选池：`config.watchlist_stock` 一只票可能在多个组里，所以要 DISTINCT
    'watchlist': ("s.asset_type = 'stock' AND x.ts_code IN "
                  "(SELECT DISTINCT ts_code FROM config.watchlist_stock)"),
}


class PatternError(ValueError):
    """参数不合法。照实抛给页面显示 —— "扫描失败"这种提示没有任何用。"""


def _pool(universe: str) -> str:
    if universe not in _POOLS:
        raise PatternError(f'票池只能是 {list(_POOLS)} 之一，收到 {universe!r}')
    return _POOLS[universe]


def latest_day(*, role: str = 'reader') -> dt.date | None:
    """有行情的最后一个交易日。基准日留空时用它。"""
    return engine.scalar('SELECT max(trade_date) FROM md.stock_daily', role=role)


def _day(trade_date, *, role: str = 'reader') -> dt.date | None:
    if not trade_date:
        return latest_day(role=role)
    if isinstance(trade_date, dt.date):
        return trade_date
    return dt.date.fromisoformat(str(trade_date)[:10])


#: 页面要用同一套"基准日怎么定"的口径（它要把窗口的真实日期显示出来），
#: 所以给一个公开名字，不让调用方去碰 `_day`。
resolve_day = _day



def back_days(target: dt.date, n: int, *, role: str = 'reader') -> list[dt.date]:
    """
    从基准日往前数的交易日列表，**`[0]` 就是基准日本身**。

    "前 3 个交易日"＝ `back_days(d, k)[3]`。这个 0 起点的约定要记住：
    页面上填 3 和 7，取的是 `dates[3] .. dates[7]`，两端都含。

    取的是 `md.stock_daily` 里真有行情的日子，不是日历 —— 日历里有 is_open
    但库里还没摄入的那几天（今天就是），会让窗口整体往前飘一格。
    """
    rows = engine.query(
        'SELECT DISTINCT trade_date FROM md.stock_daily '
        ' WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT %s',
        (target, max(int(n), 1)), role=role)
    return [r['trade_date'] for r in rows]


def window_bounds(target: dt.date, n_back: int, m_back: int, *,
                  role: str = 'reader') -> tuple[dt.date, dt.date]:
    """
    把"前 N 到前 M 个交易日"翻成一对真实日期 `(起, 止)`。

    `n_back` 是近端、`m_back` 是远端，两端都含。N 可以是 0（含基准日本身）。
    """
    n, m = int(n_back), int(m_back)
    if n < 0 or m < 0:
        raise PatternError('前 N / 前 M 不能是负数')
    if n > m:
        n, m = m, n                  # 填反了就自己正过来，不要为此报错
    dates = back_days(target, m + 1, role=role)
    if len(dates) <= n:
        raise PatternError(f'库里 {target} 往前只有 {len(dates)} 个交易日，'
                           f'凑不出"前 {n} 到前 {m}"这个窗口')
    lo = dates[min(m, len(dates) - 1)]
    hi = dates[n]
    return lo, hi


#: 窗口内逐日的涨停标记 + 序号。gaps-and-islands 数连板要用 `seq`。
_WIN_CTE = """
    SELECT x.ts_code, x.trade_date, x.close, x.high, x.low, x.pre_close,
           x.limit_up,
           row_number() OVER (PARTITION BY x.ts_code
                              ORDER BY x.trade_date) AS seq,
           """ + _ZT_EXPR + """ AS zt
      FROM md.stock_daily x
      JOIN ref.security s ON s.ts_code = x.ts_code
     WHERE x.trade_date BETWEEN %(lo)s AND %(hi)s
       AND {pool}
"""

#: 连板段：把涨停日按 `seq - 连续编号` 分组，同一段的差值相同（经典解法）。
#: 不用 lag/lead 逐日判断，是因为要的是"每一段有多长"，lag 只能判"是不是连着"。
#:
#: ⚠️ **下面这些 CTE 一律不许用相关子查询**（`WHERE r.ts_code = a.ts_code`
#: 这种）。CTE 上没有索引，5,545 只票 × 25 天 = 14 万行，每只票再回扫一遍
#: 就是几千次全扫 —— 2026-09-01 第一版箱体查询就是这么写的，直接撞
#: statement timeout。要什么就在同一个 GROUP BY 里聚出来。
_RUNS_CTE = """
    zt AS (
        SELECT ts_code, trade_date, seq,
               seq - row_number() OVER (PARTITION BY ts_code
                                        ORDER BY trade_date) AS grp
          FROM win WHERE zt = 1
    ),
    runs AS (
        SELECT ts_code, grp, count(*) AS run_len,
               max(trade_date) AS run_end
          FROM zt GROUP BY 1, 2
    )
"""


_TAIL_JOIN = """
      JOIN md.stock_daily t ON t.ts_code = a.ts_code
                           AND t.trade_date = %(d)s
      JOIN ref.security  sec ON sec.ts_code = a.ts_code
      LEFT JOIN fct.score o ON o.ts_code = a.ts_code
                           AND o.trade_date = %(d)s
                           AND o.cap_key = 'oper_score'
"""


def limit_streak_scan(trade_date=None, *, n_back: int = 3, m_back: int = 7,
                      times: int = 3, op: str = '>=',
                      universe: str = 'market', limit: int = 500,
                      role: str = 'reader') -> list[dict]:
    """
    **前 `n_back` 到前 `m_back` 个交易日这段窗口里，涨停 `times` 次的票**。

    「涨停次数」＝**窗口内涨停的天数**（用户 2026-09-01 确认的口径）：窗口里
    D1,D2,D3 连着涨停、隔一天 D5,D6 又连着，算 **5 次**，不是 2 段、也不是
    3 板。这个口径和口语里的"连板 3 次"不是一回事，所以列名叫「涨停次数」，
    另外给一列「连板高度」＝最长那一段的板数（上例是 3）。两个数摆在同一行上，
    想按哪个口径看都不用回头重算。

    `op` 只能是 `>=` / `=` / `<=`：默认 `>=`，因为"至少 3 次"比"正好 3 次"
    更常用；要精确命中就切 `=`。

    窗口两端都含，`n_back` 可以填 0（含基准日）。
    """
    d = _day(trade_date, role=role)
    if not d:
        return []
    if op not in OPS:
        raise PatternError(f'比较符只能是 {list(OPS)} 之一，收到 {op!r}')
    lo, hi = window_bounds(d, n_back, m_back, role=role)
    sql = """
    WITH win AS (""" + _WIN_CTE.format(pool=_pool(universe)) + """),
    """ + _RUNS_CTE + """,
    tops AS (
        SELECT ts_code, max(run_len) AS max_run FROM runs GROUP BY 1
    ),
    agg AS (
        SELECT z.ts_code, count(*) AS zt_days,
               string_agg(to_char(z.trade_date, 'MM-DD'), '、'
                          ORDER BY z.trade_date) AS zt_dates,
               max(z.trade_date) AS last_zt
          FROM zt z GROUP BY 1
    )
    SELECT a.ts_code                                       AS 代码,
           sec.name                                        AS 名称,
           a.zt_days                                       AS 涨停次数,
           tp.max_run                                      AS 连板高度,
           a.zt_dates                                      AS 涨停日期,
           a.last_zt                                       AS 最后涨停日,
           t.close                                         AS 收盘,
           t.pct_chg                                       AS 涨跌幅,
           t.turnover_rate_pct                             AS 换手率,
           round(o.value, 1)                               AS 操作分,
           o.quadrant                                      AS 象限
      FROM agg a
      JOIN tops tp ON tp.ts_code = a.ts_code""" + _TAIL_JOIN + """
     WHERE a.zt_days """ + op + """ %(times)s
     ORDER BY a.zt_days DESC, tp.max_run DESC, o.value DESC NULLS LAST, a.ts_code
     LIMIT %(lim)s
    """

    return engine.query(sql, {'d': d, 'lo': lo, 'hi': hi,
                              'times': max(int(times), 1),
                              'lim': min(max(int(limit), 1), MAX_LIMIT)},
                        role=role)


def box_break_scan(trade_date=None, *, lookback: int = 20, box_days: int = 3,
                   box_pct: float = 15.0, ma: str = 'ma5',
                   price: str = 'close', universe: str = 'market',
                   limit: int = 500, role: str = 'reader') -> list[dict]:
    """
    **断板之后走箱体、今天又跌破均线的票**。

    三段判据（2026-09-01 与用户确认）：

    1. **断板**：近 `lookback` 个交易日里最后一个涨停日的**次日**＝断板日。
       窗口内没涨停过的票直接不参与 —— 这个形态的前提是"曾经有过板"。
    2. **走箱体**：断板日到基准日之间 `(最高 - 最低) / 最低 × 100 <= box_pct`
       （默认 15），天数 ≥ `box_days`（默认 3），且**期间不再涨停**。
       再涨停就不是箱体而是二波，形态的含义完全不同。
    3. **跌破均线**：基准日的 `price`（`close` 收盘 / `low` 当日最低）
       < `ma`（`ma5` / `ma10` / `ma20`，**现算，见 `MA_WINDOWS`**）。
       用最低价是"盘中破过"，用收盘是"收在线下"，后者更硬。

    `箱体上沿 / 箱体下沿` 取的是**断板日之后**的最高最低，不含断板前那根涨停 ——
    含进去的话箱体振幅永远等于那根板的涨幅，判据就废了。
    """
    d = _day(trade_date, role=role)
    if not d:
        return []
    if ma not in MA_WINDOWS:
        raise PatternError(f'均线只能是 {list(MA_WINDOWS)} 之一，收到 {ma!r}')
    if price not in PRICE_COLS:
        raise PatternError(f'价格只能是 {list(PRICE_COLS)} 之一，收到 {price!r}')
    ma_n = MA_WINDOWS[ma]
    look = max(int(lookback), 2)
    dates = back_days(d, look, role=role)
    if len(dates) < 2:
        return []
    lo, hi = dates[-1], dates[0]
    # 均线要暖机：多取 ma_n 个交易日，否则窗口最早那几天的均线是残缺平均
    warm = back_days(d, look + ma_n, role=role)
    ma_lo = warm[-1]
    sql = """
    WITH px AS (
        SELECT x.ts_code, x.trade_date, x.close, x.high, x.low, x.pre_close,
               x.limit_up, x.pct_chg, x.turnover_rate_pct,
               """ + _ZT_EXPR + """ AS zt,
               CASE WHEN count(*) OVER (PARTITION BY x.ts_code
                                        ORDER BY x.trade_date
                                        ROWS BETWEEN """ + str(ma_n - 1) + """
                                        PRECEDING AND CURRENT ROW) = """ + str(ma_n) + """
                    THEN avg(x.close) OVER (PARTITION BY x.ts_code
                                            ORDER BY x.trade_date
                                            ROWS BETWEEN """ + str(ma_n - 1) + """
                                            PRECEDING AND CURRENT ROW)
               END AS ma_val
          FROM md.stock_daily x
          JOIN ref.security s ON s.ts_code = x.ts_code
         WHERE x.trade_date BETWEEN %(ma_lo)s AND %(hi)s
           AND """ + _pool(universe) + """
    ),
    win AS (
        SELECT p.*, row_number() OVER (PARTITION BY p.ts_code
                                       ORDER BY p.trade_date) AS seq
          FROM px p WHERE p.trade_date >= %(lo)s
    ),
    """ + _RUNS_CTE + """,
    -- 每只票的**最后那一段**连板：run_end 最大的那一段。它的 run_len 就是
    -- 断板前的连板高度，last_zt 就是断板日的前一天。
    last AS (
        SELECT ts_code, run_end AS last_zt, run_len AS final_run FROM (
            SELECT ts_code, run_end, run_len,
                   row_number() OVER (PARTITION BY ts_code
                                      ORDER BY run_end DESC) AS rk
              FROM runs) q
         WHERE rk = 1
    ),
    -- 断板日之后到基准日的那一段，一次 GROUP BY 全聚出来：
    -- 断板日就是这段里最早的那天，箱体上下沿就是这段的最高最低。
    box AS (
        SELECT l.ts_code, l.last_zt, l.final_run,
               min(w.trade_date) AS bust_date,
               count(*)          AS box_days,
               max(w.high)       AS box_hi,
               min(w.low)        AS box_lo,
               sum(w.zt)         AS zt_after
          FROM last l
          JOIN win w ON w.ts_code = l.ts_code
                    AND w.trade_date > l.last_zt
         GROUP BY 1, 2, 3
    ),
    agg AS (
        SELECT * FROM box
         WHERE box_days >= %(box_days)s
           AND zt_after = 0
           AND box_lo > 0
           AND (box_hi - box_lo) / box_lo * 100 <= %(box_pct)s
    )

    SELECT a.ts_code                                        AS 代码,
           sec.name                                         AS 名称,
           a.last_zt                                        AS 最后涨停日,
           a.final_run                                      AS 连板高度,
           a.bust_date                                      AS 断板日,
           a.box_days                                       AS 箱体天数,
           a.box_hi                                         AS 箱体上沿,
           a.box_lo                                         AS 箱体下沿,
           round((a.box_hi - a.box_lo) / a.box_lo * 100, 2) AS 箱体振幅,
           t.close                                          AS 收盘,
           t.low                                            AS 最低,
           round(w.ma_val, 2)                               AS 均线,
           round((w.""" + price + """ - w.ma_val) / w.ma_val * 100, 2) AS 破线幅度,
           t.pct_chg                                        AS 涨跌幅,
           t.turnover_rate_pct                              AS 换手率,
           round(o.value, 1)                                AS 操作分,
           o.quadrant                                       AS 象限
      FROM agg a""" + _TAIL_JOIN + """
      JOIN win w ON w.ts_code = a.ts_code AND w.trade_date = %(d)s
     WHERE w.ma_val > 0
       AND w.""" + price + """ < w.ma_val
     ORDER BY 破线幅度, a.box_days DESC, o.value DESC NULLS LAST, a.ts_code
     LIMIT %(lim)s
    """
    return engine.query(sql, {'d': d, 'lo': lo, 'hi': hi, 'ma_lo': ma_lo,
                              'box_days': max(int(box_days), 1),
                              'box_pct': float(box_pct),
                              'lim': min(max(int(limit), 1), MAX_LIMIT)},
                        role=role)

