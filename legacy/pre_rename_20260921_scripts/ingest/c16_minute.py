# -*- coding: utf-8 -*-
"""
C16 · 从通达信 vipdoc 的 `.lc1`/`.lc5` 摄取 1/5 分钟线，写进 md.stock_minute。

**为什么缺这一步**：vipdoc 的日线由 C1 落库、板块由 C3 落库，唯独分钟线此前
只落到本地文件 —— `readiness.minute_ready` 的"同步完成"判据到 `.lc1/.lc5`
尾部日期为止，`md.stock_minute` 一直是空表，`sync_minute` 作业也没有执行器。
解析原语（`tdx_binary.parse_minute`）和 UPSERT（`repo.quote.upsert_minute`）
早就写好了，缺的只是把两者装配成一个入库脚本，这一步补上。

**按日增量，不灌全史**：一个 `.lc1` 里存着几个月、每票每天 240 根，全量重扫
是上千万行。默认只摄取目标交易日当天（`--date`）；要回填更早的区间用 `--since`。

单位口径：vipdoc 分钟线的 vol 是「股」、amount 是「元」，与 `.day` 同源
（见 tdx_binary 模块 docstring 的实测校验），直接入库不做 ×100 换算。

用法：
    python scripts\\ingest\\c16_minute.py --date 2026-09-22 --dry-run
    python scripts\\ingest\\c16_minute.py --date 2026-09-22
    python scripts\\ingest\\c16_minute.py --since 2026-09-01 --period 5
    python scripts\\ingest\\c16_minute.py --date 2026-09-22 --overwrite
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.ingest import tdx_binary as tb  # noqa: E402

CHUNK = 200_000
STAGING = 'md.stg_c16_minute'
COLUMNS = ('ts_code', 'dt', 'period', 'open', 'high', 'low', 'close',
           'vol_shares', 'amount_yuan', 'src_batch_id')

# 分钟线时间戳是本地交易时间（A 股 09:30–15:00）。库列是 timestamptz，
# 连接会话的 timezone 已设为 Asia/Shanghai，但显式带 tzinfo 更稳妥，
# 不依赖会话设置。
_TZ = ZoneInfo(config.TIMEZONE)


def create_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STAGING}')
        cur.execute(f'CREATE UNLOGGED TABLE {STAGING} ('
                    'ts_code text, dt timestamptz, period smallint, '
                    'open numeric(12,4), high numeric(12,4), '
                    'low numeric(12,4), close numeric(12,4), '
                    'vol_shares bigint, amount_yuan numeric(20,2), '
                    'src_batch_id bigint)')


def drop_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STAGING}')


def merge(*, overwrite: bool) -> int:
    collist = ', '.join(COLUMNS)
    # ck_minute_ohlc：low<=high。坏记录（解析错位）在合并时挡掉，不炸批次。
    where = ['low IS NULL OR high IS NULL OR low <= high']
    if not overwrite:
        where.append('NOT EXISTS (SELECT 1 FROM md.stock_minute t '
                     'WHERE t.ts_code = s.ts_code AND t.dt = s.dt '
                     'AND t.period = s.period)')
    tail = ('ON CONFLICT (ts_code, dt, period) DO UPDATE SET '
            'open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, '
            'close = EXCLUDED.close, vol_shares = EXCLUDED.vol_shares, '
            'amount_yuan = EXCLUDED.amount_yuan, '
            'src_batch_id = EXCLUDED.src_batch_id'
            if overwrite else
            'ON CONFLICT (ts_code, dt, period) DO NOTHING')
    return engine.execute(
        f"""
        INSERT INTO md.stock_minute ({collist})
        SELECT DISTINCT ON (ts_code, dt, period) {collist}
          FROM {STAGING} s
         WHERE {' AND '.join(where)}
         ORDER BY ts_code, dt, period
        {tail}
        """, role='admin')


def scan(periods: tuple[int, ...], markets: tuple[str, ...], batch_id: int, *,
         since: dt.date | None, until: dt.date | None, dry_run: bool) -> dict:
    stat = {f'bars_p{p}': 0 for p in periods}
    stat.update(files=0, skipped_files=0, bad_ohlc=0)
    buf: list[tuple] = []
    t0 = time.time()

    def flush() -> None:
        if not buf or dry_run:
            buf.clear()
            return
        engine.copy_rows(STAGING, list(COLUMNS), iter(buf), role='admin')
        buf.clear()

    for period in periods:
        for path in tb.iter_minute_files(config.VIPDOC_DIR, period, markets):
            ts = tb.ts_code_of(path)
            if not ts:
                stat['skipped_files'] += 1
                continue
            stat['files'] += 1
            n = 0
            for b in tb.parse_minute(path, ts_code=ts):
                d = b.dt.date()
                if since and d < since:
                    continue
                if until and d > until:
                    continue
                if b.low is not None and b.high is not None and b.low > b.high:
                    stat['bad_ohlc'] += 1
                    continue
                buf.append((b.ts_code, b.dt.replace(tzinfo=_TZ), period,
                            b.open, b.high, b.low, b.close,
                            b.vol, b.amount, batch_id))
                n += 1
                if len(buf) >= CHUNK:
                    flush()
            stat[f'bars_p{period}'] += n
    flush()
    stat['seconds'] = round(time.time() - t0, 1)
    return stat


def main() -> int:
    ap = argparse.ArgumentParser(
        description='C16 · 通达信 vipdoc 分钟线入库（md.stock_minute）')
    ap.add_argument('--date', help='只摄取这一天（YYYY-MM-DD）。默认取目标交易日')
    ap.add_argument('--since', help='摄取这一天及之后（YYYY-MM-DD），与 --date 互斥')
    ap.add_argument('--period', action='append', type=int, choices=[1, 5],
                    help='只摄取指定周期，可重复；默认 1 和 5 都要')
    ap.add_argument('--market', action='append', choices=['sh', 'sz', 'bj'])
    ap.add_argument('--overwrite', action='store_true',
                    help='用 vipdoc 覆盖已有分钟线（默认只补空洞）')
    ap.add_argument('--full', action='store_true',
                    help='不限日期，摄取 vipdoc 里全部历史（慎用，量极大）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    periods = tuple(args.period) if args.period else (1, 5)
    markets = tuple(args.market) if args.market else ('sh', 'sz', 'bj')

    if args.full:
        since = until = None
        span = '全部历史'
    elif args.since and args.date:
        print('--since 与 --date 不能同时给')
        return 2
    elif args.since:
        since = dt.date.fromisoformat(args.since)
        until = None
        span = f'{since} 起'
    else:
        from app.tdx_client import readiness
        since = dt.date.fromisoformat(args.date) if args.date \
            else readiness.expected_trade_date()
        until = since
        span = str(since)

    if not config.VIPDOC_DIR.is_dir():
        print('vipdoc 目录不存在：%s' % config.VIPDOC_DIR)
        return 1

    print('=== C16 分钟线入库 · 周期 %s · 市场 %s · 区间 %s%s ==='
          % ('/'.join(map(str, periods)), '/'.join(markets), span,
             '（--dry-run）' if args.dry_run else ''))

    batch_id = 0
    if not args.dry_run:
        batch_id = ops_repo.begin_batch('tdx_vipdoc_minute', 'md.stock_minute',
                                        source_path=str(config.VIPDOC_DIR))
        print('批次 %s' % batch_id)
        create_staging()

    try:
        st = scan(periods, markets, batch_id, since=since, until=until,
                  dry_run=args.dry_run)
        for k, v in st.items():
            if v:
                print('  %-14s %s' % (k, f'{v:,}' if isinstance(v, int) else v))

        if args.dry_run:
            print('\n--dry-run，未写库')
            return 0

        print('\n=== 合并进 md.stock_minute（%s） ==='
              % ('覆盖模式' if args.overwrite else '只补空洞'))
        t0 = time.time()
        n = merge(overwrite=args.overwrite)
        print('  md.stock_minute  %s 行  %.1fs' % (f'{n:,}', time.time() - t0))
        ops_repo.end_batch(batch_id, status='ok', row_count=n)
    except Exception as exc:
        if not args.dry_run:
            ops_repo.end_batch(batch_id, status='failed', message=str(exc)[:2000])
        raise
    finally:
        if not args.dry_run:
            drop_staging()

    print('\n=== 验收 ===')
    print('  md.stock_minute 总行数  %s'
          % engine.scalar('SELECT count(*) FROM md.stock_minute'))
    if not args.full:
        print('  目标日 %s 行数        %s'
              % (since, engine.scalar(
                  'SELECT count(*) FROM md.stock_minute WHERE dt::date = %s',
                  (since.isoformat(),))))
    for r in engine.query(
            'SELECT period, count(*) n, min(dt)::date lo, max(dt)::date hi '
            'FROM md.stock_minute GROUP BY period ORDER BY period'):
        print('  period=%s  %s 行  %s .. %s'
              % (r['period'], f"{r['n']:,}", r['lo'], r['hi']))

    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
