# -*- coding: utf-8 -*-
"""
倍量震荡（`fct.vol_double`）：近 40 个交易日**频繁倍量**、且价格**沿 60 日线震荡**的票。

要找的形态：主力在 60 日均线附近反复进出 —— 量能一次次翻倍（有资金），
但价格没有走出趋势、始终贴着 60 线上下摆动（没突破，也没破位）。这类票
"重点关注"的意思是**等突破**，不是当天就买。

---- 为什么必须落库 ----

现算实测 **8.4 秒**（5,453 只票 × 100 个交易日的窗口函数）。MA60 需要 60 根
K 线的历史，再往前推 40 天的观察窗，一次要扫近 55 万行。放在页面上是"点一下
卡死"，所以照 `fct.sector_trend` / `fct.score_trend` 的先例预算落库，页面只读表。

**不走 `fct.indicator_value` 的 ma60**：那张表只有最近 23 个交易日
（评分历史起于 2026-07-29），40 天窗口里有一半天数取不到 ma60，筛出来是空的。
这里在 SQL 里自己用 `avg(close) OVER (ROWS 59 PRECEDING)` 算，
口径和 `config.indicator['ma60']`（`MA(close, 60)`）一致。

---- 口径（四条容易搞错的） ----

1. **倍量的基准是"前 5 个交易日的均量"，不是"昨天的量"**：
   `vol_shares >= 2 × avg(前 5 日 vol_shares)`。用昨天当基准的话，昨天缩量
   就会凭空多出一次"倍量"（缩一半、今天回到正常量 = 假倍量）。
   对昨日的口径也一起算出来存成 `dbl_prev_count` 供对照，但排序用前者。
2. **MA60 要满 60 根 K 线才算**：不足 60 根的（次新股）那天 ma60 记 NULL，
   不用"手头有几根算几根"凑一个偏高的均线 —— 那会让次新股全部挤进榜单。
3. **"沿 60 日线震荡"是四个条件同时成立**，不是只看偏离度：
   偏离度中位数小（贴着线）＋ 上下两侧都待过（`above_ratio` 在 20%~80%）
   ＋ 穿越次数够（`cross_count >= 2`）＋ 60 线自身走平（`|ma60_chg| <= 20%`）。
   只看偏离度会把"缓慢单边上行、股价一直压着均线"的趋势票算成震荡。
4. **这张表存的是原始读数，不存"是否入选"**：阈值放在
   `config.setting['vol_double.*']`，页面上改阈值立刻生效，不用重算。
"""
from __future__ import annotations

import datetime as dt

from app.db import engine
from app.db.repo import registry as reg_repo

# 观察窗口（交易日）。用户要的是"近 40 天"。
LOOKBACK = 40


# MA60 的周期。和 config.indicator['ma60'] 必须一致。
MA_WIN = 60

# 倍量倍数。2.0 = 成交量翻倍。
MULT = 2.0

# 一次 SQL 要拿多少天的原始行：观察窗 + MA60 的暖机期。
# 60 根暖机是硬要求 —— 少一根，窗口第一天的 ma60 就是 NULL。
BARS_NEEDED = LOOKBACK + MA_WIN

# 从库里取多少个**市场交易日**。刻意比 BARS_NEEDED 多 50 天：
# 取数按"市场交易日"切，但 MA60 要的是**这只票自己的 K 线根数** —— 停牌过的票在
# 100 个市场交易日里只有 90 几根，窗口最早那几天就凑不满 60 根，ma60 变 NULL。
# 2026-08-31 实测踩过：603221.SH 的 `ma60_days` 落库 33 天、逐日复核 40 天，
# 差的 7 天全是这个原因（`--verify` 当场抓到）。
# 50 天余量覆盖绝大多数停牌；停牌超过 50 天的票 `ma60_days` 会如实偏低，
# 那种票也确实不该出现在"重点关注"里。
FETCH_DAYS = BARS_NEEDED + 50

# 兼容旧名字（`--verify` 里按"这只票自己的 K 线根数"取数用的就是它）
SPAN = BARS_NEEDED


