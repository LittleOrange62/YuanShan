# -*- coding: utf-8 -*-
"""逐票「每日量能增长倍数」：今天成交量 ÷ 这只票上一根日线的成交量。

用户 2026-10-07 的原话就是这一句：「有效交易日进行了逐日量能比对，计算每日增长倍数 ——
就是今天的成交量是前一天的多少倍……顺便帮我计算所有票近一年的每日量能增长倍数，
计算结果放进数据库，应该是要新建一张表，后续定时任务每天都要跑一遍的。」
落库表 `analytics.volume_ratio_daily`（DDL：`app/db/sql/25_volume_ratio_daily.sql`）。

---- 三条口径（与 DDL 头部一字不差，改算法必须同批改两处）----

1. **“前一天”是这只票自己的上一根日线**，不是全场上一交易日。停牌十天的票复牌首日，
   分母是十天前的量。所以 `prev_trade_date` / `prev_gap_days` 必须与倍数一起交：
   只读 `vol_ratio` 会把「停牌三个月后复牌放量」读成「今天突然放量」。
2. **算不出就 NULL，不写 0 也不写 1**。0 会被读成"缩量到没有"、1 会被读成"量没变"，
   两个都是假读数；缺的原因写进 `missing` 列，与 NULL 成对出现。
3. **量用 `volume_shares`（股数原值），不用成交额**。用额就把价格涨跌混进倍数里
   （价涨一倍、量不变 → 倍数会被做成 2.0）。

---- 为什么是服务端一条 INSERT..SELECT ----

第一版按天写（每天一个 `distinct on` 找上一根），实测**单日 45.3 秒**、
近一年 241 场要跑三个小时 —— 因为那一天要把整张 640 万行的日线往前扫一遍找上一根。
改成一次窗口扫描（`LAG() OVER (PARTITION BY security_code ORDER BY trade_date)`）后
整段一次算完，写侧也不用把 132 万行拉回 Python。留这段数字是为了下次有人
按"每天单独查上一次"的直觉改写时，知道那条路实测过、走不通。

用法：
    python -m app.compute.volume_ratio --date 2026-09-30            # 只算这一天
    python -m app.compute.volume_ratio --recent 5                   # 往前 5 个交易日
    python -m app.compute.volume_ratio --start 2025-10-01 --end 2026-09-30   # 近一年整段
    python -m app.compute.volume_ratio --start ... --end ... --dry-run       # 只数不写
    python -m app.compute.volume_ratio --date 2026-09-30 --verify   # 抽一只票逐日复核

退出码：0 成功 / 1 没有可算的交易日 / 2 --verify 不通过。
"""
from __future__ import annotations

import argparse
import datetime as dt

from app.db import engine

SPEC_VERSION = 'vr-1'

#: 单日/短窗口回溯时往前多扫多少天找"上一根日线"。400 个自然日足够覆盖绝大多数停牌；
#: 超出这个窗口才叫 no_prev_bar（长期停牌与本库首场），gap 列会把可疑的那几条自己露出来。
SCAN_BACK_DAYS = 400

UPSERT_SQL = """
WITH s AS (
    SELECT security_code, trade_date, volume_shares,
           LAG(volume_shares) OVER w AS prev_volume,
           LAG(trade_date)     OVER w AS prev_trade_date
      FROM market_data.stock_daily
     WHERE trade_date BETWEEN %(scan_from)s AND %(scan_to)s
       WINDOW w AS (PARTITION BY security_code ORDER BY trade_date)
),
c AS (
    SELECT * FROM s WHERE trade_date BETWEEN %(d_from)s AND %(d_to)s
)
INSERT INTO analytics.volume_ratio_daily
      (trade_date, security_code, spec_version, volume_shares,
       prev_trade_date, prev_volume, prev_gap_days, vol_ratio, missing)
SELECT trade_date, security_code, %(spec)s, volume_shares,
       prev_trade_date, prev_volume,
       CASE WHEN prev_trade_date IS NULL THEN NULL
            ELSE (trade_date - prev_trade_date)::int END,
       CASE WHEN volume_shares IS NULL          THEN NULL
            WHEN prev_volume IS NULL            THEN NULL
            WHEN prev_volume <= 0               THEN NULL
            ELSE round(volume_shares::numeric / prev_volume::numeric, 4) END,
       CASE WHEN volume_shares IS NULL  THEN 'no_volume'
            WHEN prev_volume IS NULL    THEN 'no_prev_bar'
            WHEN prev_volume <= 0       THEN 'zero_prev_volume'
            ELSE NULL END
  FROM c
ON CONFLICT (security_code, trade_date, spec_version) DO UPDATE SET
       volume_shares   = EXCLUDED.volume_shares,
       prev_trade_date = EXCLUDED.prev_trade_date,
       prev_volume     = EXCLUDED.prev_volume,
       prev_gap_days   = EXCLUDED.prev_gap_days,
       vol_ratio       = EXCLUDED.vol_ratio,
       missing         = EXCLUDED.missing,
       calculated_at   = now()
"""

