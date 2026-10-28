# -*- coding: utf-8 -*-
"""
通达信收盘快照库这条链路（`scripts/ingest/c15_snapshot.py` → `md.stock_snapshot`）。

**这个文件的重点是两个已经踩过的坑**，改代码时最容易悄悄踩回去：

① **表名里的日期是采集日期，不是交易日期。** `day_end_20260801` 是周六
   17:45 采的、装的是 07-31 的数据；`day_end_20260821` 是周四凌晨 00:58 采的、
   装的是 08-20 的数据。第一版按表名写库，07-31 的市值被挂到 08-01 这个
   根本不存在的交易日上，而且看结果只会觉得"那天匹配率低"。

② **通达信的 `ZTPrice` 不是权威。** 08-31 有 324 只与库里不一致，逐只查完是：
   175 只北交所差一分钱（库四舍五入 14.60、通达信截断 14.59，库对）、
   142 只主板 ST（库 ±5% 对，通达信给的是不区分 ST 的 ±10%）、
   2 只库里为空（这类才该补）。所以默认只填空，不覆盖。

数据依赖：本机 `D:\\new_tdx64\\daily_close_data.db` 与库里已摄入的快照。
两者任一不在就 skip —— 别人的机器上没有这个文件，不该因此变红。
"""
import datetime as dt
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from app import config
from app.db import engine
from app.orchestrator import runner

ROOT = Path(__file__).resolve().parent.parent
C15 = ROOT / 'scripts' / 'ingest' / 'c15_snapshot.py'
DB_PATH = config.TDX_DIR / 'daily_close_data.db'


def load_c15():
    """按文件路径 import —— scripts/ 不是包。"""
    spec = importlib.util.spec_from_file_location('c15_snapshot', C15)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                       # type: ignore[union-attr]
    return mod


@pytest.fixture(scope='module')
def c15():
    return load_c15()


@pytest.fixture(scope='module')
def snapdb():
    if not DB_PATH.is_file():
        pytest.skip(f'本机没有 {DB_PATH}')
    con = sqlite3.connect(f'file:{DB_PATH.as_posix()}?mode=ro', uri=True)
    yield con
    con.close()


@pytest.fixture(scope='module')
def snap_dates():
    rows = engine.query(
        'SELECT DISTINCT trade_date FROM md.stock_snapshot ORDER BY trade_date')
    if not rows:
        pytest.skip('md.stock_snapshot 还没摄入过')
    return [r['trade_date'] for r in rows]


# ------------------------------------------------------------ 代码归一
def test_normalize_code_only_accepts_suffixed_codes(c15):
    assert c15.normalize_code('600519.SH') == '600519.SH'
    assert c15.normalize_code(' 000001.SZ ') == '000001.SZ'
    assert c15.normalize_code('920932.BJ') == '920932.BJ'
    # 下面这些都必须退回 OHLC 反查，不能当代码用：
    # 1 是 clean_value 把 '000001' 转成 int 的结果，猜错票是无声的错
    assert c15.normalize_code(1) is None
    assert c15.normalize_code('000001') is None
    assert c15.normalize_code('600519.XX') is None
    assert c15.normalize_code(None) is None


