# -*- coding: utf-8 -*-
"""趋势股形状榜（五日线趋势 × 两把新高尺子 × 放量两口径）：`analytics.trend_stock_daily`。

口径出自同事那份《涨停分析、板块分析、趋势股》三页说明（`docs/涨停分析-板块分析-趋势股——from洪杰.md`），
本机全部按**本地日线**自己算，不读外部涨停池，所以能回算全史、能对账、不依赖外部接口活着。

一处写完、界面上不许复述的三条判据：

  · **五日线趋势**：连续若干个交易日，每一天收盘都**严格**高于「截止这一天的 5 日收盘均线」
    （简单算术平均，不复权）。`ma5_streak` 问"最近连着几天"，`shape5`/`shape10` 问"整个窗口一天都没掉过"，
    两者不是一个问题的答案 —— 掉一天就整档不成立。窗口至少要 9 根收盘价才谈得上形状（第一个 MA5 要 5 根，
    再往前数 4 根），不足 9 根一律 `reject=bars_lt_9`，**不拿手头的几根凑一个偏高的均线**。
  · **10/20 日新高**：当日收盘 **高于前 10（前 20）根 K 线的最高价**。比的是前 N 根的 `high`、**不含当日**；
    它不等于"历史新高"，也不等于库里那种含当日、比收盘的自然日新高。
  · **放量两口径**：当日量 > 前一交易日量；当日量 > **前 5 个交易日量的平均**（同样不含当日，
    含当日会被自己的巨量把基准抬高，系统性读成"没放量"）。

入选（`selected5` / `selected10`）= 上面形状 × **当天收盘没有涨停** × 当日涨幅算得出来 × 板块归得进
主板/创业板/科创板（北交所不纳入，整行不写）。当天涨停那一刀是**来源口径**指定的：板块卡片里那种
"固定看 5 天、可以包含当天涨停"的读法与本页不是同一份名单，界面别互相顶。

缺项一律 NULL 不按 0（0 会被读成"没站上均线"或"新高 false"，方向是反的）；
没入选的票也留行，并交 `reject` 短码 —— 有这一列，界面才分得开"这只票形状不对"与"这台机器没算它"。

用法：
    python -m app.compute.trend_stock                        # 最新有日线的那一天
    python -m app.compute.trend_stock --date 2026-10-14
    python -m app.compute.trend_stock --recent 5
    python -m app.compute.trend_stock --since 2025-10-15 --skip-done --max-minutes 30
    python -m app.compute.trend_stock --dry-run              # 只算不写，打前几条与分布

退出码：0 成功 / 1 没有可算的交易日 / 2 一趟都没写进去 / 3 有分段算失败 / 4 自限预算用完。
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from typing import Any, Dict, List, Optional, Sequence

from app.db import engine

SPEC_VERSION = 'trend-1'

SHAPE_WINDOWS = (5, 10)       # 两档周期（用户那边页面就是这两个按钮）
MA_N = 5                      # 均线根数
MIN_BARS = 9                  # 谈得上"连续 5 天各自过 MA5"所需的最少收盘价根数
NH_WINDOWS = (10, 20)         # 两把新高尺子
VMA_N = 5                     # 放量第二把尺子的均量根数（不含当日）
CHUNK_DAYS = 20               # 一趟 SQL 算几个目标交易日（回算时分段，别让窗口帧无限长）
LOOKBACK_EXTRA = 30           # 分段起点再多取几根：20 新高 + 5 均量 + 一点余量

# 母体：沪市 60/68、深市 000/001/002/003/300/301/302、北交所不算 —— 这些都由
# `reference_data.security.board` 承担，这里只列"进表"的三档。
BOARDS = ('主板', '创业板', '科创板')

_COLUMNS = ('trade_date', 'security_code', 'board', 'close', 'pct_change', 'amount_yuan',
            'float_mv_yuan', 'bars_used', 'above_ma5', 'ma5_streak', 'shape5', 'shape10',
            'nh_10', 'nh_20', 'vol_gt_prev', 'vol_gt_vma5', 'sealed', 'boards',
            'limit_ups_5', 'limit_ups_10', 'selected5', 'selected10', 'reject', 'spec_version')
_PK = {'security_code', 'trade_date', 'spec_version'}
UPSERT = ("insert into analytics.trend_stock_daily ({cols}) values ({ph}) "
          "on conflict (security_code, trade_date, spec_version) do update set {upd}, "
          "calculated_at = now()")

# 一趟 SQL 算 CHUNK_DAYS 个目标日：每股按 trade_date 排好，用窗口帧现算五个读数，
# 最后只把落在目标日区间里的那几行交出来。三条刻意的写法：
#   1. MA5 / 均量 / 最高价这些帧**都带根数检查**（`*_n`），帧不满就置 NULL，
#      不拿手头两三根算个偏高的均线 —— 那正是"次新票看着像走强"的来源。
#   2. 形状用 `bool_and(coalesce(above,false))`：`bool_and` 会**跳过 NULL**，
#      不 coalesce 的话均线还没算出来的那几天会被无声地当成"过线了"。
#   3. 新高与均量的帧是 `... AND 1 PRECEDING`（**不含当日**），含当日就变成自己跟自己比。
CORE_SQL = """
WITH px AS (
    SELECT d.security_code, d.trade_date, d.close, d.high, d.volume_shares, d.amount_yuan,
           d.pct_change, d.limit_up, s.board,
           row_number() OVER w                                      AS rn,
           count(*)   OVER w                                        AS bars_used,
           avg(d.close)   OVER (PARTITION BY d.security_code ORDER BY d.trade_date
                                ROWS BETWEEN %(ma_prev)s PRECEDING AND CURRENT ROW)             AS ma5,
           count(*)       OVER (PARTITION BY d.security_code ORDER BY d.trade_date
                                ROWS BETWEEN %(ma_prev)s PRECEDING AND CURRENT ROW)             AS ma5_n,
           max(d.high)    OVER (PARTITION BY d.security_code ORDER BY d.trade_date
                                ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)                      AS hi10,
           count(*)       OVER (PARTITION BY d.security_code ORDER BY d.trade_date
                                ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)                      AS hi10_n,
           max(d.high)    OVER (PARTITION BY d.security_code ORDER BY d.trade_date
                                ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)                      AS hi20,
           count(*)       OVER (PARTITION BY d.security_code ORDER BY d.trade_date
                                ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)                      AS hi20_n,
           lag(d.volume_shares) OVER (PARTITION BY d.security_code ORDER BY d.trade_date)       AS vprev,
           avg(d.volume_shares) OVER (PARTITION BY d.security_code ORDER BY d.trade_date
                                ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING)                       AS vma5,
           count(*)       OVER (PARTITION BY d.security_code ORDER BY d.trade_date
                                ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING)                       AS vma5_n,
           count(*)       OVER (PARTITION BY d.security_code ORDER BY d.trade_date
                                ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)                       AS w5_n,
           count(*)       OVER (PARTITION BY d.security_code ORDER BY d.trade_date
                                ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)                       AS w10_n
      FROM market_data.stock_daily d
      JOIN reference_data.security s USING (security_code)
     WHERE d.trade_date >= %(w_from)s AND d.trade_date <= %(d_hi)s
       AND d.close IS NOT NULL
       AND s.asset_type = 'stock' AND s.board = ANY(%(boards)s)
    WINDOW w AS (PARTITION BY d.security_code ORDER BY d.trade_date)
),
flg AS (
    SELECT px.*,
           CASE WHEN ma5_n >= %(ma_n)s AND ma5 IS NOT NULL THEN close > ma5 END           AS above,
           CASE WHEN limit_up IS NOT NULL AND close >= limit_up - 0.005 THEN true
                WHEN limit_up IS NULL THEN NULL ELSE false END                            AS sealed
      FROM px
),
cum AS (
    SELECT flg.*,
           sum(CASE WHEN coalesce(above, false) THEN 1 ELSE 0 END)
               OVER (PARTITION BY security_code ORDER BY trade_date
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)           AS cum_above,
           sum(CASE WHEN coalesce(sealed, false) THEN 1 ELSE 0 END)
               OVER (PARTITION BY security_code ORDER BY trade_date
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)           AS cum_seal,
           bool_and(CASE WHEN coalesce(above, false) THEN true ELSE false END)
               OVER (PARTITION BY security_code ORDER BY trade_date
                     ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)                   AS shape5_raw,
           bool_and(CASE WHEN coalesce(above, false) THEN true ELSE false END)
               OVER (PARTITION BY security_code ORDER BY trade_date
                     ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)                   AS shape10_raw,
           count(*) FILTER (WHERE coalesce(sealed, false))
               OVER (PARTITION BY security_code ORDER BY trade_date
                     ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)                   AS limit_ups_5,
           count(*) FILTER (WHERE coalesce(sealed, false))
               OVER (PARTITION BY security_code ORDER BY trade_date
                     ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)                   AS limit_ups_10
      FROM flg
),
-- ⚠️ 连续段的写法（这一处和 `app/compute/board_node.py` 一起吃过教训）：段键 = `rn - cum_*`，
--    而且**先筛掉不满足的那一行、再开窗**。反过来写（"掉线那天组号 +1"却在含掉线行的分区里数
--    row_number）会把掉线那一天和下一段并进同一组，连着天数与第几板整体偏 1。
arow AS (
    SELECT security_code, trade_date,
           row_number() OVER (PARTITION BY security_code, (rn - cum_above) ORDER BY trade_date) AS ma5_streak
      FROM cum
     WHERE coalesce(above, false)
),
srow AS (
    SELECT security_code, trade_date,
           row_number() OVER (PARTITION BY security_code, (rn - cum_seal) ORDER BY trade_date)  AS boards
      FROM cum
     WHERE coalesce(sealed, false)
),
cap AS (                    -- 流通股本按 change_date 分段取"最后一个不晚于交易日的那段"
    SELECT dd.security_code, dd.trade_date,
           coalesce((SELECT c.float_share FROM reference_data.share_capital c
                      WHERE c.security_code = dd.security_code
                        AND c.change_date <= dd.trade_date AND c.float_share > 0
                      ORDER BY c.change_date DESC LIMIT 1),
                    s.float_share)                                       AS share
      FROM (SELECT DISTINCT security_code, trade_date FROM cum
             WHERE trade_date BETWEEN %(d_lo)s AND %(d_hi)s) dd
      JOIN reference_data.security s USING (security_code)
)
SELECT c.trade_date, c.security_code, c.board, c.close, c.pct_change, c.amount_yuan,
       CASE WHEN cp.share > 0 THEN round(cp.share * c.close, 4) END      AS float_mv_yuan,
       c.bars_used, c.above AS above_ma5, coalesce(a.ma5_streak, 0)      AS ma5_streak,
       (c.shape5_raw  AND c.w5_n  >= 5  AND c.bars_used >= %(min_bars)s)  AS shape5,
       (c.shape10_raw AND c.w10_n >= 10 AND c.bars_used >= %(min_bars)s)  AS shape10,
       CASE WHEN c.hi10_n >= 10 THEN c.close > c.hi10 END                 AS nh_10,
       CASE WHEN c.hi20_n >= 20 THEN c.close > c.hi20 END                 AS nh_20,
       CASE WHEN c.vprev > 0 THEN c.volume_shares > c.vprev END           AS vol_gt_prev,
       CASE WHEN c.vma5_n >= %(vma_n)s AND c.vma5 > 0
            THEN c.volume_shares > c.vma5 END                             AS vol_gt_vma5,
       coalesce(c.sealed, false) AS sealed, s.boards,
       CASE WHEN c.w5_n  >= 5  THEN c.limit_ups_5  END                    AS limit_ups_5,
       CASE WHEN c.w10_n >= 10 THEN c.limit_ups_10 END                    AS limit_ups_10
  FROM cum c
  LEFT JOIN arow a USING (security_code, trade_date)
  LEFT JOIN srow s USING (security_code, trade_date)
  LEFT JOIN cap cp USING (security_code, trade_date)
 WHERE c.trade_date BETWEEN %(d_lo)s AND %(d_hi)s
 ORDER BY c.security_code, c.trade_date
