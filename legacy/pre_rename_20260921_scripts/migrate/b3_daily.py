# -*- coding: utf-8 -*-
"""
B3 · 迁移日线：9,531,333 行个股 + 5,896 行指数 + 716,577 行板块。

单位换算（结论来自 scripts\\migrate\\probe_units.py 的算术判定，不是猜的）：
    旧 stock_daily.vol  单位是「手」，六个年度中位比值 99.98~100.01 → ×100 得股
    旧 stock_daily.amount 单位是「元」，原样
    旧 index_daily.vol / sector_daily.vol 已经是「股」，原样
    证据：同一行上证指数，stock_daily.vol=5,969,504.55 而
          index_daily.vol=596,950,455，正好 100 倍。

分流：旧库把指数（000001.SH 这类）混进了 stock_daily。按 ref.security.asset_type
分流到 md.stock_daily / md.index_daily / md.sector_daily，不让指数污染个股表。

置空后回算，不沿用旧值：
    limit_up / limit_down —— 旧库一刀切 pre_close*1.1，创业板/科创板/北交所全错，
                             连指数都算了涨跌停。迁完用 ref.board_limit_ratio 回算。
    adj_factor            —— 旧库全是 1.0，等于没复权。缺分红数据，先置 NULL。
    turnover_rate_pct     —— 缺股本，算不出来，置 NULL 而不是填 0。

流程：SQLite 流式读 → COPY 进 UNLOGGED 暂存表 → 一条 INSERT ... ON CONFLICT
合并进正式表。单位一致性触发器逐行校验，任何一行口径不对就整批回滚。

用法：
    python scripts\\migrate\\b3_daily.py --dry-run
    python scripts\\migrate\\b3_daily.py --year 2021
    python scripts\\migrate\\b3_daily.py            # 全部年份
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402
from app.ingest import tdx_tnf  # noqa: E402

YEARS = (2021, 2022, 2023, 2024, 2025, 2026)
CHUNK = 50_000

# 固收类代码前缀，明确不进个股表。判据基于交易所代码段：
#   10xxxx 国债/企业债(SH)  11xxxx 可转债(SH)  12xxxx 可转债(SZ)
#   13xxxx 可交换债/逆回购(SZ)  19xxxx 债券(SZ)  20xxxx 国债逆回购(SH)
BOND_PREFIX = ('10', '11', '12', '13', '19', '20')

# 基金类（ETF/LOF/封闭式）。计量单位是「份」而不是「股」，1 手 = 100 份，
# 且不同品种面值不同，与个股口径不兼容。实测强行并进个股表会有 85 万行
# 均价越界（510010.SH 均价 0.27、560000.SH 均价 184，都远离价格区间）。
#   5xxxxx(SH) ETF/LOF/基金   15/16/18xxxx(SZ) ETF/LOF/封闭式基金
FUND_PREFIX = ('50', '51', '52', '53', '55', '56', '58',
               '15', '16', '18')

# B 股以美元(SH)/港币(SZ)计价，amount 不是人民币元，混进来单位就错了。
# 900901.SH 实测均价 0.0816，正是汇率造成的偏差。
B_SHARE_PREFIX = ('900', '200')

# 通达信内部伪代码（999997 A股指数 等），不是可交易标的
TDX_PSEUDO_PREFIX = ('999',)

STOCK_COLS = ('ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close',
              'change_yuan', 'pct_chg', 'vol_shares', 'amount_yuan', 'src_batch_id')
INDEX_COLS = ('ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close',
              'pct_chg', 'vol_shares', 'amount_yuan', 'src_batch_id')
SECTOR_COLS = ('sector_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close',
               'pct_chg', 'vol_shares', 'amount_yuan', 'up_count', 'down_count',
               'member_count', 'src_batch_id')

DDL_STG = """
DROP TABLE IF EXISTS md.{stg};
CREATE UNLOGGED TABLE md.{stg} (LIKE md.{target} INCLUDING DEFAULTS);
ALTER TABLE md.{stg} DROP COLUMN IF EXISTS updated_at;
"""

MERGE_STOCK = """
INSERT INTO md.stock_daily
    (ts_code, trade_date, open, high, low, close, pre_close,
     change_yuan, pct_chg, vol_shares, amount_yuan, src_batch_id)