COUNT_SQL = """
WITH s AS (
    SELECT security_code, trade_date, volume_shares,
           LAG(volume_shares) OVER w AS prev_volume,
           LAG(trade_date)     OVER w AS prev_trade_date
      FROM market_data.stock_daily
     WHERE trade_date BETWEEN %(scan_from)s AND %(scan_to)s
       WINDOW w AS (PARTITION BY security_code ORDER BY trade_date)
)
SELECT count(*)                                                              AS rows,
       count(*) FILTER (WHERE s.trade_date BETWEEN %(d_from)s AND %(d_to)s)  AS out_rows,
       count(*) FILTER (WHERE s.trade_date BETWEEN %(d_from)s AND %(d_to)s
                          AND s.prev_volume IS NOT NULL AND s.prev_volume > 0
                          AND s.volume_shares IS NOT NULL)                   AS with_ratio,
       count(*) FILTER (WHERE s.trade_date BETWEEN %(d_from)s AND %(d_to)s
                          AND (s.prev_volume IS NULL OR s.prev_volume <= 0
                               OR s.volume_shares IS NULL))                  AS missing_rows
  FROM s
"""


def ratio_of(volume, prev_volume) -> tuple[float | None, str | None]:
    """纯判据：给 (今日量, 上一根量) 交回 (倍数, 缺因)。不碰库，单测打这一条。

    缺的一侧一律 `(None, 原因)` —— 返回 0.0 或 1.0 都会被读成真实读数。
    """
    if volume is None:
        return None, 'no_volume'
    if prev_volume is None:
        return None, 'no_prev_bar'
    try:
        volume = float(volume)
        prev = float(prev_volume)
    except (TypeError, ValueError):
        return None, 'unparseable_volume'
    if prev <= 0:
        return None, 'zero_prev_volume'
    return round(volume / prev, 4), None


def sessions_upto(target: dt.date | None, n: int) -> list[dt.date]:
    """库里实际有日线的最近 n 个交易日（含 target）。target 不给就用库里最新那天。"""
    if target is None:
        target = engine.scalar('SELECT max(trade_date) FROM market_data.stock_daily')
    if target is None:
        return []
    rows = engine.query(
        'SELECT DISTINCT trade_date FROM market_data.stock_daily '
        'WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT %s',
        (target, max(n, 1)), role='reader')
    return [r['trade_date'] for r in rows][::-1]


def _params(d_from: dt.date, d_to: dt.date, scan_from: dt.date | None = None) -> dict:
    return {'scan_from': scan_from or (d_from - dt.timedelta(days=SCAN_BACK_DAYS)),
            'scan_to': d_to, 'd_from': d_from, 'd_to': d_to, 'spec': SPEC_VERSION}


def compute_range(d_from: dt.date, d_to: dt.date, *, dry_run: bool = False,
                  scan_from: dt.date | None = None) -> dict:
    """一段日期区间的量能倍数。整段一次窗口扫描，不逐日查上一根（见模块说明的 45.3s）。"""
    p = _params(d_from, d_to, scan_from)
    if dry_run:
        return engine.query_one(COUNT_SQL, p, role='reader') or {}
    written = engine.execute(UPSERT_SQL, p, role='etl')
    return {'written': written, **p}


