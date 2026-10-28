# -*- coding: utf-8 -*-
"""突破信号（右栏那半边）计算侧的三条回归，都是 2026-10-13 现场查出来的。

① `trade_days` 里 `INTERVAL '%s days'` 的参数**从来没被绑进去**（psycopg 的参数不进字符串字面量），
   区间塌成 0 天 ⇒ `--recent 205` 实测只交出 1 个交易日，退出码还是 0。
   「已经回算近一年」这件事就是这么被记成完成的。
② 主键是 `(security_code, trade_date, spec_version)`，同一天同一只票只能落一行。
   实测 2026-10-12 算出 626 条、库里 615 行：11 条撞在主键上，原先由写入顺序决定留哪条 ——
   等于"这只票今天算不算买点"取决于批次里谁排在后面。现在按 `TYPE_RANK` 明说。
③ 母体是 `COMPUTE_LIMIT` 只（按代码序），**不是全市场**。读侧必须交出这一格，
   否则页面上「命中 615 只」会被读成"全场 5,576 只里 615 只"。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.compute import breakout_signal as bs
from duanxian import pg, quant


def _sig(code, day, typ, converge=5):
    return {'security_code': code, 'trade_date': day, 'spec_version': bs.SPEC_VERSION,
            'signal_type': typ, 'converge_days': converge, 'break_vol_ratio': 1.7}


# ---------------------------------------------------------------- ① 交易日列表

def test_回算场次用的是_LIMIT_而不是坏掉的_INTERVAL(monkeypatch):
    seen = {}

    def fake_query(sql, params=()):
        seen['sql'] = sql
        seen['params'] = tuple(params)
        return [{'trade_date': dt.date(2026, 10, 12)}]

    monkeypatch.setattr(bs.engine, "query", fake_query)
    monkeypatch.setattr(bs, "_latest_date", lambda: dt.date(2026, 10, 12))
    days = bs.trade_days(None, 205)
    # 旧写法 `trade_date > %s - INTERVAL '%s days'` 会让 %s 留在字面量里、区间塌成 0 天
    assert 'INTERVAL' not in seen['sql'].upper(), seen['sql']
    assert 'LIMIT' in seen['sql'].upper(), "要算哪几天就该由 recent 直接决定"
    assert seen['params'] == (dt.date(2026, 10, 12), 205), seen['params']
    assert days == [dt.date(2026, 10, 12)]


def test_坏掉的_INTERVAL_今天确实只给出一场_所以这一条必须钉住():
    """不打桩：直接问库。旧写法在这里只会交出 1 场，修完交出 205 场。
    库里交易日不足 205 时按实际条数判，不硬写死数字。"""
    if not pg.enabled():
        pytest.skip('本机 PG 未就绪')
    total = bs.engine.query("select count(distinct trade_date) n from market_data.stock_daily")[0]['n']
    days = bs.trade_days(None, 205)
    assert len(days) == min(205, int(total)), (len(days), total)
    assert days == sorted(days), "回算要从早到晚，日志才看得出现在跑到哪一场"


# ---------------------------------------------------------------- ② 同日同码只留一行

def test_同一天同一条码_确认过的盖掉没确认的():
    a = [_sig('600000.SH', dt.date(2026, 10, 12), 'converging', converge=9),
         _sig('600000.SH', dt.date(2026, 10, 12), 'first_break', converge=9)]
    out = bs.collapse_same_day(a)
    assert len(out) == 1 and out[0]['signal_type'] == 'first_break'
    # 反过来排也要留同一条：谁赢不能由批次顺序决定
    out2 = bs.collapse_same_day(list(reversed(a)))
    assert out2[0]['signal_type'] == 'first_break'


def test_同类相撞留收敛段更长的那一条_不同票互不相干():
    same = [_sig('1.SH', dt.date(2026, 10, 12), 'converging', converge=5),
            _sig('1.SH', dt.date(2026, 10, 12), 'converging', converge=12)]
    assert bs.collapse_same_day(same)[0]['converge_days'] == 12
    many = same + [_sig('2.SZ', dt.date(2026, 10, 12), 'converging', converge=5),
                   _sig('3.SZ', dt.date(2026, 10, 11), 'first_break', converge=5)]
    assert len(bs.collapse_same_day(many)) == 3, "别的票、别的日子不该被一起收掉"
    assert len(bs.collapse_same_day([_sig('9.SZ', dt.date(2026, 10, 12), 'converging')])) == 1


# ---------------------------------------------------------------- ③ 母体

def test_计算侧不许再有母体上限():
    """`codes[:800]` 那一步是 2026-10-13 查出来的第二个错：
    不排序时扫哪 800 只由执行计划决定；一旦排序，前 800 只全是深主板 000/002，
    沪市/创业/科创/北证的票永远进不了这一榜（自选里点它就查不到突破历史）。
    改成 bulk 取数后全场单日实测十几秒，没有理由再截。"""
    import inspect
    assert not hasattr(bs, 'COMPUTE_LIMIT'), "母体上限回来了 = 榜单又只覆盖一部分票"
    assert inspect.signature(bs.compute_day).parameters['limit'].default is None


def test_读侧交出的母体是当天有行情的全部票(bo_stub_universe):
    bo_stub_universe['total'] = 5560
    out = quant.breakout_signal()
    u = out['universe']
    assert u['codes_total'] == 5560 and u['codes_scanned'] == 5560
    assert 'bars_by_code' in u['computed_by']
    assert 'breakout_universe_partial' not in [c['code'] for c in out['caveats']]


def test_bulk_取数交回的条形如逐票查询(scan_stub_bars):
    """`scan_window` 认的是 `trade_date/close/volume_shares/ma60` 四个键、按日期升序。
    bulk 改写如果键名或顺序变了，扫出来会静默变成"没有信号"，而不是报错。"""
    got = bs.bars_by_code(['000001.SZ', '600000.SH'], dt.date(2026, 10, 12))
    assert sorted(got) == ['000001.SZ', '600000.SH']
    bars = got['000001.SZ']
    assert [b['trade_date'] for b in bars] == sorted(b['trade_date'] for b in bars)
    assert set(bars[0]) == {'trade_date', 'close', 'volume_shares', 'ma60'}


@pytest.fixture
def scan_stub_bars(monkeypatch):
    def fake_query(sql, params=(), role=None):
        dts = [dt.date(2026, 10, 8), dt.date(2026, 10, 9), dt.date(2026, 10, 12)]
        return [{'c': c, 'dts': dts, 'cls': [10.0, 10.2, 10.4],
                 'vs': [1e6, 2e6, 3e6], 'mas': [9.9, 10.0, 10.1]}
                for c in params['codes']]

    monkeypatch.setattr(bs.engine, "query", fake_query)
    return True


@pytest.fixture
def bo_stub_universe(monkeypatch):
    """右栏读侧的取数桩。`total` 是"当天有行情的票数"，控制母体那条警告出不出。"""
    box = {'rows': [_bo_row()], 'counts': [{'t': 'first_break', 'n': 7}], 'total': 5576}

    def _rows(sql, params=()):
        if 'group by 1' in sql:
            return list(box['counts'])
        return list(box['rows'])

    monkeypatch.setattr(pg, "enabled", lambda: True)
    monkeypatch.setattr(quant.pg, "session_has", lambda table, d: True)
    # 桩的签名要跟真函数一致：真的 `_latest(table, spec=None)` 带判据版本条件，
    # 桩只收一个位置参数会在被调用时 TypeError。
    monkeypatch.setattr(quant, "_latest", lambda table, spec=None: "2026-10-12")
    monkeypatch.setattr(quant, "_rows", _rows)
    monkeypatch.setattr(quant, "_one",
                        lambda sql, params=(): ({'n': 615} if 'count(*)' in sql
                                                else {'n': box['total']}))
    monkeypatch.setattr(quant, "_coverage", lambda tables: {})
    monkeypatch.setattr(quant, "_comments", lambda table: {})
    monkeypatch.setattr(quant, "_cache_caveat", lambda keys: [])
    monkeypatch.setattr(quant, "_caveats", lambda key: [])
    return box


def _bo_row():
    return {'code': '301421.SZ', 'name': '测试股', 'board': '创业板',
            'signal_type': 'first_break', 'last_surge_date': '2026-07-01',
            'surge_vol_ratio': 1.98, 'shrink_days': 6, 'converge_days': 12,
            'break_vol_ratio': 1.72, 'break_dev': 0.0213, 'break_close': 30.1,
            'break_ma60': 29.5, 'window_days': 40, 'bars_used': 100}
