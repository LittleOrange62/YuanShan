# -*- coding: utf-8 -*-
"""
C4 · 采集沪深两所 ETF 每日总份额，写入 md.etf_share。

**为什么要有这一步**：二级市场成交额只反映筹码换手，看不到增量资金。
ETF 的总份额变动才是一级市场的净申赎 —— 份额增加意味着有钱从场外进来买入
一篮子股票，减少意味着有钱撤出。实测 2026-08-26 沪深合计 −120.2 亿份，
而同日指数收涨，这种量价背离只有份额数据能看出来。

**数据源**（两个交易所官方页面，akshare 只做转发，不是第三方估算）：
    上交所  ak.fund_etf_scale_sse(date='20260826')                  897 只，含 ETF 类型分类
    深交所  ak.fund_scale_daily_szse('20260826','20260826','ETF')   724 只，无类型列

    注意深交所函数的第三个参数名是 `symbol` 不是 `category`，
    传 `category=` 会 TypeError（实测 akshare 1.18.88）。

**为什么只入库存量、不入库净申赎**：净申赎是派生量，用 md.v_etf_flow 视图算。
视图里按 ts_code 分区做 LAG，新上市 ETF 首日 delta 自然为 NULL。
若图省事直接算「当日总份额 − 前日总份额」，首发份额会被计成申购 ——
实测 2026-08-26 深交所 4 只新 ETF（158001/158010/158017/158019）贡献 +11.31 亿份，
把当天真实的 −28.05 亿份净赎回冲淡成 −16.73 亿份，失真 40%。

**非交易日**：两个接口对休市日返回空表，不报错。脚本按「空表即跳过」处理，
不写空批次。

用法：
    python scripts\\ingest\\c4_etf_share.py                       # 最近一个交易日
    python scripts\\ingest\\c4_etf_share.py --date 20260826
    python scripts\\ingest\\c4_etf_share.py --start 20260701 --end 20260827   # 回补
    python scripts\\ingest\\c4_etf_share.py --date 20260826 --dry-run
    python scripts\\ingest\\c4_etf_share.py --date 20260826 --overwrite
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

STG = 'md.stg_c4_etf_share'
TARGET = 'md.etf_share'
COLS = ('ts_code', 'trade_date', 'fund_name', 'etf_type',
        'share_units', 'src_batch_id')

SSE_COLS = {'code': '基金代码', 'name': '基金简称',
            'type': 'ETF类型', 'share': '基金份额'}
SZSE_COLS = {'code': '基金代码', 'name': '基金简称', 'share': '基金份额'}


def create_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STG}')
        cur.execute(f'CREATE UNLOGGED TABLE {STG} ('
                    'ts_code text, trade_date date, fund_name text, '
                    'etf_type text, share_units numeric(20,2), '
                    'src_batch_id bigint)')


def drop_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STG}')


def _need(df, mapping: dict[str, str], where: str) -> None:
    """接口列名变了要立刻炸，不能静默产出空数据。"""
    missing = [v for v in mapping.values() if v not in df.columns]
    if missing:
        raise RuntimeError(f'{where} 返回的列与预期不符，缺少 {missing}；'
                           f'实际列 = {list(df.columns)}')


def fetch_sse(day: dt.date, batch_id: int) -> list[tuple]:
    import akshare as ak
    df = ak.fund_etf_scale_sse(date=day.strftime('%Y%m%d'))
    if df is None or df.empty:
        return []
    _need(df, SSE_COLS, 'fund_etf_scale_sse')
    rows = []
    for r in df.itertuples(index=False):
        d = r._asdict()
        code = str(d[SSE_COLS['code']]).strip()
        share = d[SSE_COLS['share']]
        if len(code) != 6 or share is None or share != share:   # NaN != NaN
            continue
        rows.append((f'{code}.SH', day, str(d[SSE_COLS['name']]).strip(),
                     str(d[SSE_COLS['type']]).strip() or None,
                     float(share), batch_id))
    return rows


def fetch_szse(day: dt.date, batch_id: int) -> list[tuple]:
    import akshare as ak
    ds = day.strftime('%Y%m%d')
    df = ak.fund_scale_daily_szse(start_date=ds, end_date=ds, symbol='ETF')
    if df is None or df.empty:
        return []
    _need(df, SZSE_COLS, 'fund_scale_daily_szse')
    rows = []
    for r in df.itertuples(index=False):
        d = r._asdict()
        code = str(d[SZSE_COLS['code']]).strip()
        share = d[SZSE_COLS['share']]
        if len(code) != 6 or share is None:
            continue
        try:
            val = float(str(share).replace(',', ''))
        except ValueError:
            continue
        if val != val:
            continue
        rows.append((f'{code}.SZ', day, str(d[SZSE_COLS['name']]).strip(),
                     None, val, batch_id))
    return rows


def merge(*, overwrite: bool) -> int:
    collist = ', '.join(COLS)
    if overwrite:
        tail = """
        ON CONFLICT (ts_code, trade_date) DO UPDATE SET
            fund_name = EXCLUDED.fund_name, etf_type = EXCLUDED.etf_type,
            share_units = EXCLUDED.share_units,
            src_batch_id = EXCLUDED.src_batch_id, updated_at = now()
        """
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
            if cur.weekday() < 5:        # 周末两个接口都返回空表，没必要请求
                out.append(cur)
            cur += dt.timedelta(days=1)
        return out
    if args.date:
        return [dt.datetime.strptime(args.date, '%Y%m%d').date()]
    latest = engine.scalar('SELECT max(trade_date) FROM md.fund_daily')
    if latest is None:
        raise SystemExit('md.fund_daily 是空的，无法推断最近交易日，请显式给 --date')
    return [latest]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='YYYYMMDD，单日')
    ap.add_argument('--start', help='YYYYMMDD，区间起（含）')
    ap.add_argument('--end', help='YYYYMMDD，区间止（含），默认今天')
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    days = target_dates(args)
    print('=== 目标日期 %d 天：%s .. %s ===' % (len(days), days[0], days[-1]))

    batch_id = 0
    if not args.dry_run:
        batch_id = ops_repo.begin_batch(
            'akshare_etf_scale', TARGET,
            source_path='fund_etf_scale_sse + fund_scale_daily_szse')
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
                    print('  %s %s 拉取失败：%s' % (day, mkt, str(exc)[:120]))
                    continue
                got[mkt] = rows
                if rows and not args.dry_run:
                    engine.copy_rows(STG, list(COLS), iter(rows), role='admin')
                total += len(rows)
            if not any(got.values()):
                print('  %s 两所均为空（非交易日或尚未发布），跳过' % day)
                continue
            print('  %s  SSE %4d 只 %10.2f 亿份 | SZSE %4d 只 %10.2f 亿份  %.1fs'
                  % (day,
                     len(got.get('SSE', [])),
                     sum(r[4] for r in got.get('SSE', [])) / 1e8,
                     len(got.get('SZSE', [])),
                     sum(r[4] for r in got.get('SZSE', [])) / 1e8,
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
    print('  总行数        %s' % engine.scalar('SELECT count(*) FROM md.etf_share'))
    print('  日期区间      %s' % engine.scalar(
        "SELECT min(trade_date)::text || ' .. ' || max(trade_date)::text "
        'FROM md.etf_share'))
    for r in engine.query(
            """
            SELECT trade_date,
                   count(*)                                   AS n,
                   round(sum(delta_units) / 1e8, 2)           AS delta_yi,
                   round(sum(delta_amount_yuan) / 1e8, 2)     AS amount_yi,
                   count(delta_amount_yuan)                   AS priced
              FROM md.v_etf_flow
             WHERE delta_units IS NOT NULL
             GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5
            """):
        print('  %s  %4d 只  净申赎 %+9.2f 亿份  %+10.2f 亿元（%d 只有收盘价）'
              % (r['trade_date'], r['n'], r['delta_yi'] or 0,
                 r['amount_yi'] or 0, r['priced']))

    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
