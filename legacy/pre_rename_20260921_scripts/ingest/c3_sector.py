# -*- coding: utf-8 -*-
"""
C3 · 从通达信 vipdoc 的 880/881 板块 `.day` 补 md.sector_daily，并回算板块家数。

**为什么要有这一步**：迁移进来的 71.6 万行板块日线有两个洞。
    1. 日期停在 2026-08-21，而个股日线已经用 vipdoc 补到 2026-08-25。
       板块截面为空 → 板块热度 4 个分项全部拿不到值 → sector_heat 一只票都算不出，
       而 env_resonance 里 sector_heat 占 65%，它悄悄退化成"只看大盘情绪"。
       评分照样出数，看不出坏了 —— 这是最危险的那种坏。
    2. up_count / down_count / member_count **全库都是 NULL**。旧库没采，
       `.day` 一条 32 字节记录里也只有 OHLC/vol/amount。板块广度用不了，
       而广度是热度里权重最高的一维。

**为什么信 vipdoc 的板块文件**：`sh880001.day` 与已迁好的 TDX_880001 在
2026-08-17..08-21 五天上 open/close/vol/amount 逐字段完全相同（比值 1.0），
不需要任何单位换算 —— 两边本来就是同一个来源。

家数三列从 ref.sector_member × md.stock_daily 自己数，不编造。

用法：
    python scripts\\ingest\\c3_sector.py --dry-run
    python scripts\\ingest\\c3_sector.py                    # 补缺失日期 + 回算近 130 日家数
    python scripts\\ingest\\c3_sector.py --counts-since 2021-08-02   # 家数全历史回算
    python scripts\\ingest\\c3_sector.py --overwrite        # 用 vipdoc 覆盖已有行
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.db.repo import quote as quote_repo  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402
from app.ingest import tdx_binary as tb  # noqa: E402

STG = 'md.stg_c3_sector'
COLS = ('sector_code', 'trade_date', 'open', 'high', 'low', 'close',
        'pre_close', 'pct_chg', 'vol_shares', 'amount_yuan', 'src_batch_id')

# 板块文件只在 sh 目录下，sz/bj 里一个都没有（实测 sh 1,120 个 / sz 0 / bj 0）
SECTOR_FILE_RE = re.compile(r'^sh(88[01]\d{3})\.day$', re.IGNORECASE)

# 家数默认回算窗口。评分的板块回溯是 60 日，留一倍余量足够；
# 全历史是 128,590 条成员关系 × 1,228 个交易日的聚合，要另开 --counts-since
DEFAULT_COUNT_DAYS = 130


def sector_code_map() -> tuple[dict[str, str], list[str]]:
    """
    通达信 6 位板块码 → ref.sector_info.sector_code（带 TDX_/GN_/FG_/ZS_ 前缀）。

    后 6 位有 427 个键是重复的：同一个通达信板块在旧库里被登记了两次，
    比如 880501 既是 `GN_880501/概念/含H股` 又是 `TDX_880501/行业/含H股`。
    **按「谁已经有日线数据」来定**：md.sector_daily 里的 653 个板块码全是
    TDX_ 那一份，跟着数据走才能保证新摄取的行和历史行落在同一个键上，
    否则同一个板块会分裂成两条互不相连的序列，滚动均值全断。
    """
    rows = engine.query(
        """
        SELECT i.sector_code,
               EXISTS (SELECT 1 FROM md.sector_daily d
                        WHERE d.sector_code = i.sector_code) AS has_data
          FROM ref.sector_info i
         WHERE i.sector_code ~ '[0-9]{6}$'
         ORDER BY i.sector_code
        """)
    cand: dict[str, list[dict]] = {}
    for r in rows:
        cand.setdefault(r['sector_code'][-6:], []).append(r)

    out: dict[str, str] = {}
    ambiguous: list[str] = []
    for key, group in cand.items():
        withdata = [g['sector_code'] for g in group if g['has_data']]
        if len(withdata) == 1:
            out[key] = withdata[0]
        elif len(group) == 1:
            out[key] = group[0]['sector_code']
        else:
            # 有数据的不止一份，或一份都没有 —— 按代码序取第一个并报出来，
            # 让人能看见这里做了一次任意选择，而不是当作确定的映射
            out[key] = group[0]['sector_code']
            ambiguous.append(key)
    return out, sorted(set(ambiguous))


def create_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STG}')
        cur.execute(f'CREATE UNLOGGED TABLE {STG} ('
                    'sector_code text, trade_date date, '
                    'open numeric(14,4), high numeric(14,4), '
                    'low numeric(14,4), close numeric(14,4), '
                    'pre_close numeric(14,4), pct_chg numeric(10,4), '
                    'vol_shares bigint, amount_yuan numeric(20,2), '
                    'src_batch_id bigint)')


def drop_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {STG}')


def scan(batch_id: int, code_map: dict[str, str], *, dry_run: bool,
         since: dt.date | None) -> dict:
    lday = config.VIPDOC_DIR / 'sh' / 'lday'
    stat = {'files': 0, 'unmapped_files': 0, 'bars': 0, 'staged': 0}
    unmapped: list[str] = []
    buf: list[tuple] = []
    t0 = time.time()

    for path in sorted(lday.iterdir()):
        m = SECTOR_FILE_RE.match(path.name)
        if not m:
            continue
        stat['files'] += 1
        sector_code = code_map.get(m.group(1))
        if not sector_code:
            # vipdoc 里有、ref.sector_info 里没有的板块。不静默丢，报出来 ——
            # 通达信会新增板块，而 ref.sector_info 是从旧库迁来的快照
            stat['unmapped_files'] += 1
            if len(unmapped) < 20:
                unmapped.append(m.group(1))
            continue
        prev_close: float | None = None
        for b in tb.parse_day(path):
            stat['bars'] += 1
            pre = prev_close
            prev_close = b.close
            if since and b.trade_date < since:
                continue
            pct = None
            if pre:
                pct = round((b.close - pre) / pre * 100, 4)
            buf.append((sector_code, b.trade_date, b.open, b.high, b.low,
                        b.close, pre, pct, b.vol, round(b.amount, 2), batch_id))
        if len(buf) >= 200_000 and not dry_run:
            engine.copy_rows(STG, list(COLS), iter(buf), role='admin')
            stat['staged'] += len(buf)
            buf = []

    if buf and not dry_run:
        engine.copy_rows(STG, list(COLS), iter(buf), role='admin')
        stat['staged'] += len(buf)
    elif dry_run:
        stat['staged'] = len(buf)

    stat['unmapped_sample'] = unmapped
    stat['scan_seconds'] = round(time.time() - t0, 1)
    return stat


def merge(*, overwrite: bool) -> int:
    collist = ', '.join(COLS)
    cond = ''
    if not overwrite:
        cond = ('WHERE NOT EXISTS (SELECT 1 FROM md.sector_daily t '
                'WHERE t.sector_code = s.sector_code '
                'AND t.trade_date = s.trade_date)')
    if overwrite:
        # 家数三列不能出现在 UPDATE SET 里：它们是从成员股回算来的，
        # vipdoc 文件里没有这三个字段，覆盖等于把刚算好的家数抹成 NULL
        tail = """
        ON CONFLICT (sector_code, trade_date) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
            close = EXCLUDED.close, pre_close = EXCLUDED.pre_close,
            pct_chg = EXCLUDED.pct_chg, vol_shares = EXCLUDED.vol_shares,
            amount_yuan = EXCLUDED.amount_yuan,
            src_batch_id = EXCLUDED.src_batch_id, updated_at = now()
        """
    else:
        tail = 'ON CONFLICT (sector_code, trade_date) DO NOTHING'
    return engine.execute(
        f"""
        INSERT INTO md.sector_daily ({collist})
        SELECT DISTINCT ON (sector_code, trade_date) {collist}
          FROM {STG} s
        {cond}
         ORDER BY sector_code, trade_date
        {tail}
        """, role='admin')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--since', help='只摄取该日期之后的记录，默认全量')
    ap.add_argument('--counts-since', help='家数回算起始日，默认最近 130 个交易日')
    ap.add_argument('--skip-counts', action='store_true')
    args = ap.parse_args()

    lday = config.VIPDOC_DIR / 'sh' / 'lday'
    if not lday.exists():
        print('找不到 vipdoc 目录：%s' % lday)
        return 1

    code_map, dup = sector_code_map()
    print('=== 板块代码映射 ===')
    print('  ref.sector_info 可映射 %s 个板块码' % f'{len(code_map):,}')
    if dup:
        print('  后 6 位重复、无法按数据判定的：%s' % ', '.join(dup[:10]))

    before = engine.query(
        'SELECT count(*) AS n, max(trade_date) AS mx, '
        'count(up_count) AS has_cnt FROM md.sector_daily', role='app')[0]
    print('  库内现状 %s 行，最新 %s，有家数 %s 行'
          % (f"{before['n']:,}", before['mx'], f"{before['has_cnt']:,}"))

    since = dt.date.fromisoformat(args.since) if args.since else None
    batch_id = 0
    if not args.dry_run:
        batch_id = ops_repo.begin_batch(
            'tdx_vipdoc', 'md.sector_daily',
            source_path=str(config.VIPDOC_DIR / 'sh' / 'lday'))
        create_staging()

    try:
        print('\n=== 扫描 vipdoc 板块文件 ===')
        st = scan(batch_id, code_map, dry_run=args.dry_run, since=since)
        print('  文件 %s 个（未映射 %s）  记录 %s 条  入暂存 %s 行  %.1fs'
              % (f"{st['files']:,}", st['unmapped_files'],
                 f"{st['bars']:,}", f"{st['staged']:,}", st['scan_seconds']))
        if st['unmapped_sample']:
            print('    未映射示例：%s' % ', '.join(st['unmapped_sample']))

        merged = 0
        if not args.dry_run:
            print('\n=== 合并进 md.sector_daily ===')
            t0 = time.time()
            merged = merge(overwrite=args.overwrite)
            print('  写入 %s 行  %.1fs' % (f'{merged:,}', time.time() - t0))
            drop_staging()
            ops_repo.end_batch(batch_id, status='ok', row_count=merged)
    except Exception as exc:
        if not args.dry_run:
            drop_staging()
            ops_repo.end_batch(batch_id, status='failed', message=str(exc))
        raise

    if args.dry_run:
        print('\n--dry-run，未写库')
        engine.close_all()
        return 0

    if not args.skip_counts:
        print('\n=== 回算板块涨跌家数 ===')
        if args.counts_since:
            cstart = dt.date.fromisoformat(args.counts_since)
        else:
            dates = sec_repo.recent_trade_dates(DEFAULT_COUNT_DAYS)
            cstart = dates[0] if dates else None
        t0 = time.time()
        n = quote_repo.recompute_sector_counts(cstart)
        print('  %s 起  更新 %s 行  %.1fs'
              % (cstart, f'{n:,}', time.time() - t0))

    print('\n=== 验收 ===')
    after = engine.query(
        'SELECT count(*) AS n, min(trade_date) AS mn, max(trade_date) AS mx, '
        'count(up_count) AS has_cnt, count(DISTINCT sector_code) AS c '
        'FROM md.sector_daily', role='app')[0]
    print('  md.sector_daily   %s 行 / %s 个板块  %s .. %s'
          % (f"{after['n']:,}", after['c'], after['mn'], after['mx']))
    print('  有家数            %s 行（原 %s）'
          % (f"{after['has_cnt']:,}", f"{before['has_cnt']:,}"))
    print('  最新日板块数      %s' % engine.scalar(
        'SELECT count(*) FROM md.sector_daily WHERE trade_date = %s',
        (after['mx'],), role='app'))
    gap = engine.scalar(
        'SELECT (SELECT max(trade_date) FROM md.stock_daily) '
        '- (SELECT max(trade_date) FROM md.sector_daily)', role='app')
    print('  与个股日线的日期差 %s 天' % gap)
    for r in engine.query(
            'SELECT trade_date, count(*) AS n, count(up_count) AS c, '
            'round(avg(up_count),1) AS up, round(avg(member_count),1) AS mem '
            'FROM md.sector_daily WHERE trade_date >= %s GROUP BY 1 '
            'ORDER BY 1 DESC LIMIT 5', (after['mx'] - dt.timedelta(days=10),),
            role='app'):
        print('    %s  %s 个板块  有家数 %s  平均上涨 %s / 成分 %s'
              % (r['trade_date'], r['n'], r['c'], r['up'], r['mem']))

    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
