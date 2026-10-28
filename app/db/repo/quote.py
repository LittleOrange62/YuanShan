# -*- coding: utf-8 -*-
"""
行情读写。

单位约定（字段名自带单位，不做换算）：
  amount_yuan   元
  volume_shares    股
  turnover_rate_percent  百分数

写入一律 ON CONFLICT DO UPDATE，同一天重跑任意次结果一致。
每行必须带 source_batch_id（NOT NULL），血缘强制可追溯。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.db import engine

DAILY_COLUMNS = [
    'security_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close',
    'change_yuan', 'pct_change', 'volume_shares', 'amount_yuan', 'turnover_rate_percent',
    'limit_up', 'limit_down', 'adjustment_factor', 'source_batch_id',
]

UPSERT_DAILY = """
INSERT INTO market_data.stock_daily
    (security_code, trade_date, open, high, low, close, pre_close, change_yuan, pct_change,
     volume_shares, amount_yuan, turnover_rate_percent, limit_up, limit_down,
     adjustment_factor, source_batch_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (security_code, trade_date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, pre_close = EXCLUDED.pre_close,
    change_yuan = EXCLUDED.change_yuan, pct_change = EXCLUDED.pct_change,
    volume_shares = EXCLUDED.volume_shares, amount_yuan = EXCLUDED.amount_yuan,
    turnover_rate_percent = EXCLUDED.turnover_rate_percent,
    limit_up = EXCLUDED.limit_up, limit_down = EXCLUDED.limit_down,
    adjustment_factor = EXCLUDED.adjustment_factor, source_batch_id = EXCLUDED.source_batch_id,
    updated_at = now()
"""


def upsert_daily(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(UPSERT_DAILY, rows, role=role)


UPSERT_INDEX = """
INSERT INTO market_data.index_daily
    (security_code, trade_date, open, high, low, close, pre_close, pct_change,
     volume_shares, amount_yuan, source_batch_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (security_code, trade_date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, pre_close = EXCLUDED.pre_close,
    pct_change = EXCLUDED.pct_change, volume_shares = EXCLUDED.volume_shares,
    amount_yuan = EXCLUDED.amount_yuan, source_batch_id = EXCLUDED.source_batch_id,
    updated_at = now()
"""


def upsert_index_daily(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(UPSERT_INDEX, rows, role=role)


UPSERT_SECTOR = """
INSERT INTO market_data.sector_daily
    (sector_code, trade_date, open, high, low, close, pre_close, pct_change,
     volume_shares, amount_yuan, up_count, down_count, member_count, source_batch_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (sector_code, trade_date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, pre_close = EXCLUDED.pre_close,
    pct_change = EXCLUDED.pct_change, volume_shares = EXCLUDED.volume_shares,
    amount_yuan = EXCLUDED.amount_yuan, up_count = EXCLUDED.up_count,
    down_count = EXCLUDED.down_count, member_count = EXCLUDED.member_count,
    source_batch_id = EXCLUDED.source_batch_id, updated_at = now()
"""


def upsert_sector_daily(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(UPSERT_SECTOR, rows, role=role)


UPSERT_MINUTE = """
INSERT INTO market_data.stock_minute
    (security_code, dt, period, open, high, low, close, volume_shares, amount_yuan, source_batch_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (security_code, dt, period) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, volume_shares = EXCLUDED.volume_shares,
    amount_yuan = EXCLUDED.amount_yuan, source_batch_id = EXCLUDED.source_batch_id
"""


def upsert_minute(rows: list[tuple], *, role: str = 'etl') -> int:
    return engine.executemany(UPSERT_MINUTE, rows, role=role)


UPSERT_SNAPSHOT = """
INSERT INTO market_data.stock_snapshot
    (security_code, trade_date, pe_ttm, pb, total_market_value_yuan, float_market_value_yuan,
     eps, bps, roe_percent, extra, source_batch_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (security_code, trade_date) DO UPDATE SET
    pe_ttm = EXCLUDED.pe_ttm, pb = EXCLUDED.pb,
    total_market_value_yuan = EXCLUDED.total_market_value_yuan,
    float_market_value_yuan = EXCLUDED.float_market_value_yuan,
    eps = EXCLUDED.eps, bps = EXCLUDED.bps, roe_percent = EXCLUDED.roe_percent,
    -- extra 是整块覆盖而不是 `||` 合并：来源是同一个采集脚本的同一次快照，
    -- 合并只会把上一版的旧字段留在里面，看起来像"这天两种口径都有"
    extra = EXCLUDED.extra, source_batch_id = EXCLUDED.source_batch_id
"""


def upsert_snapshot(rows: list[tuple], *, role: str = 'etl') -> int:
    """
    每日收盘快照（市值 / 估值 / 涨跌停价 / 内外盘…）。

    上游是通达信客户端插件 `PYPlugins\\user\\Get_DayEnd.py` 落的
    `daily_close_data.db`，由 `ingest/jobs/c15_snapshot.py` 搬进来。
    """
    return engine.executemany(UPSERT_SNAPSHOT, rows, role=role)


# ============================================================ 读

_ADJ_EXPR = {
    'none': '{col}',
    # 后复权：乘以复权因子
    'hfq': '({col} * coalesce(adjustment_factor, 1))',
    # 前复权：除以最新因子
    'qfq': '({col} * coalesce(adjustment_factor, 1) / nullif(last_value(coalesce(adjustment_factor, 1)) '
           'OVER (PARTITION BY security_code ORDER BY trade_date '
           'ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING), 0))',
}


def daily(codes: str | list[str] | None = None, start: dt.date | None = None,
          end: dt.date | None = None, *, adjust: str = 'none',
          fields: list[str] | None = None, limit: int | None = None,
          role: str = 'reader') -> pd.DataFrame:
    """
    日线查询。分区表让跨年查询是一句普通 SQL —— 不再有年份分库路由与 ATTACH。
    adjust: none | qfq | hfq
    """
    if adjust not in _ADJ_EXPR:
        raise ValueError(f'adjust 必须是 none/qfq/hfq，收到 {adjust!r}')
    if isinstance(codes, str):
        codes = [codes]

    price_cols = ('open', 'high', 'low', 'close', 'pre_close')
    tmpl = _ADJ_EXPR[adjust]
    select = ['security_code', 'trade_date']
    for c in price_cols:
        select.append(f'{tmpl.format(col=c)} AS {c}' if adjust != 'none' else c)
    select += ['pct_change', 'change_yuan', 'volume_shares', 'amount_yuan',
               'turnover_rate_percent', 'limit_up', 'limit_down', 'adjustment_factor']

    if fields:
        keep = set(fields) | {'security_code', 'trade_date'}
        select = [s for s in select if s.split(' AS ')[-1].strip() in keep]

    sql = f"""
        SELECT {', '.join(select)}
          FROM market_data.stock_daily
         WHERE (%(codes)s::text[] IS NULL OR security_code = ANY(%(codes)s))
           AND (%(start)s::date IS NULL OR trade_date >= %(start)s)
           AND (%(end)s::date IS NULL OR trade_date <= %(end)s)
         ORDER BY security_code, trade_date
    """
    if limit:
        sql += f' LIMIT {int(limit)}'
    rows = engine.query(sql, {'codes': codes, 'start': start, 'end': end}, role=role)
    return pd.DataFrame(rows)


def daily_one_date(trade_date: dt.date, *, asset_type: str = 'stock',
                   role: str = 'reader') -> pd.DataFrame:
    """某个交易日的全市场截面 —— 评分与筛选的主要输入。"""
    rows = engine.query(
        """
        SELECT d.*, s.name, s.board, s.float_share, s.is_st
          FROM market_data.stock_daily d
          JOIN reference_data.security s USING (security_code)
         WHERE d.trade_date = %s AND s.asset_type = %s
         ORDER BY d.security_code
        """,
        (trade_date, asset_type), role=role)
    return pd.DataFrame(rows)


def daily_window(trade_date: dt.date, lookback: int, *, codes: list[str] | None = None,
                 role: str = 'reader') -> pd.DataFrame:
    """
    截止 trade_date 往前 lookback 个交易日的数据。

    注意：窗口以传入的 trade_date 为基准，不是"今天"。旧系统四个
    score_*d_* 脚本用 get_recent_dates() 取当前窗口、无视传入日期，
    导致所有回溯计算都是错的。
    """
    rows = engine.query(
        """
        WITH dates AS (
            SELECT calendar_date FROM reference_data.trade_calendar
             WHERE is_open AND calendar_date <= %(d)s
             ORDER BY calendar_date DESC LIMIT %(n)s)
        SELECT d.* FROM market_data.stock_daily d
         WHERE d.trade_date IN (SELECT calendar_date FROM dates)
           AND (%(codes)s::text[] IS NULL OR d.security_code = ANY(%(codes)s))
         ORDER BY d.security_code, d.trade_date
        """,
        {'d': trade_date, 'n': lookback, 'codes': codes}, role=role)
    return pd.DataFrame(rows)


def minute(security_code: str, trade_date: dt.date, period: int = 1,
           *, role: str = 'reader') -> pd.DataFrame:
    rows = engine.query(
        """
        SELECT security_code, dt, period, open, high, low, close, volume_shares, amount_yuan
          FROM market_data.stock_minute
         WHERE security_code = %s AND period = %s
           AND dt >= %s::date AND dt < %s::date + interval '1 day'
         ORDER BY dt
        """,
        (security_code, period, trade_date, trade_date), role=role)
    return pd.DataFrame(rows)


def index_daily(codes: list[str] | None = None, start: dt.date | None = None,
                end: dt.date | None = None, *, role: str = 'reader') -> pd.DataFrame:
    rows = engine.query(
        """
        SELECT * FROM market_data.index_daily
         WHERE (%(codes)s::text[] IS NULL OR security_code = ANY(%(codes)s))
           AND (%(start)s::date IS NULL OR trade_date >= %(start)s)
           AND (%(end)s::date IS NULL OR trade_date <= %(end)s)
         ORDER BY security_code, trade_date
        """,
        {'codes': codes, 'start': start, 'end': end}, role=role)
    return pd.DataFrame(rows)


def sector_daily(trade_date: dt.date | None = None, sector_code: str | None = None,
                 start: dt.date | None = None, end: dt.date | None = None,
                 *, role: str = 'reader') -> pd.DataFrame:
    rows = engine.query(
        """
        SELECT sd.*, si.name, si.sector_type
          FROM market_data.sector_daily sd
          LEFT JOIN reference_data.sector_info si USING (sector_code)
         WHERE (%(d)s::date IS NULL OR sd.trade_date = %(d)s)
           AND (%(sc)s::text IS NULL OR sd.sector_code = %(sc)s)
           AND (%(start)s::date IS NULL OR sd.trade_date >= %(start)s)
           AND (%(end)s::date IS NULL OR sd.trade_date <= %(end)s)
         ORDER BY sd.trade_date DESC, sd.pct_change DESC NULLS LAST
        """,
        {'d': trade_date, 'sc': sector_code, 'start': start, 'end': end}, role=role)
    return pd.DataFrame(rows)


def market_breadth(trade_date: dt.date, *, role: str = 'reader') -> dict:
    """大盘广度：涨跌家数、涨跌停数、成交额。/review 第一层的数据源。"""
    return engine.query_one(
        """
        SELECT count(*)                                              AS total,
               count(*) FILTER (WHERE pct_change > 0)                    AS up_count,
               count(*) FILTER (WHERE pct_change < 0)                    AS down_count,
               count(*) FILTER (WHERE pct_change = 0)                    AS flat_count,
               count(*) FILTER (WHERE limit_up IS NOT NULL
                                  AND close >= limit_up - 0.001)      AS limit_up_count,
               count(*) FILTER (WHERE limit_down IS NOT NULL
                                  AND close <= limit_down + 0.001)    AS limit_down_count,
               sum(amount_yuan)                                       AS amount_yuan,
               avg(pct_change)                                           AS avg_pct_chg,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY pct_change)   AS median_pct_chg
          FROM market_data.stock_daily d JOIN reference_data.security s USING (security_code)
         WHERE d.trade_date = %s AND s.asset_type = 'stock'
        """,
        (trade_date,), role=role) or {}


def pct_distribution(trade_date: dt.date, *, role: str = 'reader') -> list[dict]:
    """固定分箱边界，保证跨日可比（自适应分箱会让不同日期的图无法对比）。"""
    return engine.query(
        """
        WITH b(lo, hi, label) AS (VALUES
            (-100.0, -10.0, '跌停附近'), (-10.0, -7.0, '-10~-7'), (-7.0, -5.0, '-7~-5'),
            (-5.0, -3.0, '-5~-3'), (-3.0, -1.0, '-3~-1'), (-1.0, 0.0, '-1~0'),
            (0.0, 1.0, '0~1'), (1.0, 3.0, '1~3'), (3.0, 5.0, '3~5'),
            (5.0, 7.0, '5~7'), (7.0, 10.0, '7~10'), (10.0, 100.0, '涨停附近'))
        SELECT b.label, b.lo, b.hi, count(d.security_code) AS cnt
          FROM b LEFT JOIN market_data.stock_daily d
            ON d.trade_date = %s AND d.pct_change >= b.lo AND d.pct_change < b.hi
          LEFT JOIN reference_data.security s ON s.security_code = d.security_code AND s.asset_type = 'stock'
         GROUP BY b.label, b.lo, b.hi ORDER BY b.lo
        """,
        (trade_date,), role=role)


def top_movers(trade_date: dt.date, n: int = 50, *, desc: bool = True,
               role: str = 'reader') -> pd.DataFrame:
    order = 'DESC' if desc else 'ASC'
    rows = engine.query(
        f"""
        SELECT d.security_code, s.name, s.board, d.close, d.pct_change,
               d.volume_shares, d.amount_yuan, d.turnover_rate_percent
          FROM market_data.stock_daily d JOIN reference_data.security s USING (security_code)
         WHERE d.trade_date = %s AND s.asset_type = 'stock'
         ORDER BY d.pct_change {order} NULLS LAST
         LIMIT %s
        """,
        (trade_date, n), role=role)
    return pd.DataFrame(rows)


def date_range(*, role: str = 'reader') -> dict:
    return engine.query_one(
        'SELECT min(trade_date) AS first_date, max(trade_date) AS last_date, '
        'count(*) AS rows FROM market_data.stock_daily', role=role) or {}


def dates_available(*, role: str = 'reader') -> list[dt.date]:
    rows = engine.query(
        'SELECT DISTINCT trade_date FROM market_data.stock_daily ORDER BY trade_date DESC', role=role)
    return [r['trade_date'] for r in rows]


# ============================================================ 派生字段回填

def recompute_limits(trade_date: dt.date | None = None, *, role: str = 'app',
                     only_missing: bool = False) -> int:
    """
    刷 `limit_up/limit_down`。**实现已挪到 `app/compute/limit_prices.py`**，
    这里只留一个签名不变的门面 —— 是为了让"谁写这两列"在仓库里只有一条路：

    * 以前这里用 `reference_data.security.is_st`（当前快照，updated_at 停在 2026-08-27）
      套全部历史，把"现在才是 ST"的票在它**还不是 ST 的年份**判成 ±5%：
      越界 1,698 只 / 16,074 行（2021-08-03~2026-09-30）。
    * 另一条写者 `c15_snapshot` 把通达信未复权的 ZTPrice 填进除权日，
      与库里同基的 pre_close 打架（2026 年 6,088 行不在任何一档，其中 5,895 行是复权因子变化日）。

    现在档位按"当日"定：通达信快照反推 → 当日行情可证 → ST 档期 → 规则，
    并把每行的出处写进 `limit_basis`。`only_missing` 仍表示"只补空值"，
    纠错必须走全量（不带这个开关）；判为"那天没有涨跌停价/档位解释不了这天"的行
    照样会被清空并记出处，那不是补空。

    返回受影响行数。**备份策略**：单日回填（收盘链天天走的那条）不再另建备份表 ——
    链上每次循环建一张表会把 `operations` 淹掉，这一层有每日 `pg_dump` 兜底；
    整库/区间重刷必须由 `python -m app.compute.limit_prices --recompute` 走，
    那条路强制建 `operations.limit_price_backup_YYYYMMDD`，回退用
    `--restore <备份表>`。
    """
    from app.compute import limit_prices     # 局部导入：避免 repo 层被计算层反向依赖

    out = limit_prices.recompute(trade_date, only_missing=only_missing,
                                 dry_run=False, backup=trade_date is None)
    return int(out.get('values_written') or 0) + int(out.get('nulls_written') or 0)


def recompute_sector_counts(start: dt.date | None = None,
                            end: dt.date | None = None, *,
                            role: str = 'admin') -> int:
    """
    从成员个股回算板块的涨/跌/成分家数。

    **这三列在迁移进来的 71.6 万行里全是 NULL** —— 旧库没采，通达信的
    `.day` 文件里也没有（一条 32 字节记录只有 OHLC/vol/amount）。
    而板块广度（up/(up+down)）是板块热度里权重最高、最难造假的一维：
    指数能被几只权重股拉起来，上涨家数占比骗不了人。所以这里从
    reference_data.sector_member × market_data.stock_daily 自己数。

    默认不跑全历史：128,590 条成员关系 × 1,228 个交易日的聚合是上亿行，
    而评分只需要回溯窗口那段。调用方给 start/end。
    """
    return engine.execute(
        """
        UPDATE market_data.sector_daily sd
           SET up_count = a.up_n, down_count = a.down_n, member_count = a.n,
               updated_at = now()
          FROM (SELECT m.sector_code, d.trade_date,
                       count(*) AS n,
                       count(*) FILTER (WHERE d.pct_change > 0) AS up_n,
                       count(*) FILTER (WHERE d.pct_change < 0) AS down_n
                  FROM reference_data.sector_member m
                  JOIN market_data.stock_daily d ON d.security_code = m.security_code
                 WHERE m.out_date IS NULL
                   AND (%(s)s::date IS NULL OR d.trade_date >= %(s)s)
                   AND (%(e)s::date IS NULL OR d.trade_date <= %(e)s)
                 GROUP BY 1, 2) a
         WHERE a.sector_code = sd.sector_code AND a.trade_date = sd.trade_date
        """,
        {'s': start, 'e': end}, role=role)


def recompute_turnover(trade_date: dt.date | None = None, *,
                       only_missing: bool = False, role: str = 'app') -> int:
    """
    换手率 = 成交量 ÷ **当日有效**流通股本 × 100。

    **股本要按日期分段取，不能用"当前流通股本"回算历史。** 次新股解禁前后
    股本能差几倍，用今天的股本去算三年前的换手率，算出来的是个没有意义的
    数字，而且它看起来完全正常 —— 这种错比空值危险。
    `reference_data.share_capital` 按 `change_date` 存分段，这里取"最后一个不晚于
    交易日的分段"；没有分段记录的票退回 `reference_data.security.float_share`，
    并且**只有这一步才是近似**，退回时算的是"按当前股本折算"。

    `only_missing=True` 只补空值，别把已有的值（可能来自更权威的源，
    比如新浪日线自带的换手率）覆盖掉。
    """
    inner_where = ' AND d2.turnover_rate_percent IS NULL' if only_missing else ''
    return engine.execute(
        f"""
        UPDATE market_data.stock_daily d
           SET turnover_rate_percent = round(d.volume_shares::numeric / fs.share * 100, 4)
          FROM (
            SELECT d2.security_code, d2.trade_date,
                   coalesce(
                       (SELECT c.float_share FROM reference_data.share_capital c
                         WHERE c.security_code = d2.security_code
                           AND c.change_date <= d2.trade_date
                           AND c.float_share > 0
                         ORDER BY c.change_date DESC LIMIT 1),
                       s.float_share) AS share
              FROM market_data.stock_daily d2
              JOIN reference_data.security s ON s.security_code = d2.security_code
             WHERE d2.volume_shares IS NOT NULL
               AND (%s::date IS NULL OR d2.trade_date = %s){inner_where}
          ) fs
         WHERE fs.security_code = d.security_code AND fs.trade_date = d.trade_date
           AND fs.share > 0
        """,
        (trade_date, trade_date), role=role)



def recompute_adj_factor(ts_codes: list[str] | None = None, *,
                         role: str = 'app') -> int:
    """
    由分红送配记录从最新交易日往前累乘，写进 market_data.stock_daily.adjustment_factor。

    因子的含义：`close × adjustment_factor` 是后复权价，跨除权日连续。
    校验方式（2026-08-27 实测 002594.SZ 比亚迪 2025-07-29 十送二十）：
        07-28 close 337.00 × 0.32817673 = 110.596
        07-29 close 111.42 × 0.99627859 = 111.005   ← 没有断层
    未复权的话这里是 337 → 111.42，看着像跌了 67%。

    **区间连接，不要逐行子查询。** 第一版是
        SET adjustment_factor = (SELECT cf FROM cum WHERE ex_date > d.trade_date
                          ORDER BY ex_date LIMIT 1)
    对 625 万行每行跑一次子查询，实测跑了 **15,135 秒（4.2 小时）**。
    现在把事件序列变成左闭右开区间 [上一个除权日, 本次除权日) 再和日线
    做一次范围连接，同样的结果一次扫描就够。

    `ts_codes` 给了就只重算这些票 —— 日常只有新出除权公告的那几只需要重算，
    而一只票新增一个除权事件会改写它**全部历史**的因子，所以粒度只能到票，
    不能到日期。
    """
    codes = list(ts_codes) if ts_codes else None
    n = engine.execute(
        """
        WITH ev AS (
            SELECT d.security_code, d.ex_date,
                   (1 + coalesce(d.bonus_ratio, 0) + coalesce(d.rights_ratio, 0)) AS split,
                   coalesce(d.cash_per_share, 0) AS cash
              FROM reference_data.dividend d
             WHERE %(codes)s::text[] IS NULL OR d.security_code = ANY(%(codes)s)
        ),
        px AS (
            SELECT e.security_code, e.ex_date, e.split, e.cash, p.close AS prev_close
              FROM ev e
              LEFT JOIN LATERAL (
                   SELECT s.close FROM market_data.stock_daily s
                    WHERE s.security_code = e.security_code AND s.trade_date < e.ex_date
                    ORDER BY s.trade_date DESC LIMIT 1) p ON true
        ),
        f AS (
            SELECT security_code, ex_date,
                   CASE WHEN prev_close IS NULL OR prev_close <= 0 THEN 1
                        ELSE ((prev_close - cash) / prev_close) / nullif(split, 0)
                   END AS factor
              FROM px
        ),
        cum AS (
            SELECT security_code, ex_date,
                   exp(sum(ln(nullif(factor, 0))) OVER (
                        PARTITION BY security_code ORDER BY ex_date DESC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) AS cf
              FROM f WHERE factor > 0
        ),
        rng AS (
            SELECT security_code, ex_date AS hi, cf,
                   lag(ex_date) OVER (PARTITION BY security_code ORDER BY ex_date) AS lo
              FROM cum
        )
        UPDATE market_data.stock_daily d
           SET adjustment_factor = r.cf
          FROM rng r
         WHERE r.security_code = d.security_code
           AND d.trade_date < r.hi
           AND (r.lo IS NULL OR d.trade_date >= r.lo)
        """, {'codes': codes}, role=role)
    # 最后一个除权日及其之后的行因子恒为 1（后复权基准就是最新价）
    n += engine.execute(
        """
        UPDATE market_data.stock_daily d
           SET adjustment_factor = 1.0
          FROM (SELECT security_code, max(ex_date) AS last_ex
                  FROM reference_data.dividend
                 WHERE %(codes)s::text[] IS NULL OR security_code = ANY(%(codes)s)
                 GROUP BY security_code) x
         WHERE x.security_code = d.security_code
           AND d.trade_date >= x.last_ex
           AND (d.adjustment_factor IS DISTINCT FROM 1.0)
        """, {'codes': codes}, role=role)
    return n


def recompute_pct_chg_ex(ts_codes: list[str] | None = None, *,
                         role: str = 'app') -> int:
    """
    修正**除权日当天**的 pre_close / pct_change / change_yuan。

    为什么必须单独修：`pre_close` 是按交易日历取的上一日收盘（未复权），
    所以除权日那一行的涨跌幅是"除权后价 ÷ 除权前价 − 1"，
    比亚迪 2025-07-29 因此显示 **−66.94%**，而当天真实涨跌是 +0.37%。
    这个假跌幅会一路污染下去：形态分、均线、板块涨幅与广度、市场情绪
    ——凡是读 pct_change 的地方全中招，而且看不出异常（它就是个很大的负数）。

    正确的除权价 = 上日收盘 × 上日因子 ÷ 当日因子。只有因子发生跳变的那些
    行需要改，实测全市场 2.4 万个除权事件量级，几秒钟的事。

    **只在 adjustment_factor 已经算好之后跑**，否则因子全 NULL，一行都匹配不上。
    """
    codes = list(ts_codes) if ts_codes else None
    return engine.execute(
        """
        WITH s AS (
            SELECT security_code, trade_date, close, adjustment_factor,
                   lag(close)      OVER w AS prev_close,
                   lag(adjustment_factor) OVER w AS prev_f
              FROM market_data.stock_daily
             WHERE adjustment_factor IS NOT NULL
               AND (%(codes)s::text[] IS NULL OR security_code = ANY(%(codes)s))
            WINDOW w AS (PARTITION BY security_code ORDER BY trade_date)
        ),
        ex AS (
            SELECT security_code, trade_date,
                   round(prev_close * prev_f / adjustment_factor, 4) AS adj_pre
              FROM s
             WHERE prev_close IS NOT NULL AND prev_close > 0
               AND prev_f IS NOT NULL AND adjustment_factor > 0
               AND prev_f <> adjustment_factor
        )
        UPDATE market_data.stock_daily d
           SET pre_close   = ex.adj_pre,
               change_yuan = round(d.close - ex.adj_pre, 4),
               pct_change     = round((d.close - ex.adj_pre) / ex.adj_pre * 100, 4)
          FROM ex
         WHERE ex.security_code = d.security_code AND ex.trade_date = d.trade_date
           AND ex.adj_pre > 0
        """, {'codes': codes}, role=role)



def recompute_pre_close(trade_date: dt.date | None = None, *, role: str = 'app',
                        force: bool = False) -> int:
    """
    从上一交易日的收盘价回填 pre_close / change_yuan / pct_change。

    `force=True` 连已有值一起重算。**换数据源时必须 force**：用 vipdoc
    覆盖了网络兜底源写的 close 之后，pre_close/pct_change 还留着兜底源那一版，
    于是同一行里 close 是通达信口径、涨跌幅是新浪口径 —— 这种混口径最难查，
    因为每一列单独看都正常。
    """
    return engine.execute(
        f"""
        UPDATE market_data.stock_daily d
           SET pre_close = p.close,
               change_yuan = d.close - p.close,
               pct_change = CASE WHEN p.close > 0
                              THEN round((d.close - p.close) / p.close * 100, 4) END
          FROM reference_data.trade_calendar c
          JOIN market_data.stock_daily p ON p.trade_date = c.prev_open_date
         WHERE c.calendar_date = d.trade_date AND p.security_code = d.security_code
           {'' if force else 'AND d.pre_close IS NULL'}
           AND (%s::date IS NULL OR d.trade_date = %s)
        """,
        (trade_date, trade_date), role=role)