EXPLAIN = {
    'basis': '近 40 个交易日的倍量次数与"贴 60 日线"程度，全部由 `md.stock_daily` '
             '一张表算出（MA60 在 SQL 里自算，不依赖 fct.indicator_value）',
    'terms': (
        {'name': '倍量次数', 'key': 'dbl_count',
         'expr': 'count(vol_shares >= 2 × avg(前 5 日 vol_shares))',
         'note': '**基准是前 5 日均量不是昨日量**：昨天缩量会让今天凭空多一次倍量'},
        {'name': '倍量次数(对昨日)', 'key': 'dbl_prev_count',
         'expr': 'count(vol_shares >= 2 × 昨日 vol_shares)',
         'note': '只作对照。两个口径差太多说明这只票的量本身在剧烈跳动'},
        {'name': '穿越次数', 'key': 'cross_count',
         'expr': 'close 与 ma60 的上下关系在相邻交易日发生翻转的次数',
         'note': '震荡的核心判据。0 次 = 单边（一直在上面或一直在下面），不是震荡'},
        {'name': '偏离中位数', 'key': 'dev_med',
         'expr': 'percentile_cont(0.5) of |close / ma60 − 1| × 100',
         'note': '用中位数不用均值：中间一根涨停拉不动中位数，能真实反映"贴不贴"'},
        {'name': '最大偏离', 'key': 'dev_max',
         'expr': 'max(|close / ma60 − 1|) × 100',
         'note': '配偏离中位数一起看：中位小而最大值很大 = 中间被拉过一次又回来'},
        {'name': '线上天数占比', 'key': 'above_ratio',
         'expr': 'count(close >= ma60) / count(ma60) × 100',
         'note': '100% 或 0% 都不是震荡。20%~80% 才说明上下两侧都待过'},
        {'name': '60线变化', 'key': 'ma60_chg',
         'expr': '(窗口末 ma60 − 窗口首 ma60) / 窗口首 ma60 × 100',
         'note': '60 线自己在陡涨/陡跌时，"贴着线"就是趋势不是震荡，所以要卡住它'},
        {'name': '日均成交额', 'key': 'avg_amt_yuan',
         'expr': 'avg(amount_yuan)',
         'note': '流动性门槛。北交所小票能选出漂亮形态但一天成交几百万，进不去'},
    ),
    'caveat': 'MA60 不满 60 根 K 线的那天记 NULL（次新股不凑数）；'
              '`ma60_days` 如实记窗口内有几天算得出 ma60，不足的票不该入选。'
              '入选阈值在 config.setting[\'vol_double.*\']，改了立刻生效不用重算。',
}

# 一天一批（全市场每票一行）。参数：%(d)s 基准日、%(span)s 取几天原始行、
# %(n)s 观察窗天数、%(mult)s 倍量倍数、%(ma)s 均线周期。
#
# 三处刻意的写法：
# 1. `ROWS BETWEEN 59 PRECEDING AND CURRENT ROW` 配 `count(*) >= 60` ——
#    窗口帧不够 60 根时把 ma60 置 NULL，而不是用手头几根算个偏高的均值。
# 2. 倍量基准是 `ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING`（**不含当日**）。
#    含当日的话，当日的巨量会把基准自己抬上去，倍量次数会被系统性低估。
# 3. 首尾 ma60 用 `array_agg(...) FILTER (WHERE ma60 IS NOT NULL)`：
#    窗口第一天可能还没满 60 根，不 FILTER 会整列变 NULL。
CORE_SQL = """
        WITH span AS (
            SELECT min(trade_date) AS lo FROM (
                SELECT DISTINCT trade_date FROM md.stock_daily
                 WHERE trade_date <= %(d)s
                 ORDER BY trade_date DESC LIMIT %(span)s) z
        ),
        base AS (
            SELECT s.ts_code, s.trade_date, s.close, s.vol_shares, s.amount_yuan,
                   avg(s.close) OVER w60                       AS ma60,
                   count(*)     OVER w60                       AS ma60_n,
                   avg(s.vol_shares) OVER (PARTITION BY s.ts_code
                                           ORDER BY s.trade_date
                                           ROWS BETWEEN 5 PRECEDING
                                                    AND 1 PRECEDING) AS vma5,
                   lag(s.vol_shares) OVER (PARTITION BY s.ts_code
                                           ORDER BY s.trade_date)    AS vprev,
                   row_number() OVER (PARTITION BY s.ts_code
                                      ORDER BY s.trade_date DESC)    AS rn
              FROM md.stock_daily s, span
             WHERE s.trade_date >= span.lo AND s.trade_date <= %(d)s
               AND s.close IS NOT NULL AND s.vol_shares > 0
            WINDOW w60 AS (PARTITION BY s.ts_code ORDER BY s.trade_date
                           ROWS BETWEEN %(ma_prev)s PRECEDING AND CURRENT ROW)
        ),
        win AS (
            SELECT ts_code, trade_date, close, vol_shares, amount_yuan,
                   vma5, vprev,
                   CASE WHEN ma60_n >= %(ma)s THEN ma60 END AS ma60
              FROM base WHERE rn <= %(n)s
        ),
        side AS (
            SELECT w.*,
                   CASE WHEN ma60 IS NULL THEN NULL
                        WHEN close >= ma60 THEN 1 ELSE -1 END AS sd,
                   lag(CASE WHEN ma60 IS NULL THEN NULL
                            WHEN close >= ma60 THEN 1 ELSE -1 END)
                       OVER (PARTITION BY ts_code ORDER BY trade_date) AS sd_prev
              FROM win w
        )
        SELECT ts_code,
               count(*)                                        AS bars,
               count(ma60)                                     AS ma60_days,
               count(*) FILTER (WHERE vma5 > 0
                                  AND vol_shares >= %(mult)s * vma5)  AS dbl_count,
               count(*) FILTER (WHERE vprev > 0
                                  AND vol_shares >= %(mult)s * vprev) AS dbl_prev_count,
               count(*) FILTER (WHERE sd IS NOT NULL AND sd_prev IS NOT NULL
                                  AND sd <> sd_prev)           AS cross_count,
               avg(amount_yuan)                                AS avg_amt_yuan,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY abs(close / ma60 - 1) * 100)        AS dev_med,
               max(abs(close / ma60 - 1) * 100)                 AS dev_max,
               100.0 * count(*) FILTER (WHERE sd = 1)
                     / NULLIF(count(ma60), 0)                   AS above_ratio,
               (array_agg(ma60 ORDER BY trade_date)
                    FILTER (WHERE ma60 IS NOT NULL))[1]         AS ma60_first,
               (array_agg(ma60 ORDER BY trade_date DESC)
                    FILTER (WHERE ma60 IS NOT NULL))[1]         AS ma60_last,
               (array_agg(close ORDER BY trade_date DESC))[1]    AS close_last
          FROM side
         GROUP BY 1
        HAVING count(ma60) > 0
"""


