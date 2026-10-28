# -*- coding: utf-8 -*-
"""首页「涨停与连板」母体写侧（`app/compute/limit_up_pool.py`，lup-1）的闸。

分两层，故意的：

① **纯判据不打桩**（连板连续计数、分钟线首封/回封/开板次数、流通市值两把尺、组行）——
   这几条是这一屏最容易写错的地方：按票自己的行数往前数会把停牌那几天算进连板；
   按 `lag` 数开板会把"隔了 7 场"说成"上一场"；缺分钟线兜成 09:25 就是一句假读数。
② **落库与登记**：列清单必须与库里真列一致（少一列 INSERT 当场炸，比跑到一半红好），
   重算一场必须先清这一场这一版。
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.compute import limit_up_pool as L                       # noqa: E402
from app.compute.limit_prices import LIMIT_EPS                   # noqa: E402

D = dt.date(2026, 10, 23)


def _sessions(*days):
    return [dt.date.fromisoformat(x) for x in days]


# ---------------------------------------------------------------- ① 连板：按市场场次往前数

def test_连板数按市场场次连续封住():
    s = _sessions('2026-10-21', '2026-10-22', '2026-10-23')
    n, start = L.consec_at(D, s, {D, s[0], s[1]})
    assert (n, str(start)) == (3, '2026-10-21')


def test_中间断一场就重新起算():
    s = _sessions('2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23')
    n, start = L.consec_at(D, s, {s[0], s[2], D})       # 10-21 没封 ⇒ 断了
    assert (n, str(start)) == (2, '2026-10-22')


def test_停牌那天没有行也算断_不许按票自己的行数往前数():
    """这只票 10-21 整天停牌（库里没有它的行），10-20 封过、10-22 又封：
    按自己的 K 线往前数会得到 3 连板，按市场场次数只有 2。"""
    s = _sessions('2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23')
    n, _ = L.consec_at(D, s, {s[0], s[2], D})
    assert n == 2, "停牌那天没有封住 ⇒ 这一轮从 10-22 重新起算，不能说 3 板"


def test_本场没封住就没有这一轮_交None而不是0板():
    s = _sessions('2026-10-22', '2026-10-23')
    assert L.consec_at(D, s, {s[0]}) == (None, None)
    assert L.consec_at(D, [], {D}) == (None, None), "场次表里没有这一场 ⇒ 判不了，不猜 1 板"


# ---------------------------------------------------------------- ① 分钟线：首封/回封/开板

def _bars(*pairs):
    return [(dt.time(h, m), c) for (h, m), c in pairs]


def test_首封是第一根收盘还在档位上的分钟():
    st = L.seal_stats(_bars(((9, 30), 9.0), ((9, 31), 10.0), ((9, 32), 10.0)), 10.0)
    assert st['first_seal_at'] == dt.time(9, 31)
    assert st['last_seal_at'] == dt.time(9, 32)
    assert st['at_limit_minutes'] == 2 and st['reopen_count'] == 0
    assert st['minute_bars'] == 3


def test_开板次数数的是从档位上掉下来的那几次():
    st = L.seal_stats(_bars(((9, 31), 10.0), ((9, 32), 9.6), ((9, 33), 10.0),
                            ((9, 34), 9.4), ((9, 35), 10.0)), 10.0)
    assert st['reopen_count'] == 2
    assert (str(st['first_seal_at']), str(st['last_seal_at'])) == ('09:31:00', '09:35:00')


def test_盘中从没封住时三个时刻全是None而不是0():
    """炸板行：日线 high 冲过档位，分钟收盘一根都没停在档位上。
    `at_limit_minutes=0` 是真读数，`reopen_count=None` 才是"没有开板这件事可言"。"""
    st = L.seal_stats(_bars(((9, 31), 9.8), ((9, 32), 9.5)), 10.0)
    assert st['first_seal_at'] is None and st['last_seal_at'] is None
    assert st['reopen_count'] is None and st['at_limit_minutes'] == 0


def test_没有分钟线或没有档位就是整组None():
    assert L.seal_stats([], 10.0)['first_seal_at'] is None
    assert L.seal_stats(_bars(((9, 31), 10.0)), None)['at_limit_minutes'] is None


def test_容差用的是全项目那一份半分():
    assert L.LIMIT_EPS == LIMIT_EPS == 0.005, "不许在这一侧另写一份容差"
    st = L.seal_stats(_bars(((9, 31), 9.996),), 10.0)
    assert st['at_limit_minutes'] == 1, "半分以内的毛刺算封住（与 firstboard 严格口径同一条）"


def test_hhmmss_形状与封板强度那条尺接得上():
    assert L.hhmmss_of(dt.time(9, 25, 30)) == 92530
    assert L.hhmmss_of(None) is None


# ---------------------------------------------------------------- ① 流通市值两把尺

def test_池里有就只认池里那份():
    assert L.float_cap_of(1e9, 5e8, 2.0) == (1e9, 'source')


def test_池里没有才用当日日线反推():
    cap, basis = L.float_cap_of(None, 1e8, 4.0)      # 100 × 1e8 ÷ 4 = 2.5e9+...（元）
    assert basis == 'daily_replay' and abs(cap - 2.5e9) < 1


def test_换手率缺或为零就不反推_交None而不是无穷大():
    assert L.float_cap_of(None, 1e8, 0) == (None, None)
    assert L.float_cap_of(None, None, 2.0) == (None, None)


# ---------------------------------------------------------------- ① 组一行（assemble）

def _base(code6, full, name, *, sealed=True, touched=True, close=10.0):
    return {'code': code6, 'security_code': full, 'name': name, 'sealed': sealed,
            'touched': touched, 'limit_up_price': 10.0, 'close': close, 'pct_change': 10.0,
            'turnover_pct': 5.0, 'amount_yuan': 1e8}


def test_整场没有分钟线时首封为空并记的是那一天缺而不是这只票缺():
    rows = L.assemble(D, [_base('000001', '000001.SZ', '甲')], _sessions('2026-10-23'),
                      {'000001.SZ': {D}}, {}, {}, None)
    r = rows[0]
    assert r['first_seal_at'] is None and r['seal_basis'] == 'none'
    assert r['missing']['first_seal_at'] == L.GAP_NO_MINUTE_DAY
    assert r['missing']['reason'] == L.GAP_SRC_ROW, "池里没这只票 ⇒ 逐行记"


def test_源站整场取不到时缺数记的是那一种而不是逐行缺席():
    """超保留窗（永久缺）与"这只票源站不收"是两件事：前者界面要说"补不回来"，
    后者要说"池不含北交所/ST"。写侧把整场原因原样落到每一行。"""
    rows = L.assemble(D, [_base('000001', '000001.SZ', '甲')], _sessions('2026-10-23'),
                      {'000001.SZ': {D}}, {}, {}, L.GAP_SRC_NO_RECORDS)
    assert rows[0]['missing']['reason'] == L.GAP_SRC_NO_RECORDS
    assert rows[0]['missing']['order_amount'] == L.GAP_SRC_NO_RECORDS
    assert rows[0]['src_has_row'] is False


def test_没有分钟线但源站有首封时退回源站并标出出处():
    pool = {'000001': {'first_limit_up_time': str(int(dt.datetime(2026, 10, 23, 9, 25, 30)
                                                       .replace(tzinfo=dt.timezone.utc)
                                                       .timestamp()) - 8 * 3600),
                       'last_limit_up_time': None, 'open_num': 0, 'order_amount': 3e6,
                       'order_volume': 2e5, 'limit_up_type': '一字板',
                       'high_days': '2天2板', 'currency_value': 1e9,
                       'reason_type': '概念'}}
    rows = L.assemble(D, [_base('000001', '000001.SZ', '甲')], _sessions('2026-10-23'),
                      {'000001.SZ': {D}}, {}, pool, None)
    r = rows[0]
    assert r['seal_basis'] == 'source' and r['first_seal_at'] is None
    assert r['src_first_seal_at'] == dt.time(9, 25, 30)
    assert r['reason'] == '概念' and r['order_amount'] == 3e6
    assert r['float_cap_yuan'] == 1e9 and r['float_cap_basis'] == 'source'
    assert r['missing']['first_seal_at'] == 'local_minute_absent_use_source'


def test_有分钟线时自算优先_两把尺都留在行里():
    minutes = {'000001': _bars(((9, 30), 9.0), ((9, 31), 10.0))}
    rows = L.assemble(D, [_base('000001', '000001.SZ', '甲')], _sessions('2026-10-23'),
                      {'000001.SZ': {D}}, minutes, {}, None)
    assert rows[0]['first_seal_at'] == dt.time(9, 31)
    assert rows[0]['seal_basis'] == 'local' and rows[0]['src_high_days'] is None


def test_收盘没封住的行没有连板可言_交None而不是1板():
    rows = L.assemble(D, [_base('000002', '000002.SZ', '乙', sealed=False)],
                      _sessions('2026-10-23'), {'000002.SZ': {D}}, {}, {}, None)
    assert rows[0]['consec'] is None and rows[0]['streak_start'] is None
    assert rows[0]['sealed'] is False and rows[0]['touched'] is True


def test_北证与ST不在池里时缺数记的是源站不收而不是本机没采到():
    rows = L.assemble(D, [_base('920091', '920091.BJ', '丙')], _sessions('2026-10-23'),
                      {'920091.BJ': {D}}, {}, {'000001': {}}, None)
    r = rows[0]
    assert r['src_has_row'] is False
    assert r['missing']['reason'] == L.GAP_SRC_ROW
    assert r['float_cap_basis'] == 'daily_replay', "池里没有这只票 ⇒ 流通市值只能走日线反推"


def test_池里有这只票但某一栏没给时要点名是哪一栏():
    """实测同花顺池内 50 只里 27 只没给 `open_num`：那一格 null 与「0 次＝从头封到尾」
    是两件事，`missing` 必须分开说，读者才知道该看哪一栏。"""
    raw = {'first_limit_up_time': None, 'order_amount': 3e6, 'limit_up_type': '换手板'}
    rows = L.assemble(D, [_base('000001', '000001.SZ', '甲')], _sessions('2026-10-23'),
                      {'000001.SZ': {D}}, {}, {'000001': {**raw, 'currency_value': 1e9,
                                                           'reason_type': '概念'}}, None)
    miss = rows[0]['missing']
    assert miss['src_reopen'] == L.GAP_SRC_FIELD and miss['src_high_days'] == L.GAP_SRC_FIELD
    assert rows[0]['src_reopen'] is None and rows[0]['order_amount'] == 3e6


def test_没去取源站与取不到是两种缺数():
    """`--no-source`（全史回算）落的那一批要是标成「源站不收这只票」，界面上那句解释就是假的。"""
    out = L.compute_day(D, fetch_source=False, pairs={}, sessions=_sessions('2026-10-23'))
    assert out['src_gap'] == L.GAP_SRC_NOT_FETCHED
    assert out['rows'][0]['missing']['reason'] == L.GAP_SRC_NOT_FETCHED
    # 取到了但整场没有（超出保留窗）⇒ 走另一条码
    out2 = L.compute_day(D, pool={}, src_gap=L.GAP_SRC_NO_RECORDS, fetch_source=False,
                         pairs={}, sessions=_sessions('2026-10-23'))
    assert out2['rows'][0]['missing']['reason'] == L.GAP_SRC_NO_RECORDS


def test_每行都带着口径版本与场次():
    rows = L.assemble(D, [_base('000001', '000001.SZ', '甲')], _sessions('2026-10-23'),
                      {'000001.SZ': {D}}, {}, {}, None)
    assert rows[0]['spec_version'] == L.SPEC_VERSION == 'lup-1'
    assert rows[0]['trade_date'] == D


# ---------------------------------------------------------------- ② 列清单与库一致

def test_COLs_与库里的列一一对应():
    """少一列 ⇒ 那一族的数悄悄不落库；多一列 ⇒ INSERT 当场炸。两边都要在这里红。"""
    from app.db import engine

    got = {r['column_name'] for r in (engine.query(
        "select column_name from information_schema.columns "
        "where table_schema='analytics' and table_name='limit_up_pool_daily'") or [])}
    if not got:
        pytest.skip('本机库里还没跑过 53_limit_up_pool_daily.sql')
    assert set(L.COLS) == got - {'calculated_at'}, \
        f"写侧与库不一致：只在写侧 {set(L.COLS) - got}，只在库里 {got - set(L.COLS) - {'calculated_at'}}"


def test_insert_的列与占位符都从一份清单生成():
    assert L.INSERT_SQL.count('%(') == len(L.COLS)
    assert "::jsonb" in L.INSERT_SQL, "missing 是 jsonb，不显式 cast 会类型不匹配"


def test_重算一场先清这一场这一版(monkeypatch):
    calls = []

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None): calls.append((sql.split()[0], params))
        def executemany(self, sql, payload): calls.append(('executemany', len(payload)))

    monkeypatch.setattr(L.engine, 'cursor', lambda role='etl': _Cur())
    row = dict(security_code='000001.SZ', trade_date=D, spec_version=L.SPEC_VERSION,
               missing={'reason': L.GAP_SRC_ROW})
    assert L.write([row]) == 1
    kinds = [c[0] for c in calls]
    assert kinds[0] == 'delete', "没有先清场，上一轮半截残留会一直挂在页面上"
    assert calls[0][1] == (D, L.SPEC_VERSION), "清场必须带上口径版本，否则新批次会抹掉旧批次"
    assert kinds[-1] == 'executemany'
    assert calls[-1][1] == 1


def test_空批什么都不写(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError('空批不该开库连接')
    monkeypatch.setattr(L.engine, 'cursor', _boom)
    assert L.write([]) == 0


def test_缺数短码都有一句人话():
    """读侧那句"这一格为什么是 —"直接读 `GAP_LABELS`：短码没配文字就是界面上一个裸码。"""
    for code in (L.GAP_NO_MINUTE_DAY, L.GAP_NO_MINUTE_ROW, L.GAP_SRC_NO_RECORDS,
                 L.GAP_SRC_HTTP, L.GAP_SRC_ROW, L.GAP_SRC_FIELD, L.GAP_TURNOVER_ZERO):
        assert code in L.GAP_LABELS and L.GAP_LABELS[code]
    for text in L.GAP_LABELS.values():
        assert '**' not in text, "这段文字会上屏幕（悬停/说明框），不许带 markdown 粗体标记"