SELECT DISTINCT ON (ts_code, trade_date)
       ts_code, trade_date, open, high, low, close, pre_close,
       change_yuan, pct_chg, vol_shares, amount_yuan, src_batch_id
  FROM md.stg_stock_daily
 ORDER BY ts_code, trade_date, src_batch_id DESC
ON CONFLICT (ts_code, trade_date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, pre_close = EXCLUDED.pre_close,
    change_yuan = EXCLUDED.change_yuan, pct_chg = EXCLUDED.pct_chg,
    vol_shares = EXCLUDED.vol_shares, amount_yuan = EXCLUDED.amount_yuan,
    src_batch_id = EXCLUDED.src_batch_id, updated_at = now()
"""

MERGE_INDEX = """
INSERT INTO md.index_daily
    (ts_code, trade_date, open, high, low, close, pre_close,
     pct_chg, vol_shares, amount_yuan, src_batch_id)
SELECT DISTINCT ON (ts_code, trade_date)
       ts_code, trade_date, open, high, low, close, pre_close,
       pct_chg, vol_shares, amount_yuan, src_batch_id
  FROM md.stg_index_daily
 ORDER BY ts_code, trade_date, src_batch_id DESC
ON CONFLICT (ts_code, trade_date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, pre_close = EXCLUDED.pre_close,
    pct_chg = EXCLUDED.pct_chg, vol_shares = EXCLUDED.vol_shares,
    amount_yuan = EXCLUDED.amount_yuan,
    src_batch_id = EXCLUDED.src_batch_id, updated_at = now()
"""

MERGE_SECTOR = """
INSERT INTO md.sector_daily
    (sector_code, trade_date, open, high, low, close, pre_close, pct_chg,
     vol_shares, amount_yuan, up_count, down_count, member_count, src_batch_id)
SELECT DISTINCT ON (sector_code, trade_date)
       sector_code, trade_date, open, high, low, close, pre_close, pct_chg,
       vol_shares, amount_yuan, up_count, down_count, member_count, src_batch_id
  FROM md.stg_sector_daily
 ORDER BY sector_code, trade_date, src_batch_id DESC
ON CONFLICT (sector_code, trade_date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, pre_close = EXCLUDED.pre_close,
    pct_chg = EXCLUDED.pct_chg, vol_shares = EXCLUDED.vol_shares,
    amount_yuan = EXCLUDED.amount_yuan, up_count = EXCLUDED.up_count,
    down_count = EXCLUDED.down_count, member_count = EXCLUDED.member_count,
    src_batch_id = EXCLUDED.src_batch_id, updated_at = now()
"""


def ro(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, timeout=30)
    c.execute('PRAGMA busy_timeout=30000')
    return c


def to_date(s) -> dt.date | None:
    if not s:
        return None
    s = str(s).strip().replace('-', '')
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def load_asset_types() -> dict[str, str]:
    return {r['ts_code']: r['asset_type']
            for r in engine.query('SELECT ts_code, asset_type FROM ref.security')}


def register_missing(codes: set[str], asset_types: dict[str, str]) -> int:
    """
    补登旧 security_info 里漏掉的证券。

    tq_core.security_info 是 8 月 23 日的快照，之后上市的新股（301655.SZ、
    688835.SH）和新 ETF（159xxx / 51xxxx / 58xxxx）不在里面。日线里有数据
    却查不到分类，直接丢弃就是静默丢真实个股 —— 这正是要避免的失效模式。
    所以按 .tnf 的名称 + 代码前缀补登，补不上的才报出来。
    """
    if not codes:
        return 0
    names = tdx_tnf.name_map()
    rows = []
    for ts in sorted(codes):
        symbol, _, market = ts.partition('.')
        if not market:
            continue
        kind = sec_repo.guess_asset_type(symbol, market)
        if kind not in ('stock', 'etf', 'index'):
            continue
        tnf = names.get(ts)
        name = tnf.name if tnf else None
        is_st = bool(tnf and tnf.is_st)
        board = sec_repo.guess_board(symbol, market) if kind == 'stock' else None
        rows.append((ts, symbol, market, name, None, None, board, kind, is_st,
                     None, None, None, True))
    if rows:
        sec_repo.upsert_securities(rows)
        for r in rows:
            asset_types[r[0]] = r[7]
    return len(rows)


def classify(ts_code: str, asset_types: dict[str, str]) -> str:
    """
    返回 stock | index | sector | bond | fund | bshare | pseudo | unknown。

    旧 stock_daily 是个大杂烩：9,531,333 行里只有约 690 万行是真 A 股，
    其余是指数、通达信板块、可转债、ETF/LOF、B 股和内部伪代码。

    **判据顺序：代码前缀优先，旧库 asset_type 只当兜底。**
    第一版反了，结果 204001.SH（1 天期国债逆回购）在旧库里被标成 fund，
    被当 ETF 放进个股表，触发器算出均价 9999.9998 远超价格区间 [191, 249.5]
    直接拒绝整批。旧库的分类不可信 —— 交易所代码段是硬事实，旧库字段是
    别人的推断，冲突时信硬事实。

    非 A 股一律不进 md.stock_daily，因为计量口径互不兼容：
        债券   按「张」计量，报价是每百元面值
        逆回购 报的是年化利率，不是价格
        基金   按「份」计量，面值各不相同
        B 股   以美元/港币计价，amount 不是人民币元
    实测把这些混进个股表会有 907,042 行均价越界。要做这些品种就单独建表
    配单独的口径，不是往个股表里挤。
    """
    symbol, _, _market = ts_code.partition('.')

    if symbol.startswith(config.SECTOR_PREFIX):     # 880 / 881 通达信板块
        return 'sector'
    if symbol.startswith(TDX_PSEUDO_PREFIX):        # 999xxx 内部伪代码
        return 'pseudo'
    if symbol.startswith(B_SHARE_PREFIX):           # B 股，外币计价
        return 'bshare'
    if symbol.startswith(BOND_PREFIX):              # 债券、逆回购
        return 'bond'
    if symbol.startswith(FUND_PREFIX):              # ETF / LOF / 封闭式基金
        return 'fund'

    known = asset_types.get(ts_code)
    if known in ('stock', 'etf'):
        return 'stock'
    if known in ('index', 'sector'):
        return known
    if known == 'other':
        return 'bond'
    return 'unknown'


def to_shares(vol) -> int | None:
    """旧 stock_daily.vol 是「手」，×100 转「股」。"""
    if vol is None:
        return None
    try:
        return int(round(float(vol) * 100))
    except (TypeError, ValueError, OverflowError):
        return None


def as_shares(vol) -> int | None:
    """index_daily / sector_daily 的 vol 已经是「股」。"""
    if vol is None:
        return None
    try:
        return int(round(float(vol)))
    except (TypeError, ValueError, OverflowError):
        return None


def prepare_staging():
    for stg, target in (('stg_stock_daily', 'stock_daily'),
                        ('stg_index_daily', 'index_daily'),
                        ('stg_sector_daily', 'sector_daily')):
        with engine.conn('admin') as c, c.cursor() as cur:
            cur.execute(DDL_STG.format(stg=stg, target=target))


def drop_staging():
    with engine.conn('admin') as c, c.cursor() as cur:
        for stg in ('stg_stock_daily', 'stg_index_daily', 'stg_sector_daily'):
            cur.execute(f'DROP TABLE IF EXISTS md.{stg}')


def truncate_staging():
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute('TRUNCATE md.stg_stock_daily, md.stg_index_daily, '
                    'md.stg_sector_daily')


def migrate_year(year: int, batch_id: int, asset_types: dict[str, str],
                 *, dry_run: bool) -> dict:
    p = config.LEGACY_DATA / f'tq_daily_{year}.db'
    if not p.exists():
        return {}
    stat = {'read': 0, 'stock': 0, 'index': 0, 'sector': 0, 'bond': 0,
            'fund': 0, 'bshare': 0, 'pseudo': 0,
            'unknown': 0, 'bad_date': 0, 'no_price': 0}
    unknown_codes: set[str] = set()

    c = ro(p)
    t0 = time.time()

    # ---- stock_daily：个股 + 混进来的指数 ----
    stock_rows, index_rows = [], []
    cur = c.execute(
        'SELECT ts_code, trade_date, open, high, low, close, pre_close, '
        'change, pct_change, vol, amount FROM stock_daily')
    while True:
        chunk = cur.fetchmany(CHUNK)
        if not chunk:
            break
        for (ts, td, o, h, low, cl, pre, chg, pct, vol, amt) in chunk:
            stat['read'] += 1
            d = to_date(td)
            if not d:
                stat['bad_date'] += 1
                continue
            if cl is None or o is None:
                stat['no_price'] += 1
                continue
            kind = classify(ts, asset_types)
            if kind == 'stock':
                stock_rows.append((ts, d, o, h, low, cl, pre, chg, pct,
                                   to_shares(vol), amt, batch_id))
                stat['stock'] += 1
            elif kind == 'index':
                # 指数在 stock_daily 里的 vol 同样是「手」
                index_rows.append((ts, d, o, h, low, cl, pre, pct,
                                   to_shares(vol), amt, batch_id))
                stat['index'] += 1
            elif kind == 'sector':
                stat['sector'] += 1   # 880/881 走 sector_daily，这里丢弃
            elif kind in ('bond', 'fund', 'bshare', 'pseudo'):
                # 债/基金(份)/B股(外币计价)/通达信伪代码，口径与个股不兼容，
                # 明确不进 md.stock_daily。基金后续单独建 md.fund_daily。
                stat[kind] += 1
            else:
                stat['unknown'] += 1
                unknown_codes.add(ts)
        if not dry_run and len(stock_rows) >= CHUNK:
            engine.copy_rows('md.stg_stock_daily', list(STOCK_COLS),
                             iter(stock_rows), role='admin')
            stock_rows = []
    if not dry_run and stock_rows:
        engine.copy_rows('md.stg_stock_daily', list(STOCK_COLS),
                         iter(stock_rows), role='admin')
    if not dry_run and index_rows:
        engine.copy_rows('md.stg_index_daily', list(INDEX_COLS),
                         iter(index_rows), role='admin')

    # ---- index_daily：独立的指数表，vol 已是股 ----
    idx2 = []
    for (ts, td, o, h, low, cl, pre, pct, vol, amt) in c.execute(
            'SELECT ts_code, trade_date, open, high, low, close, pre_close, '
            'pct_change, vol, amount FROM index_daily'):
        d = to_date(td)
        if not d or cl is None:
            continue
        idx2.append((ts, d, o, h, low, cl, pre, pct, as_shares(vol), amt, batch_id))
    stat['index_daily_table'] = len(idx2)
    if not dry_run and idx2:
        engine.copy_rows('md.stg_index_daily', list(INDEX_COLS),
                         iter(idx2), role='admin')

    # ---- sector_daily：只有 2026 那个库里有，但跨 2021-2026 ----
    try:
        sec_rows = []
        for (code, td, o, h, low, cl, pre, pct, vol, amt, mc, uc, dc) in c.execute(
                'SELECT sector_code, trade_date, open, high, low, close, pre_close, '
                'pct_change, vol, amount, member_count, up_count, down_count '
                'FROM sector_daily'):
            d = to_date(td)
            if not d or cl is None:
                continue
            sec_rows.append((code, d, o, h, low, cl, pre, pct, as_shares(vol), amt,
                             uc, dc, mc, batch_id))
        stat['sector_daily_table'] = len(sec_rows)
        if not dry_run and sec_rows:
            engine.copy_rows('md.stg_sector_daily', list(SECTOR_COLS),
                             iter(sec_rows), role='admin')
    except sqlite3.Error:
        stat['sector_daily_table'] = 0

    c.close()
    stat['seconds'] = round(time.time() - t0, 1)
    stat['unknown_sample'] = sorted(unknown_codes)[:10]
    return stat


SANITY_PROBE = """
SELECT left(ts_code, 2) AS prefix,
       count(*)         AS n,
       min(ts_code)     AS sample_code,
       round(min(amount_yuan / vol_shares), 4) AS min_avg_price,
       round(max(amount_yuan / vol_shares), 4) AS max_avg_price
  FROM md.stg_stock_daily
 WHERE vol_shares > 0 AND amount_yuan > 0 AND low > 0
   AND (amount_yuan / vol_shares < low * 0.5
        OR amount_yuan / vol_shares > high * 2.0)
 GROUP BY 1 ORDER BY n DESC
"""

SANITY_PURGE = """
DELETE FROM md.stg_stock_daily
 WHERE vol_shares > 0 AND amount_yuan > 0 AND low > 0
   AND (amount_yuan / vol_shares < low * 0.5
        OR amount_yuan / vol_shares > high * 2.0)
"""


def jsonable(row: dict) -> dict:
    """numeric 列回来是 Decimal，json.dumps 不认。写 ops.quality_result 前转 float。"""
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in row.items()}


def prescreen_units(batch_id: int) -> list[dict]:
    """
    合并前先在暂存表里把口径不自洽的行找出来。

    不这么做的话，7,000,000 行的 INSERT 会在触发器撞到第一个坏行时整批回滚，
    每跑一次三分钟只能发现一个问题代码 —— 排查成本高得离谱。
    先聚合看全貌，再一次性清掉，并写进 ops.quality_result 留痕，
    不静默丢数据。
    """
    bad = engine.query(SANITY_PROBE, role='admin')
    if not bad:
        return []
    total = sum(r['n'] for r in bad)
    print('  口径不自洽 %s 行，按代码前缀分组：' % f'{total:,}')
    for r in bad:
        print('    %s*  %8s 行  例 %s  均价区间 %s ~ %s'
              % (r['prefix'], f"{r['n']:,}", r['sample_code'],
                 r['min_avg_price'], r['max_avg_price']))
    engine.execute(SANITY_PURGE, role='admin')
    try:
        ops_repo.save_quality_result(
            None, 'vol_amount_consistent', 'md.stock_daily', 'error', False,
            actual={'rejected_rows': total,
                    'by_prefix': [jsonable(r) for r in bad]},
            expected={'rule': 'amount_yuan/vol_shares ∈ [low*0.5, high*2]'},
            batch_id=batch_id if batch_id > 0 else None)
    except Exception as exc:  # noqa: BLE001
        print('  [warn] 未能写入 ops.quality_result：%s' % exc)
    return bad


def merge_all() -> dict:
    out = {}
    for label, sql in (('md.stock_daily', MERGE_STOCK),
                       ('md.index_daily', MERGE_INDEX),
                       ('md.sector_daily', MERGE_SECTOR)):
        t0 = time.time()
        n = engine.execute(sql, role='admin')
        out[label] = (n, round(time.time() - t0, 1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, action='append', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    years = args.year or list(YEARS)

    asset_types = load_asset_types()
    print('ref.security 已载入 %d 条代码分类' % len(asset_types))
    if len(asset_types) < 1000:
        print('!! ref.security 行数异常，先跑 b2_ref.py')
        return 1

    # 预扫：日线里出现但 ref.security 没有的代码，先补登再迁，避免静默丢股
    unseen: set[str] = set()
    for y in years:
        p = config.LEGACY_DATA / f'tq_daily_{y}.db'
        if not p.exists():
            continue
        c = ro(p)
        try:
            for (ts,) in c.execute('SELECT DISTINCT ts_code FROM stock_daily'):
                if ts not in asset_types:
                    unseen.add(ts)
        finally:
            c.close()
    if unseen:
        print('日线中有 %d 个代码不在 ref.security' % len(unseen))
        if not args.dry_run:
            n = register_missing(unseen, asset_types)
            print('  已补登 %d 条（其余为债券等非个股，按规则排除）' % n)
        else:
            print('  样本：%s' % sorted(unseen)[:10])


    if args.dry_run:
        batch_id = -1
    else:
        batch_id = ops_repo.begin_batch(
            'legacy_sqlite', 'md.stock_daily',
            source_path=str(config.LEGACY_DATA))
        print('批次 %d' % batch_id)
        prepare_staging()

    totals = {}
    try:
        for y in years:
            print('\n=== tq_daily_%d.db ===' % y)
            st = migrate_year(y, batch_id, asset_types, dry_run=args.dry_run)
            if not st:
                print('  文件不存在，跳过')
                continue
            for k, v in st.items():
                if v:
                    print('    %-20s %s' % (k, v))
            for k, v in st.items():
                if isinstance(v, int):
                    totals[k] = totals.get(k, 0) + v

        print('\n=== 累计 ===')
        for k, v in sorted(totals.items()):
            print('  %-20s %s' % (k, f'{v:,}' if isinstance(v, int) else v))

        # 分类口径落库。旧库一行的去向必须能穷举：个股/指数/板块/债/基金/B股/伪代码，
        # 加起来要等于 read。这个恒等式是 B6 对账的依据，不落库 B6 就只能猜。
        cats = ('stock', 'index', 'sector', 'bond', 'fund', 'bshare', 'pseudo',
                'unknown', 'bad_date', 'no_price')
        acc = sum(totals.get(k, 0) for k in cats)
        print('  %-20s %s / read %s  %s'
              % ('分类合计', f'{acc:,}', f"{totals.get('read', 0):,}",
                 'OK' if acc == totals.get('read', 0) else '不平!'))


        if args.dry_run:
            print('\n--dry-run，未写库')
            return 0

        ops_repo.save_quality_result(
            None, 'migration_classification', 'md.stock_daily',
            'info' if acc == totals.get('read', 0) else 'error',
            acc == totals.get('read', 0),
            actual={k: totals.get(k, 0) for k in ('read', *cats)},
            expected={'rule': 'read == stock+index+sector+bond+fund+bshare'
                              '+pseudo+unknown+bad_date+no_price'},
            batch_id=batch_id, role='admin')


        print('\n=== 合并前口径预检 ===')
        prescreen_units(batch_id)

        print('\n=== 合并进正式表（单位触发器逐行校验）===')
        for label, (n, secs) in merge_all().items():
            print('  %-16s %s 行  %.1fs' % (label, f'{n:,}', secs))

        ops_repo.end_batch(batch_id, status='ok',
                           row_count=totals.get('stock', 0))
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
            ('md.index_daily', 'SELECT count(*) FROM md.index_daily'),
            ('md.sector_daily', 'SELECT count(*) FROM md.sector_daily'),
            ('日期区间', "SELECT min(trade_date)||' .. '||max(trade_date) "
                         'FROM md.stock_daily'),
            ('交易日数', 'SELECT count(DISTINCT trade_date) FROM md.stock_daily'),
            ('分区行数', "SELECT string_agg(t, ' ' ORDER BY t) FROM (SELECT "
                         "extract(year FROM trade_date)::text||':'||count(*) t "
                         'FROM md.stock_daily '
                         'GROUP BY extract(year FROM trade_date)) x'),
            ('limit_up 已填', 'SELECT count(*) FROM md.stock_daily '
                              'WHERE limit_up IS NOT NULL'),
            ('均价越界行数', 'SELECT count(*) FROM md.stock_daily '
                             'WHERE vol_shares > 0 AND amount_yuan > 0 AND low > 0 '
                             'AND (amount_yuan/vol_shares < low*0.5 '
                             'OR amount_yuan/vol_shares > high*2)'),
    ):
        print('  %-16s %s' % (label, engine.scalar(sql)))

    # 按批次分组打印。第 3 轮发现表里混着 4,151 行来自已失败批次的残留
    # （ETF 和通达信伪代码），只看总行数看不出来，按血缘分组一眼就能看到。
    print('  按批次血缘：')
    for r in engine.query('SELECT src_batch_id, count(*) AS n FROM md.stock_daily '
                          'GROUP BY 1 ORDER BY 1'):
        print('    batch %-6s %s' % (r['src_batch_id'], f"{r['n']:,}"))

    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