def verify(day: dt.date) -> tuple[bool, list[str]]:
    """常识体检：这一天的行是否自洽。

    四条都是"看着对但可能反着错"的形状，靠读码发现不了，只能现查：
      · 库里这一天的行数与表里这一天几乎不等 → 有票整批没进来；
      · `vol_ratio` 有值而 `missing` 也有值 → 成对约束破了（写入侧改了判据没改 CASE）；
      · 倍数为 0 或负 → 分母处理漏了；
      · 极端倍数（>50 或 <0.02）的只数要报出来，让人自己看一眼是不是停牌复牌。
    """
    bad: list[str] = []
    src = engine.scalar(
        'SELECT count(*) FROM market_data.stock_daily WHERE trade_date = %s', (day,))
    got = engine.query_one(
        'SELECT count(*) AS n, '
        '       count(*) FILTER (WHERE vol_ratio IS NOT NULL AND missing IS NOT NULL) AS both, '
        '       count(*) FILTER (WHERE vol_ratio IS NOT NULL AND missing IS NULL) AS have, '
        '       count(*) FILTER (WHERE vol_ratio <= 0) AS nonpos, '
        '       count(*) FILTER (WHERE vol_ratio > 50 OR vol_ratio < 0.02) AS extreme, '
        '       count(*) FILTER (WHERE prev_gap_days > 30) AS long_gap '
        '  FROM analytics.volume_ratio_daily '
        ' WHERE trade_date = %s AND spec_version = %s', (day, SPEC_VERSION), role='reader') or {}
    n = int(got.get('n') or 0)
    if not n:
        return False, ['%s 这一行都没有 —— 步骤没跑还是跑了没写进去？' % day]
    if abs(n - int(src or 0)) > 5:
        bad.append('当日日线 %s 行，量能倍数只有 %s 行（差 %s）' % (src, n, int(src or 0) - n))
    if int(got.get('both') or 0):
        bad.append('有 %s 行同时带倍数和缺因 —— 成对约束破了' % got['both'])
    if int(got.get('nonpos') or 0):
        bad.append('有 %s 行倍数 ≤ 0，分母处理漏了' % got['nonpos'])
    print('  体检 %s：行 %s / 有倍数 %s / 缺因 %s / 极端 %s / 间隔>30天 %s'
          % (day, n, got.get('have'), n - int(got.get('have') or 0),
             got.get('extreme'), got.get('long_gap')))
    return (not bad), bad


def sample_one_day(day: dt.date) -> list[dict]:
    """抽一只票在这一天的读数（复核用：拿日线自己除一遍）。"""
    return engine.query(
        'SELECT security_code, volume_shares, prev_trade_date, prev_volume, prev_gap_days, '
        '       vol_ratio, missing '
        '  FROM analytics.volume_ratio_daily '
        ' WHERE trade_date = %s AND spec_version = %s AND vol_ratio IS NOT NULL '
        ' ORDER BY vol_ratio DESC LIMIT 3', (day, SPEC_VERSION), role='reader')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='逐票每日量能增长倍数（成交量 ÷ 上一根日线成交量）')
    ap.add_argument('--date', help='只算这一个交易日（YYYY-MM-DD）')
    ap.add_argument('--recent', type=int, default=1, help='从 --date（或库里最新日）往前几个交易日')
    ap.add_argument('--start', help='区间起日（含）；给了就按区间整段跑')
    ap.add_argument('--end', help='区间止日（含）')
    ap.add_argument('--dry-run', action='store_true', help='只数行数不写库')
    ap.add_argument('--verify', action='store_true', help='算完对这一天做常识体检')
    args = ap.parse_args(argv)

    if args.start and args.end:
        d_from = dt.date.fromisoformat(args.start)
        d_to = dt.date.fromisoformat(args.end)
        days = (d_to - d_from).days + 1
        print('=== 量能倍数（%s）：区间 %s .. %s（自然日 %d 天）%s'
              % (SPEC_VERSION, d_from, d_to, days, '（dry-run）' if args.dry_run else ''))
        # 整段一次扫描：窗口起点再往前留 SCAN_BACK_DAYS，好让区间首日也有"上一根"
        out = compute_range(d_from, d_to, dry_run=args.dry_run)
        if args.dry_run:
            print('  会写 %s 行（其中能算出倍数 %s 行、缺因 %s 行）'
                  % (out.get('out_rows'), out.get('with_ratio'), out.get('missing_rows')))
        else:
            print('  写入 %s 行' % out.get('written'))
        return 0

    target = dt.date.fromisoformat(args.date) if args.date else None
    days = sessions_upto(target, max(args.recent, 1))
    if not days:
        print('没有可算的交易日（库里日线为空？）')
        return 1
    d_from, d_to = days[0], days[-1]
    print('=== 量能倍数（%s）：%s .. %s（%d 个交易日）%s'
          % (SPEC_VERSION, d_from, d_to, len(days), '（dry-run）' if args.dry_run else ''))
    out = compute_range(d_from, d_to, dry_run=args.dry_run)
    if args.dry_run:
        print('  会写 %s 行（能算出倍数 %s、缺因 %s）'
              % (out.get('out_rows'), out.get('with_ratio'), out.get('missing_rows')))
        return 0
    print('  写入 %s 行' % out.get('written'))
    for r in sample_one_day(d_to)[:2]:
        print('    样例 %s：%s 的量 %s ÷ %s 的 %s = %s 倍（隔 %s 天）'
              % (r['security_code'], d_to, r['volume_shares'], r['prev_trade_date'],
                 r['prev_volume'], r['vol_ratio'], r['prev_gap_days']))
    if args.verify:
        ok, bad = verify(d_to)
        for b in bad:
            print('  ⚠ ' + b)
        print('  体检：%s' % ('通过' if ok else '不通过'))
        return 0 if ok else 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