# ------------------------------------------------------------ 坑 ①：日期
def test_table_name_is_collect_date_not_trade_date(c15, snapdb):
    """
    `day_end_20260801` / `day_end_20260802` 都装 07-31 的数据（周末采集），
    `day_end_20260821` 装 08-20 的（凌晨采集）。按表名写就全错位。
    """
    cases = {'day_end_20260801': dt.date(2026, 7, 31),
             'day_end_20260802': dt.date(2026, 7, 31),
             'day_end_20260821': dt.date(2026, 8, 20)}
    names = {n for (n,) in snapdb.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    checked = 0
    for table, want in cases.items():
        if table not in names:
            continue
        recs = c15.read_table(snapdb, table)
        keys = {(d.get('Open'), d.get('Max'), d.get('Min'), d.get('Now'))
                for d in recs}
        table_day = dt.datetime.strptime(table[-8:], '%Y%m%d').date()
        got, ratio, _ = c15.resolve_trade_date(keys, table_day)
        assert got == want, f'{table} 应该定到 {want}，却定到 {got}'
        assert ratio >= c15.MATCH_MIN
        assert got != table_day, '这几张表的意义就在于"表名日期≠交易日"'
        checked += 1
    if not checked:
        pytest.skip('这几张表在本机的快照库里不存在')


def test_intraday_tables_are_skipped_not_written(c15, snapdb):
    """
    盘中/盘前采集的表（`Now` 是当时现价）命中率只有 0%~6%，必须被判成 None。
    写进去的话那天的市值是按盘中价算的，而表面上看不出来。
    """
    names = {n for (n,) in snapdb.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    checked = 0
    for table in ('day_end_20260721', 'day_end_20260818', 'day_end_20260825'):
        if table not in names:
            continue
        recs = c15.read_table(snapdb, table)
        keys = {(d.get('Open'), d.get('Max'), d.get('Min'), d.get('Now'))
                for d in recs}
        table_day = dt.datetime.strptime(table[-8:], '%Y%m%d').date()
        got, ratio, _ = c15.resolve_trade_date(keys, table_day)
        assert got is None, f'{table} 是盘中采的，不该被当成收盘快照'
        assert ratio < c15.MATCH_MIN
        checked += 1
    if not checked:
        pytest.skip('这几张表在本机的快照库里不存在')


def test_written_dates_are_real_trading_days(snap_dates):
    """库里每个 trade_date 都要在 md.stock_daily 里有行情 —— 08-01 那种周末不行。"""
    bad = engine.query(
        'SELECT DISTINCT s.trade_date FROM md.stock_snapshot s '
        ' WHERE NOT EXISTS (SELECT 1 FROM md.stock_daily d '
        '                    WHERE d.trade_date = s.trade_date)')
    assert not bad, f'这些日期没有日线：{[r["trade_date"] for r in bad]}'


# ------------------------------------------------------------ 坑 ②：涨跌停价
def test_fill_only_never_overwrites_existing_limits(c15):
    """默认那条 SQL 必须只动空值。"""
    assert 'IS NULL' in c15.LIMIT_FILL
    assert 'coalesce' in c15.LIMIT_FILL
    # 覆盖版留着但要显式开关；两条不能是同一条
    assert c15.LIMIT_FILL != c15.LIMIT_OVERWRITE
    assert 'IS DISTINCT FROM' in c15.LIMIT_OVERWRITE


def test_overwrite_limits_is_opt_in():
    """`--overwrite-limits` 不能有默认值以外的行为：默认必须是关的。"""
    src = C15.read_text(encoding='utf-8')
    assert "'--overwrite-limits', action='store_true'" in src
    assert "'--no-fill-limits', action='store_true'" in src
    # 早先那个"默认开的覆盖"开关必须已经不存在了
    assert '--fix-limits' not in src


def test_st_stocks_keep_the_five_percent_limit(snap_dates):
    """
    主板 ST 的涨跌停是 ±5%。**这是坑 ② 的回归闸门**：真被通达信的值覆盖过，
    这里会看到 ±10%。用库里实际的 is_st 主板票算，不写死代码清单。
    """
    d = snap_dates[-1]
    rows = engine.query(
        """
        SELECT d.ts_code, d.pre_close, d.limit_up,
               round(d.limit_up / d.pre_close, 3) AS ratio
          FROM md.stock_daily d
          JOIN ref.security r ON r.ts_code = d.ts_code
         WHERE d.trade_date = %s AND r.is_st AND d.pre_close > 0
           AND d.limit_up IS NOT NULL
           AND (d.ts_code LIKE '60%%' OR d.ts_code LIKE '00%%')
         LIMIT 200
        """, (d,))
    if not rows:
        pytest.skip('这天没有主板 ST 样本')
    bad = [r for r in rows if not (1.04 < float(r['ratio']) < 1.06)]
    assert not bad, f'主板 ST 的涨停幅度不是 5%：{bad[:5]}'


# ------------------------------------------------------------ 值的量级
def test_market_cap_is_in_yuan_and_float_never_exceeds_total(snap_dates):
    d = snap_dates[-1]
    r = engine.query_one(
        """
        SELECT count(*) n,
               count(*) FILTER (WHERE total_mv_yuan IS NULL) mv_null,
               count(*) FILTER (WHERE float_mv_yuan > total_mv_yuan * 1.001) fmv_gt,
               round(min(total_mv_yuan) / 1e8, 2) mv_min_yi,
               round(max(total_mv_yuan) / 1e8, 0) mv_max_yi
          FROM md.stock_snapshot WHERE trade_date = %s
        """, (d,))
    assert r['n'] > 4000, '一天应该有五千多只票'
    assert r['mv_null'] == 0
    assert r['fmv_gt'] == 0, '流通市值不可能大于总市值'
    # 单位是元：最小的票 1 亿量级、最大的 4 万亿以内。若误按"亿元"直存，
    # 这两个数会小一亿倍，一眼就抓出来
    assert 0.5 < float(r['mv_min_yi']) < 100
    assert 5_000 < float(r['mv_max_yi']) < 60_000


def test_total_mv_equals_close_times_shares(snap_dates):
    """总市值 / 收盘价 = 总股本。抽查几只大票，钉住"市值配的是同一天的价"。"""
    d = snap_dates[-1]
    rows = engine.query(
        """
        SELECT s.ts_code, s.total_mv_yuan / d.close / 1e8 AS shares_yi
          FROM md.stock_snapshot s
          JOIN md.stock_daily d
            ON d.ts_code = s.ts_code AND d.trade_date = s.trade_date
         WHERE s.trade_date = %s AND s.ts_code = ANY(%s)
        """, (d, ['600519.SH', '601398.SH', '000001.SZ']))
    if not rows:
        pytest.skip('这天没有这几只样本')
    want = {'600519.SH': 12.5, '601398.SH': 2696.0, '000001.SZ': 194.1}
    for r in rows:
        got = float(r['shares_yi'])
        exp = want[r['ts_code']]
        assert abs(got - exp) / exp < 0.02, \
            f'{r["ts_code"]} 反算股本 {got:.2f} 亿股，期望 {exp} 亿股'


def test_extra_keeps_the_fields_that_have_no_column(snap_dates):
    """六个定型列之外的东西都在 extra 里 —— 涨跌停价、内外盘、量比得能取到。"""
    d = snap_dates[-1]
    r = engine.query_one(
        'SELECT extra FROM md.stock_snapshot '
        ' WHERE trade_date = %s AND ts_code = %s', (d, '600519.SH'))
    if not r:
        pytest.skip('这天没有 600519.SH')
    extra = r['extra']
    for k in ('ZTPrice', 'DTPrice', 'Inside', 'Outside', 'fLianB', 'Zsz'):
        assert k in extra, f'extra 里少了 {k}'


# ------------------------------------------------------------ 注册齐不齐
def test_ingest_snapshot_is_a_real_runner():
    r = runner.RUNNERS['ingest_snapshot']
    assert (ROOT / r['script']).is_file()
    assert r['date'] is None, '日期由脚本自己按数据回推，不该由调用方传'


def test_job_and_datasource_are_registered():
    job = engine.query_one(
        'SELECT params, enabled FROM config.job WHERE job_key = %s',
        ('sync_snapshot',))
    assert job, 'config.job 里没有 sync_snapshot'
    assert job['params']['runner'] == 'ingest_snapshot'

    ds = engine.query_one(
        'SELECT probe_spec, layer FROM config.datasource WHERE ds_key = %s',
        ('tdx_snapshot_db',))
    assert ds, 'config.datasource 里没有 tdx_snapshot_db'
    assert ds['probe_spec']['kind'] == 'snapdb'
    assert ds['layer'] == 'p0'

    src = engine.query_one(
        'SELECT parser_key FROM config.data_source WHERE source_key = %s',
        ('tdx_snapshot_db',))
    assert src, 'config.data_source 里没有 tdx_snapshot_db'


def test_snapdb_probe_is_implemented():
    """探活 kind 登记了就必须有实现 —— 页面上一条永远 unknown 的源等于骗人。"""
    from app.datasource import probe

    got = probe.probe_one({'ds_key': 'tdx_snapshot_db',
                           'probe_spec': {'kind': 'snapdb'}})
    assert got['health'] in ('ok', 'degraded', 'down', 'broken')
    assert got['message']
    if DB_PATH.is_file():
        assert got['health'] in ('ok', 'degraded')
        assert '日表' in got['message']