"""


def _reject(shape: bool, shape10: bool, sealed: bool, pct: Any, bars: Any) -> Optional[str]:
    """把"为什么不在榜上"收成短码。两条周期都不成立时只报 5 日那条（更常读的那一档）。"""
    codes: List[str] = []
    if (bars or 0) < MIN_BARS:
        codes.append('bars_lt_9')
    if not shape and not shape10:
        codes.append('shape5_not' if not shape else 'shape10_not')
    elif not shape:
        codes.append('shape5_not')
    elif not shape10:
        codes.append('shape10_not')
    if sealed:
        codes.append('sealed_today')
    if pct is None:
        codes.append('no_pct')
    return ','.join(codes) or None


def sessions(lo: dt.date, hi: dt.date) -> List[dt.date]:
    rows = engine.query('SELECT DISTINCT trade_date FROM market_data.stock_daily '
                        ' WHERE trade_date BETWEEN %s AND %s ORDER BY 1', (lo, hi), role='reader')
    return [r['trade_date'] for r in rows]


def all_sessions_up_to(hi: dt.date, n: int) -> List[dt.date]:
    rows = engine.query('SELECT DISTINCT trade_date FROM market_data.stock_daily '
                        ' WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT %s', (hi, n), role='reader')
    return sorted(r['trade_date'] for r in rows)


def compute_chunk(d_lo: dt.date, d_hi: dt.date) -> List[Dict[str, Any]]:
    """一趟算 [d_lo, d_hi] 这几个目标日。窗口起点 = d_lo 再往前 LOOKBACK_EXTRA + max(NH) 根。"""
    need = CHUNK_DAYS + LOOKBACK_EXTRA + max(NH_WINDOWS) + MIN_BARS
    days = all_sessions_up_to(d_hi, need)
    if not days or days[-1] < d_lo:
        return []
    raw = engine.query(CORE_SQL, {
        'w_from': days[0], 'd_lo': d_lo, 'd_hi': d_hi, 'boards': list(BOARDS),
        'ma_prev': MA_N - 1, 'ma_n': MA_N, 'vma_n': VMA_N, 'min_bars': MIN_BARS,
    }, role='reader') or []
    out: List[Dict[str, Any]] = []
    for r in raw:
        shape5 = bool(r['shape5'])
        shape10 = bool(r['shape10'])
        sealed = bool(r['sealed'])
        sel5 = shape5 and not sealed and r['pct_change'] is not None
        sel10 = shape10 and not sealed and r['pct_change'] is not None
        out.append({
            'trade_date': r['trade_date'], 'security_code': r['security_code'], 'board': r['board'],
            'close': r['close'], 'pct_change': r['pct_change'], 'amount_yuan': r['amount_yuan'],
            'float_mv_yuan': r['float_mv_yuan'], 'bars_used': r['bars_used'],
            'above_ma5': r['above_ma5'], 'ma5_streak': r['ma5_streak'],
            'shape5': shape5, 'shape10': shape10,
            'nh_10': r['nh_10'], 'nh_20': r['nh_20'],
            'vol_gt_prev': r['vol_gt_prev'], 'vol_gt_vma5': r['vol_gt_vma5'],
            'sealed': sealed, 'boards': r['boards'],
            'limit_ups_5': r['limit_ups_5'], 'limit_ups_10': r['limit_ups_10'],
            'selected5': sel5, 'selected10': sel10,
            'reject': None if (sel5 or sel10) else _reject(shape5, shape10, sealed,
                                                           r['pct_change'], r['bars_used']),
            'spec_version': SPEC_VERSION,
        })
    return out


def write(rows: Sequence[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    cols = ', '.join(_COLUMNS)
    ph = ', '.join(['%s'] * len(_COLUMNS))
    upd = ', '.join(f'{c} = excluded.{c}' for c in _COLUMNS if c not in _PK)
    payload = [tuple(r.get(c) for c in _COLUMNS) for r in rows]
    with engine.cursor(role='etl') as cur:
        cur.executemany(UPSERT.format(cols=cols, ph=ph, upd=upd), payload)
    return len(rows)


def verify() -> tuple[bool, list[str]]:
    """体检：判据自洽，不是"有没有数"。"""
    bad: list[str] = []
    agg = engine.query(
        'SELECT spec_version, count(*) n, count(*) FILTER (WHERE ma5_streak > bars_used) 连续超过总根数, '
        '       count(*) FILTER (WHERE selected5 AND NOT shape5) 入选但形状不成立, '
        '       count(*) FILTER (WHERE selected5 AND sealed) 入选但当天涨停, '
        '       count(*) FILTER (WHERE reject IS NULL AND NOT (selected5 OR selected10)) 没入选却没理由, '
        '       count(*) FILTER (WHERE float_mv_yuan IS NULL) 无流通市值 '
        '  FROM analytics.trend_stock_daily GROUP BY 1', (), role='reader') or []
    for a in agg:
        print('  %s：行 %s／连续天数超过总根数 %s／入选但形状不成立 %s／入选但当天涨停 %s'
              '／没入选却没理由 %s／无流通市值 %s'
              % (a['spec_version'], a['n'], a['连续超过总根数'], a['入选但形状不成立'],
                 a['入选但当天涨停'], a['没入选却没理由'], a['无流通市值']))
        for k in ('连续超过总根数', '入选但形状不成立', '入选但当天涨停', '没入选却没理由'):
            if int(a[k] or 0):
                bad.append('%s 有 %s 行 %s（判据不自洽）' % (a['spec_version'], a[k], k))
    if not agg:
        bad.append('一张行都没有 —— 步骤没跑，还是母体为空？')
    return (not bad), bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='趋势股形状榜：analytics.trend_stock_daily')
    ap.add_argument('--date', help='只算这一天（YYYY-MM-DD）')
    ap.add_argument('--recent', type=int, default=1, help='往前数 N 个交易日')
    ap.add_argument('--since', help='从这一天起把所有交易日都算一遍（回算用）')
    ap.add_argument('--skip-done', action='store_true', help='跳过表里已有该 spec_version 的场')
    ap.add_argument('--max-minutes', type=float, help='自限预算，到点就停并交还剩几场')
    ap.add_argument('--dry-run', action='store_true', help='只算不写')
    ap.add_argument('--verify', action='store_true', help='写完后体检')
    a = ap.parse_args(argv)

    d_end = (dt.date.fromisoformat(a.date) if a.date else
             engine.scalar('SELECT max(trade_date) FROM market_data.stock_daily', role='reader'))
    if d_end is None:
        print('库里没有日线，谈不上趋势股')
        return 1
    if a.since:
        targets = sessions(dt.date.fromisoformat(a.since), d_end)
    else:
        targets = all_sessions_up_to(d_end, max(1, a.recent))
    if a.skip_done and targets:
        done = {r['trade_date'] for r in engine.query(
            'SELECT DISTINCT trade_date FROM analytics.trend_stock_daily WHERE spec_version = %s',
            (SPEC_VERSION,), role='reader') or []}
        targets = [t for t in targets if t not in done]
    if not targets:
        print('没有要算的交易日（%s..%s，或这一批已算过）' % (d_end, SPEC_VERSION))
        return 1
    print('=== 趋势股形状榜（%s）：要算 %d 场 %s .. %s%s'
          % (SPEC_VERSION, len(targets), targets[0], targets[-1], '（dry-run）' if a.dry_run else ''))

    t0 = time.time()
    wrote_any = False
    failed = 0
    for i in range(0, len(targets), CHUNK_DAYS):
        seg = targets[i:i + CHUNK_DAYS]
        if a.max_minutes and (time.time() - t0) / 60 > a.max_minutes:
            print('  ⏱ 自限预算 %s 分钟用完，还剩 %d 场没算（重跑带 --skip-done 接着算）'
                  % (a.max_minutes, len(targets) - i))
            return 4
        rows = compute_chunk(seg[0], seg[-1])
        by: Dict[str, int] = {}
        for r in rows:
            if r['selected5']:
                by[r['board']] = by.get(r['board'], 0) + 1
        print('  %s .. %s：交出 %d 行，5 日档入选 主板 %s／创业板 %s／科创板 %s'
              % (seg[0], seg[-1], len(rows), by.get('主板', 0), by.get('创业板', 0), by.get('科创板', 0)))
        if a.dry_run:
            for r in rows[:3]:
                if r['selected5']:
                    print('    样本 %s %s：%s 收盘 %s 涨 %s%% streak %s／nh10 %s／nh20 %s／放量 %s·%s'
                          % (r['security_code'], r['board'], r['trade_date'], r['close'], r['pct_change'],
                             r['ma5_streak'], r['nh_10'], r['nh_20'], r['vol_gt_prev'], r['vol_gt_vma5']))
            continue
        if not rows:
            print('  ⚠ 这一段交出 0 行（窗口没覆盖到？先看 stock_daily 这段有没有数据）')
            failed += 1
            continue
        wrote_any = True
        write(rows)

    if a.verify:
        ok, bad = verify()
        for b in bad:
            print('  ⚠ ' + b)
        print('  体检：%s' % ('通过' if ok else '不通过'))
        return 0 if ok else 2
    if failed:
        return 3
    return 0 if wrote_any or a.dry_run else 2


if __name__ == '__main__':
    raise SystemExit(main())
