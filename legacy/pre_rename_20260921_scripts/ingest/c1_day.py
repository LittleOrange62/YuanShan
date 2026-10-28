# -*- coding: utf-8 -*-
"""
C1 · 从通达信 vipdoc 的 `.day` 摄取日线，补齐旧库缺的品种。

**为什么要有这一步**：旧 SQLite 有两个洞，都不是迁移能补的。
    1. 北交所 —— `ref.security` 有 340 只，`md.stock_daily` 里 0 行，旧系统压根没采过。
    2. ETF/LOF —— 旧库那 165 万行的 amount/vol 落在价格区间的 5 倍或 15 倍上，
       单位本身是错的。vipdoc 的 `.day` 里 vol/amount 单位在文件格式中是确定的。

**为什么信 vipdoc**：`sh600519.day` 尾部记录与已迁好的 md.stock_daily 逐字段相同
（open 1311.89 / close 1304.00 / vol 2111118 / amount 2757527040），两个独立来源
对上了，所以 vipdoc 可作权威源。

默认 `ON CONFLICT DO NOTHING`：只填空洞，不覆盖已有行。已经比对过两边一致，
没必要为了重写 600 万行等十分钟。要全量刷新用 `--overwrite`。

用法：
    python scripts\\ingest\\c1_day.py --dry-run
    python scripts\\ingest\\c1_day.py --market bj          # 只补北交所
    python scripts\\ingest\\c1_day.py                      # 三个市场全量
    python scripts\\ingest\\c1_day.py --overwrite          # 用 vipdoc 覆盖已有行
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402
from app.ingest import instrument as ins  # noqa: E402
from app.ingest import tdx_binary as tb  # noqa: E402
from app.ingest import tdx_tnf  # noqa: E402

CHUNK = 100_000

STOCK_COLS = ('ts_code', 'trade_date', 'open', 'high', 'low', 'close',
              'vol_shares', 'amount_yuan', 'src_batch_id')
INDEX_COLS = STOCK_COLS
FUND_COLS = ('ts_code', 'trade_date', 'open', 'high', 'low', 'close',
             'vol_units', 'amount_yuan', 'src_batch_id')

TARGETS = {
    'stock': ('md.stg_c1_stock', 'md.stock_daily', STOCK_COLS, 'vol_shares'),
    'index': ('md.stg_c1_index', 'md.index_daily', INDEX_COLS, 'vol_shares'),
    'fund':  ('md.stg_c1_fund',  'md.fund_daily',  FUND_COLS,  'vol_units'),
}


def create_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        for stg, _target, cols, _vol in TARGETS.values():
            cur.execute(f'DROP TABLE IF EXISTS {stg}')
            cur.execute(f'CREATE UNLOGGED TABLE {stg} ('
                        'ts_code text, trade_date date, '
                        'open numeric(14,4), high numeric(14,4), '
                        'low numeric(14,4), close numeric(14,4), '
                        f'{cols[6]} bigint, amount_yuan numeric(20,2), '
                        'src_batch_id bigint)')


def drop_staging() -> None:
    with engine.conn('admin') as c, c.cursor() as cur:
        for stg, *_ in TARGETS.values():
            cur.execute(f'DROP TABLE IF EXISTS {stg}')


def merge(kind: str, *, overwrite: bool) -> int:
    stg, target, cols, vol_col = TARGETS[kind]
    collist = ', '.join(cols)
    where = []
    if not overwrite:
        # 已存在的行根本别送进 INSERT。md.stock_daily 上的单位一致性触发器是
        # BEFORE INSERT，ON CONFLICT DO NOTHING 挡不住它 —— 冲突是在触发器
        # 之后才判定的。不先反连接过滤，440 万条已有行会全部过一遍触发器，
        # 其中任何一条口径不自洽就整批回滚。
        where.append(f'NOT EXISTS (SELECT 1 FROM {target} t '
                     'WHERE t.ts_code = s.ts_code AND t.trade_date = s.trade_date)')
    if kind == 'stock':
        # 与触发器同一条判据，提前在暂存表里筛掉，让坏数据可统计而不是炸批次
        where.append(f'({vol_col} IS NULL OR amount_yuan IS NULL '
                     f'OR {vol_col} <= 0 OR amount_yuan <= 0 OR low <= 0 '
                     f'OR (amount_yuan / {vol_col} BETWEEN low * 0.5 AND high * 2))')
    cond = ('WHERE ' + ' AND '.join(where)) if where else ''

    if overwrite:
        tail = f"""
        ON CONFLICT (ts_code, trade_date) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
            close = EXCLUDED.close, {vol_col} = EXCLUDED.{vol_col},
            amount_yuan = EXCLUDED.amount_yuan,
            src_batch_id = EXCLUDED.src_batch_id, updated_at = now()
        """
    else:
        tail = 'ON CONFLICT (ts_code, trade_date) DO NOTHING'
    return engine.execute(
        f"""
        INSERT INTO {target} ({collist})
        SELECT DISTINCT ON (ts_code, trade_date) {collist}
          FROM {stg} s
        {cond}
         ORDER BY ts_code, trade_date
        {tail}
        """, role='admin')


def count_unit_outliers(kind: str) -> int:
    """报出被口径判据筛掉的行数，不静默丢数据。"""
    stg, _target, _cols, vol_col = TARGETS[kind]
    return engine.scalar(
        f'SELECT count(*) FROM {stg} WHERE {vol_col} > 0 AND amount_yuan > 0 '
        f'AND low > 0 AND amount_yuan / {vol_col} '
        f'NOT BETWEEN low * 0.5 AND high * 2') or 0


def register_missing(codes: set[str]) -> int:
    """
    vipdoc 里有、ref.security 里没有的代码要补登。
    直接丢弃等于静默丢掉真实标的 —— 北交所 920xxx 新股就属于这一类。
    """
    if not codes:
        return 0
    names = tdx_tnf.name_map()
    rows = []
    for ts in sorted(codes):
        symbol, _, market = ts.partition('.')
        kind = sec_repo.guess_asset_type(symbol, market)
        tnf = names.get(ts)
        board = sec_repo.guess_board(symbol, market) if kind == 'stock' else None
        rows.append((ts, symbol, market, tnf.name if tnf else None, None, None,
                     board, kind, bool(tnf and tnf.is_st), None, None, None, True))
    if rows:
        sec_repo.upsert_securities(rows)
    return len(rows)


def scan(markets: tuple[str, ...], batch_id: int, *, dry_run: bool,
         since: dt.date | None = None) -> dict:
    known = {r['ts_code'] for r in engine.query('SELECT ts_code FROM ref.security')}
    stat = {k: 0 for k in (*ins.KINDS, 'files', 'bars', 'skipped_files')}
    buf: dict[str, list[tuple]] = {'stock': [], 'index': [], 'fund': []}
    missing: set[str] = set()
    t0 = time.time()


    def flush(kind: str) -> None:
        rows = buf[kind]
        if not rows or dry_run:
            buf[kind] = []
            return
        stg, _target, cols, _vol = TARGETS[kind]
        engine.copy_rows(stg, list(cols), iter(rows), role='admin')
        buf[kind] = []

    for path in tb.iter_day_files(config.VIPDOC_DIR, markets):
        ts = tb.ts_code_of(path)
        if not ts:
            stat['skipped_files'] += 1
            continue
        kind = ins.classify(ts)
        stat['files'] += 1
        if kind not in buf:
            stat[kind] += 1          # bond / bshare / pseudo / sector / unknown
            continue
        if ts not in known:
            missing.add(ts)
        n = 0
        for b in tb.parse_day(path, ts_code=ts):
            if since and b.trade_date < since:
                continue
            buf[kind].append((b.ts_code, b.trade_date, b.open, b.high, b.low,
                              b.close, b.vol, b.amount, batch_id))
            n += 1

        stat[kind] += 1
        stat['bars'] += n
        if len(buf[kind]) >= CHUNK:
            flush(kind)

    for kind in buf:
        flush(kind)

    stat['seconds'] = round(time.time() - t0, 1)
    stat['missing_in_ref'] = len(missing)
    if missing and not dry_run:
        stat['registered'] = register_missing(missing)
    stat['missing_sample'] = sorted(missing)[:8]
    return stat


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--market', action='append',
                    choices=['sh', 'sz', 'bj'], default=None)
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--since',
                    help='只摄取这一天及之后的记录（YYYY-MM-DD）。'
                         '**配合 --overwrite 用**：不带它的覆盖会把 600 多万行'
                         '全过一遍触发器，要十分钟；只想把某几天换回通达信口径时'
                         '加上它，几秒就完事')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    markets = tuple(args.market) if args.market else ('sh', 'sz', 'bj')
    since = dt.date.fromisoformat(args.since) if args.since else None

    if not config.VIPDOC_DIR.is_dir():
        print('vipdoc 目录不存在：%s' % config.VIPDOC_DIR)
        return 1

    batch_id = 0
    if not args.dry_run:
        batch_id = ops_repo.begin_batch('tdx_vipdoc', 'md.stock_daily',
                                        source_path=str(config.VIPDOC_DIR))
        print('批次 %s' % batch_id)
        create_staging()

    try:
        print('=== 扫描 %s%s ===' % ('/'.join(markets),
                                    f'（只要 {since} 之后）' if since else ''))
        st = scan(markets, batch_id, dry_run=args.dry_run, since=since)

        for k, v in st.items():
            if v:
                print('  %-18s %s' % (k, f'{v:,}' if isinstance(v, int) else v))

        if args.dry_run:
            print('\n--dry-run，未写库')
            return 0

        print('\n=== 合并进正式表%s ===' % ('（覆盖模式）' if args.overwrite else '（只补空洞）'))
        merged = {}
        for kind in ('stock', 'index', 'fund'):
            if kind == 'stock':
                bad = count_unit_outliers(kind)
                if bad:
                    print('  [口径] 暂存表里 %s 行 amount/vol 越出价格区间，不并入'
                          % f'{bad:,}')
            t0 = time.time()
            n = merge(kind, overwrite=args.overwrite)
            merged[kind] = n
            print('  %-16s %s 行  %.1fs' % (TARGETS[kind][1], f'{n:,}',
                                            time.time() - t0))
        ops_repo.end_batch(batch_id, status='ok', row_count=sum(merged.values()))
    except Exception as exc:
        if not args.dry_run:
            ops_repo.end_batch(batch_id, status='failed', message=str(exc)[:2000])
        raise
    finally:
        if not args.dry_run:
            drop_staging()

    print('\n=== 验收 ===')
    for label, sql in (
            ('md.stock_daily', 'SELECT count(*) FROM md.stock_daily'),
            ('  其中北交所', "SELECT count(*) FROM md.stock_daily d "
                             'JOIN ref.security s USING (ts_code) '
                             "WHERE s.board = '北交所'"),
            ('md.index_daily', 'SELECT count(*) FROM md.index_daily'),
            ('md.fund_daily', 'SELECT count(*) FROM md.fund_daily'),
            ('日期区间', "SELECT min(trade_date)::text || ' .. ' || "
                         'max(trade_date)::text FROM md.stock_daily'),
    ):
        print('  %-16s %s' % (label, engine.scalar(sql)))

    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
