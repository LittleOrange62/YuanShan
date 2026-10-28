# -*- coding: utf-8 -*-
"""资金轮动趋势（`app/compute/sector_flow_trend.py`）的判据回归。

守三件事：
  1. **份额而不是绝对额** —— 实测用绝对额测连续性会得到"73%→83% 延续"的假惯性（08 契约坑 12）；
  2. **攒不满窗口不许假装攒满** —— `days_N` 必须与 `cum_N` 一起交出，占比不足时不给名次；
  3. **主力净额那条路没数据就明说不做** —— 不用份额或代理值凑一个"看起来像资金流"的数。
"""
from __future__ import annotations

import datetime as dt

from app.compute import sector_flow_trend as sft

D = dt.date


def _p(**kw):
    p = dict(sft.FALLBACK)
    p.update(kw)
    return p


def test_streak_只看尾部同向长度():
    assert sft._streak([0.01, 0.02, 0.03, 0.05]) == (4, 0)
    assert sft._streak([0.05, 0.04, 0.03]) == (0, 3)
    # 中间断一次，只数尾巴上那一段
    assert sft._streak([0.05, 0.01, 0.02, 0.03]) == (3, 0)
    # 相等既不是升也不是降：断链，两个方向都不给
    assert sft._streak([0.02, 0.02]) == (0, 0)
    assert sft._streak([0.02]) == (0, 0)


def test_两个方向不许同时为正():
    """表上有 CHECK 挡着，但判据本身先要自洽（否则整批行会被约束打回）。"""
    for seq in ([1, 2, 3], [3, 2, 1], [1, 1], [2, 1, 2], [1, 2, 1]):
        up, dn = sft._streak(seq)
        assert not (up > 0 and dn > 0), seq


def test_份额口径_分母是当日全场合计(monkeypatch):
    """A 板块成交额翻倍、B 不动 → A 份额升、B 份额**降**（钱在轮动，不是大家一起涨）。"""
    days = [D(2026, 1, 1), D(2026, 1, 2)]
    series = {
        'A': [{'date': days[0], 'amount': 100.0, 'pct': 1.0},
              {'date': days[1], 'amount': 300.0, 'pct': 2.0}],
        'B': [{'date': days[0], 'amount': 100.0, 'pct': 0.5},
              {'date': days[1], 'amount': 100.0, 'pct': -0.2}],
    }
    totals = {days[0]: 200.0, days[1]: 400.0}
    monkeypatch.setattr(sft, '_fetch', lambda p, dmax, need: (series, totals, days))
    written = []
    monkeypatch.setattr(sft, '_flush', lambda batch: written.extend(batch) or len(batch))

    out = sft.run_shares([days[1]], _p(windows=[10, 20, 30]), dry_run=False)
    rows = {r['code']: r for r in written}
    assert rows['A']['value'] == 0.75 and rows['B']['value'] == 0.25
    assert rows['A']['up'] == 2 and rows['A']['dn'] == 0
    assert rows['B']['dn'] == 2 and rows['B']['up'] == 0        # B 绝对额没变，份额在掉
    assert rows['A']['delta'] == 2500.0                         # 25 个百分点 = 2500bp
    assert out['rows'] == 2


def test_攒不满窗口时_给出实际场次且不参与排名(monkeypatch):
    days = [D(2026, 1, i + 1) for i in range(5)]        # 全场只有 5 场，10/20/30 都攒不满
    series = {'A': [{'date': d, 'amount': 100.0 + i, 'pct': 0.1} for i, d in enumerate(days)]}
    totals = {d: 100.0 for d in days}
    monkeypatch.setattr(sft, '_fetch', lambda p, dmax, need: (series, totals, days))
    written = []
    monkeypatch.setattr(sft, '_flush', lambda batch: written.extend(batch) or len(batch))

    out = sft.run_shares([days[-1]], _p(windows=[10, 20, 30], min_days_ratio=0.6), dry_run=False)
    r = written[0]
    assert (r['d10'], r['d20'], r['d30']) == (5, 5, 5)   # 诚实：只有 5 场，不是"10 场的累计"
    assert r['cum10'] is not None                        # 数照给，让读的人自己判断可比性
    assert (r['r10'], r['r20'], r['r30']) == (None, None, None)   # 但 5/10 < 0.6 → 不给名次
    assert r['reasons'].find('"days_with_value"') > 0


def test_dry_run_一行不写(monkeypatch):
    days = [D(2026, 1, 1), D(2026, 1, 2)]
    series = {'A': [{'date': d, 'amount': 10.0, 'pct': 0.0} for d in days]}
    monkeypatch.setattr(sft, '_fetch', lambda p, dmax, need: (series, {d: 10.0 for d in days}, days))
    calls = []
    monkeypatch.setattr(sft, '_flush', lambda batch: calls.append(len(batch)) or 0)
    out = sft.run_shares([days[1]], _p(), dry_run=True)
    assert out['rows'] == 0 and calls == []


def test_目标日没行情_报no_data而不是默写零行(monkeypatch):
    days = [D(2026, 1, 1), D(2026, 1, 2)]
    series = {'A': [{'date': d, 'amount': 10.0, 'pct': 0.0} for d in days]}
    monkeypatch.setattr(sft, '_fetch', lambda p, dmax, need: (series, {d: 10.0 for d in days}, days))
    out = sft.run_shares([D(2026, 3, 9)], _p(), dry_run=True)
    assert out['rows'] == 0 and out['no_data_days'] == ['2026-03-09']


def test_主力净额没入库时明说不做(monkeypatch):
    monkeypatch.setattr(sft.engine, 'query_one',
                        lambda sql, params=None, **k: {'rows': 0, 'days': 0, 'hi': None})
    out = sft.run_main_net([D(2026, 9, 23)], _p())
    assert out['rows'] == 0 and out['sessions_needed'] == 30
    assert 'fund_flow_rank' in out['skipped'] and '补不出来' in out['skipped']


def test_参数只从单行读_不靠_max_字符串比(monkeypatch):
    got = {}

    def qone(sql, params=None, **k):
        got['sql'] = sql
        return {'value': {'spec_version': 'sft-7', 'windows': [5, 10],
                          'sector_types': ['概念'], 'basis': ['share'],
                          'min_days_ratio': 0.8}}
    monkeypatch.setattr(sft.engine, 'query_one', qone)
    p = sft.params()
    assert p['spec_version'] == 'sft-7' and p['windows'] == [5, 10]
    assert 'config.setting' in got['sql'] and 'max(' not in got['sql'].lower()