def _params(d: dt.date) -> dict:
    return {'d': d, 'span': FETCH_DAYS, 'n': LOOKBACK, 'mult': MULT,
            'ma': MA_WIN, 'ma_prev': MA_WIN - 1}


def trade_days(end: dt.date | None = None, recent: int = 1) -> list[dt.date]:
    """最近 `recent` 个**有日线数据**的交易日（升序）。判据同 breadth。"""
    rows = engine.query(
        """
        SELECT DISTINCT trade_date FROM md.stock_daily
         WHERE (%(end)s::date IS NULL OR trade_date <= %(end)s::date)
         ORDER BY trade_date DESC
         LIMIT %(lim)s
        """, {'end': end, 'lim': max(1, int(recent))})
    return sorted(r['trade_date'] for r in rows)


def compute(trade_date: dt.date, limit: int | None = None) -> list[dict]:
    """算一天的全市场读数（不落库）。给 `--dry-run` 和测试用。"""
    sql = CORE_SQL + (' ORDER BY dbl_count DESC LIMIT %(lim)s' if limit else '')
    p = _params(trade_date)
    if limit:
        p['lim'] = int(limit)
    return engine.query(sql, p)


def refresh(dates: list[dt.date], *, task_id: int | None = None) -> int:
    """
    逐日算并落库，返回写入行数。

    **全市场每票一行都存**，不只存"入选的"：阈值是 `config.setting` 里的配置，
    页面上一改就该立刻生效；只存入选的话，每次调阈值都要重算 8 秒一天。
    """
    rows = 0
    for d in dates:
        rows += engine.execute(
            """
            INSERT INTO fct.vol_double
                   (trade_date, ts_code, win_days, bars, ma60_days,
                    dbl_count, dbl_prev_count, cross_count,
                    dev_med, dev_max, above_ratio, ma60_chg,
                    avg_amt_yuan, close_last, ma60_last, task_id)
            SELECT %(d)s, c.ts_code, %(n)s, c.bars, c.ma60_days,
                   c.dbl_count, c.dbl_prev_count, c.cross_count,
                   round(c.dev_med::numeric, 2), round(c.dev_max::numeric, 2),
                   round(c.above_ratio, 2),
                   round((100.0 * (c.ma60_last - c.ma60_first)
                          / NULLIF(c.ma60_first, 0))::numeric, 2),
                   round(c.avg_amt_yuan, 0), c.close_last,
                   round(c.ma60_last::numeric, 4), %(task)s
              FROM (""" + CORE_SQL + """) c
            ON CONFLICT (trade_date, ts_code) DO UPDATE SET
                win_days       = EXCLUDED.win_days,
                bars           = EXCLUDED.bars,
                ma60_days      = EXCLUDED.ma60_days,
                dbl_count      = EXCLUDED.dbl_count,
                dbl_prev_count = EXCLUDED.dbl_prev_count,
                cross_count    = EXCLUDED.cross_count,
                dev_med        = EXCLUDED.dev_med,
                dev_max        = EXCLUDED.dev_max,
                above_ratio    = EXCLUDED.above_ratio,
                ma60_chg       = EXCLUDED.ma60_chg,
                avg_amt_yuan   = EXCLUDED.avg_amt_yuan,
                close_last     = EXCLUDED.close_last,
                ma60_last      = EXCLUDED.ma60_last,
                task_id        = EXCLUDED.task_id,
                calculated_at  = now()
            """, {**_params(d), 'task': task_id}, role='app')
    return rows


