# -*- coding: utf-8 -*-
"""
C5 · 采集沪深两所融资融券逐股明细，写入 md.margin_detail。

**为什么要有这一步**：融资余额是"加杠杆的钱"，它的变化方向比成交额更能说明
参与者的信心。同样一根阳线，融资余额同步增加是加杠杆追，融资余额反而下降
是有人借着反弹还钱走人。这个信号在日线里完全看不到。

**数据源**：只能走 akshare（交易所官网转发）。Tushare 的 `margin` 与
`margin_detail` 在本项目 token 下返回 40203「没有接口访问权限」，实测确认。

    上交所  ak.stock_margin_detail_sse('20260826')     1999 行 9 列
    深交所  ak.stock_margin_detail_szse('20260825')     2101 行 8 列

**两所列不一样**（见 md.margin_detail 的表注释）：上交所给偿还额不给融券余额，
深交所反过来。缺的列留空，不自己折算 —— 猜出来的数会被下游当实测值用。

**深交所 T+1**：实测 2026-08-26 当天查 08-26 返回 0 行，查 08-25 正常。
所以默认把「目标日」和往前 `--lag-days`（默认 1）天一起拉，空表跳过不算失败。

`stock_margin_szse`（深交所汇总）在 akshare 1.18.88 里会抛
`Length mismatch: Expected axis has 0 elements, new values have 6 elements`，
明确不接这个接口。要市场总量用 `stock_margin_sse` 或对本表求和。

用法：
    python scripts\\ingest\\c5_margin.py                        # 最近交易日（含 T+1 回补）
    python scripts\\ingest\\c5_margin.py --date 20260825
    python scripts\\ingest\\c5_margin.py --start 20260701 --end 20260827
    python scripts\\ingest\\c5_margin.py --date 20260825 --dry-run
    python scripts\\ingest\\c5_margin.py --date 20260825 --overwrite
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402

STG = 'md.stg_c5_margin'
TARGET = 'md.margin_detail'
COLS = ('ts_code', 'trade_date', 'rz_balance', 'rz_buy', 'rz_repay',
        'rq_volume', 'rq_sell', 'rq_repay', 'rq_balance', 'rzrq_balance',
        'src_batch_id')

# 交易所列名 → 本表列名。值为 None 表示该所不提供
SSE_MAP = {
    'code': '标的证券代码',
    'rz_balance': '融资余额', 'rz_buy': '融资买入额', 'rz_repay': '融资偿还额',
    'rq_volume': '融券余量', 'rq_sell': '融券卖出量', 'rq_repay': '融券偿还量',
}
SZSE_MAP = {
    'code': '证券代码',
    'rz_balance': '融资余额', 'rz_buy': '融资买入额',
    'rq_volume': '融券余量', 'rq_sell': '融券卖出量',
    'rq_balance': '融券余额', 'rzrq_balance': '融资融券余额',
}


def create_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STG}')
        cur.execute(f'CREATE UNLOGGED TABLE {STG} ('
                    'ts_code text, trade_date date, '
                    'rz_balance numeric(20,2), rz_buy numeric(20,2), '
                    'rz_repay numeric(20,2), rq_volume numeric(20,2), '
                    'rq_sell numeric(20,2), rq_repay numeric(20,2), '
                    'rq_balance numeric(20,2), rzrq_balance numeric(20,2), '
                    'src_batch_id bigint)')


def drop_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STG}')


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(str(v).replace(',', '').strip())
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # NaN


def _rows(df, mapping: dict[str, str], suffix: str, day: dt.date,
          batch_id: int, where: str) -> list[tuple]:
    missing = [v for v in mapping.values() if v not in df.columns]
    if missing:
        raise RuntimeError(f'{where} 列与预期不符，缺少 {missing}；'
                           f'实际 = {list(df.columns)}')
    out = []
    for r in df.itertuples(index=False):
        d = r._asdict()
        code = str(d[mapping['code']]).strip()
        if len(code) != 6 or not code.isdigit():
            continue
        vals = {k: _num(d[col]) for k, col in mapping.items() if k != 'code'}
        if vals.get('rz_balance') is None and vals.get('rz_buy') is None:
            continue                      # 整行没有融资数据，没有入库价值
        out.append((
            f'{code}.{suffix}', day,
            vals.get('rz_balance'), vals.get('rz_buy'), vals.get('rz_repay'),
            vals.get('rq_volume'), vals.get('rq_sell'), vals.get('rq_repay'),
            vals.get('rq_balance'), vals.get('rzrq_balance'), batch_id))
    return out


def fetch_sse(day: dt.date, batch_id: int) -> list[tuple]:
    import akshare as ak
    df = ak.stock_margin_detail_sse(date=day.strftime('%Y%m%d'))
    if df is None or df.empty:
        return []
    return _rows(df, SSE_MAP, 'SH', day, batch_id, 'stock_margin_detail_sse')


def fetch_szse(day: dt.date, batch_id: int) -> list[tuple]:
    import akshare as ak
    df = ak.stock_margin_detail_szse(date=day.strftime('%Y%m%d'))
    if df is None or df.empty:
        return []
    return _rows(df, SZSE_MAP, 'SZ', day, batch_id, 'stock_margin_detail_szse')


def merge(*, overwrite: bool) -> int:
    collist = ', '.join(COLS)
    if overwrite:
        sets = ', '.join(f'{c} = EXCLUDED.{c}' for c in COLS
                         if c not in ('ts_code', 'trade_date'))
        tail = f'ON CONFLICT (ts_code, trade_date) DO UPDATE SET {sets}, updated_at = now()'
        cond = ''
    else:
        tail = 'ON CONFLICT (ts_code, trade_date) DO NOTHING'
        cond = (f'WHERE NOT EXISTS (SELECT 1 FROM {TARGET} t '
                'WHERE t.ts_code = s.ts_code AND t.trade_date = s.trade_date)')
    return engine.execute(
        f"""
        INSERT INTO {TARGET} ({collist})
        SELECT DISTINCT ON (ts_code, trade_date) {collist}
          FROM {STG} s
        {cond}
         ORDER BY ts_code, trade_date
        {tail}
        """, role='admin')


def target_dates(args) -> list[dt.date]:
    if args.start:
        start = dt.datetime.strptime(args.start, '%Y%m%d').date()
        end = (dt.datetime.strptime(args.end, '%Y%m%d').date()
               if args.end else dt.date.today())
        out, cur = [], start
        while cur <= end:
            if cur.weekday() < 5:
                out.append(cur)
            cur += dt.timedelta(days=1)
        return out

    if args.date:
        base = dt.datetime.strptime(args.date, '%Y%m%d').date()
    else:
        base = engine.scalar('SELECT max(trade_date) FROM md.stock_daily')
        if base is None:
            raise SystemExit('md.stock_daily 是空的，请显式给 --date')
    # 深交所 T+1：把前 lag_days 个交易日一起拉，漏发的那天下次跑自然补上
    days, cur = [], base
    while len(days) < args.lag_days + 1:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= dt.timedelta(days=1)
    return sorted(days)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='YYYYMMDD')
    ap.add_argument('--start', help='YYYYMMDD，区间起（含）')
    ap.add_argument('--end', help='YYYYMMDD，区间止（含）')
    ap.add_argument('--lag-days', type=int, default=1,
                    help='额外往前回补几个交易日（深交所 T+1 发布），默认 1')
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    days = target_dates(args)
    print('=== 目标日期 %d 天：%s .. %s ===' % (len(days), days[0], days[-1]))

    batch_id = 0
    if not args.dry_run:
        batch_id = ops_repo.begin_batch(
            'akshare_margin', TARGET,
            source_path='stock_margin_detail_sse + stock_margin_detail_szse')
        print('批次 %s' % batch_id)
        create_staging()

    total = 0
    try:
        for day in days:
            t0 = time.time()
            got = {}
            for mkt, fn in (('SSE', fetch_sse), ('SZSE', fetch_szse)):
                try:
                    rows = fn(day, batch_id)
                except Exception as exc:
                    print('  %s %s 拉取失败：%s' % (day, mkt, str(exc)[:140]))
                    continue
                got[mkt] = rows
                if rows and not args.dry_run:
                    engine.copy_rows(STG, list(COLS), iter(rows), role='admin')
                total += len(rows)
            sse, szse = got.get('SSE', []), got.get('SZSE', [])
            if not sse and not szse:
                print('  %s 两所均为空（非交易日或尚未发布），跳过' % day)
                continue
            print('  %s  SSE %4d 只 融资余额 %9.2f 亿 | SZSE %4d 只 %9.2f 亿  %.1fs'
                  % (day, len(sse), sum(r[2] or 0 for r in sse) / 1e8,
                     len(szse), sum(r[2] or 0 for r in szse) / 1e8,
                     time.time() - t0))

        if args.dry_run:
            print('\n--dry-run，未写库（共取到 %s 行）' % f'{total:,}')
            return 0

        print('\n=== 合并进 %s%s ===' % (TARGET, '（覆盖）' if args.overwrite else '（只补空洞）'))
        n = merge(overwrite=args.overwrite)
        print('  写入 %s 行（暂存 %s 行）' % (f'{n:,}', f'{total:,}'))
        ops_repo.end_batch(batch_id, status='ok', row_count=n)
    except Exception as exc:
        if not args.dry_run:
            ops_repo.end_batch(batch_id, status='failed', message=str(exc)[:2000])
        raise
    finally:
        if not args.dry_run:
            drop_staging()

    print('\n=== 验收 ===')
    print('  总行数     %s' % engine.scalar('SELECT count(*) FROM md.margin_detail'))
    print('  日期区间   %s' % engine.scalar(
        "SELECT min(trade_date)::text || ' .. ' || max(trade_date)::text "
        'FROM md.margin_detail'))
    for r in engine.query(
            """
            SELECT trade_date, count(*) n,
                   round(sum(rz_balance) / 1e8, 2) rz_yi,
                   count(rq_balance)    AS has_rq_bal,
                   count(rz_repay)      AS has_rz_repay
              FROM md.margin_detail
             GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5
            """):
        print('  %s  %5d 只  融资余额合计 %10.2f 亿元（深交所口径 %d 只 / 上交所口径 %d 只）'
              % (r['trade_date'], r['n'], r['rz_yi'] or 0,
                 r['has_rq_bal'], r['has_rz_repay']))

    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