def run(dates: list[dt.date], *, task_id: int | None = None) -> dict:
    return {'rows': refresh(dates, task_id=task_id), 'days': len(dates)}


def latest_date() -> dt.date | None:
    return engine.scalar('SELECT max(trade_date) FROM fct.vol_double')


# ------------------------------------------------------------------ 入选阈值

# 入选阈值的默认值。**放在代码里、只把覆盖项写进 config.setting['vol_double.filter']**
# （同 app/compute/ignition.py 的 DEFAULTS 做法）：种子里存一个空对象，
# 重跑 seed 不会把用户在页面上调过的阈值拍回默认。
#
# 每个数的来由（2026-08-28 全市场 5,510 只票实测分布）：
#   min_dbl      2   倍量 1 次谈不上"频繁"；≥2 次剩 2,266 只，≥5 次剩 146 只
#   dev_med_max  8   |收盘/MA60−1| 的中位数 ≤8% 才算"贴着线"
#   above_lo/hi  20/80  两侧都待过。87.5% 那种是"一直在线上"，属于趋势不是震荡
#   cross_min    2   至少来回穿两次
#   ma60_chg_max 20  60 线自己 40 天涨跌超过 20% 时，"贴着线"就是趋势
#   min_ma60_days 35 窗口 40 天里至少 35 天算得出 MA60，否则是次新
#   min_amt_yi   0.5 日均成交额 5,000 万以下的票形态再好也难进出
FILTER_DEFAULTS = {
    'min_dbl': 2,
    'dev_med_max': 8.0,
    'above_lo': 20.0,
    'above_hi': 80.0,
    'cross_min': 2,
    'ma60_chg_max': 20.0,
    'min_ma60_days': 35,
    'min_amt_yi': 0.5,
    'limit': 50,
}


def thresholds(**override) -> dict:
    """
    默认值 ← `config.setting['vol_double.filter']` ← 调用方参数，逐层覆盖。

    非法值（类型不对、负数）**直接忽略回到默认**，不让它进 SQL —— 阈值是要拼进
    WHERE 的，`None` 或字符串进去会让整张卡变成"无数据"，看的人以为今天没票入选。
    """
    out = dict(FILTER_DEFAULTS)
    for src in (reg_repo.setting('vol_double.filter', {}) or {}, override):
        if not isinstance(src, dict):
            continue
        for k, v in src.items():
            if k not in FILTER_DEFAULTS or v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v < 0:
                continue
            out[k] = int(v) if isinstance(FILTER_DEFAULTS[k], int) else v
    return out


def picks(trade_date: dt.date | None = None, **override) -> tuple[list[dict], dict]:
    """
    读表出榜：按倍量次数从高到低，返回 `(行, 实际用的阈值+基准日)`。

    **只读 `fct.vol_double`**，不现算 —— 现算 10 秒，页面会当场卡住。
    库里没有当天时**退到库里最新那天并如实标出来**，不留空卡：
    盘后到计算作业跑完之间有一段空窗，那段时间显示上一天比显示"无数据"有用。
    """
    t = thresholds(**override)
    d = trade_date or latest_date()
    if not d:
        return [], {**t, 'trade_date': None}
    rows = engine.query(
        """
        SELECT v.ts_code, s.name, s.board,
               v.dbl_count, v.dbl_prev_count, v.cross_count,
               v.dev_med, v.dev_max, v.above_ratio, v.ma60_chg,
               v.close_last, v.ma60_last,
               round(v.avg_amt_yuan / 1e8, 2) AS avg_amt_yi,
               v.ma60_days, v.bars
          FROM fct.vol_double v
          JOIN ref.security s ON s.ts_code = v.ts_code
                             AND s.asset_type = 'stock'
         WHERE v.trade_date = %(d)s
           AND v.dbl_count    >= %(min_dbl)s
           AND v.cross_count   >= %(cross_min)s
           AND v.ma60_days     >= %(min_ma60_days)s
           AND v.dev_med       <= %(dev_med_max)s
           AND v.above_ratio BETWEEN %(above_lo)s AND %(above_hi)s
           AND abs(v.ma60_chg) <= %(ma60_chg_max)s
           AND v.avg_amt_yuan  >= %(min_amt_yi)s * 1e8
         ORDER BY v.dbl_count DESC, v.dev_med ASC
         LIMIT %(limit)s
        """, {**t, 'd': d})
    return rows, {**t, 'trade_date': d}

