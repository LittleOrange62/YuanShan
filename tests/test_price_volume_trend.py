# -*- coding: utf-8 -*-
"""「量价走势（日 / 周 / 月）」（`duanxian/price_volume_trend.py`）的聚合与三态。

三块都从同一张日线表来，所以最要钉的是**聚合没漏也没重**、**复权口径说得清**、
**"本期未走完"不许被当成一整期**。真库那两条不打桩。
"""
from __future__ import annotations

import json

import pytest

from duanxian import price_volume_trend as P


def day(date, o, h, l, c, vol=100.0, amt=1e8, trn=1.0, af=1.0):
    import datetime as dt
    return {'trade_date': dt.date.fromisoformat(date), 'open': o, 'high': h, 'low': l,
            'close': c, 'volume_shares': vol, 'amount_yuan': amt,
            'turnover_rate_percent': trn, 'adjustment_factor': af}


# ------------------------------------------------------------------ 日线 → K 线

def test_单根日线的复权与原始收同时交出():
    b = P._bar([day('2026-10-15', 1286, 1317.76, 1282, 1312.97, af=0.5)])
    assert b['close'] == pytest.approx(1312.97 * 0.5)     # 图上那根是折算后的
    assert b['close_raw'] == pytest.approx(1312.97)        # 悬停给的是真成交价
    assert b['open'] == pytest.approx(1286 * 0.5)
    assert b['bars'] == 1
    assert 'partial' not in b, '`partial` 是 _series 给的（"这一期走完了没"），不是单根日线的属性'


def test_周线聚合不重复也不漏():
    """一根周线吃掉五根日线：开=首根开、高低=五根里的极值、收=末根收、量额相加。"""
    raw = [day('2026-10-12', 10, 12, 9, 11), day('2026-10-13', 11, 15, 10, 14),
           day('2026-10-14', 14, 14, 8, 9), day('2026-10-15', 9, 13, 9, 12)]
    bars = [P._bar([r]) for r in raw]
    w = P._rollup(bars)
    assert (w['open'], w['high'], w['low'], w['close']) == (10.0, 15.0, 8.0, 12.0)
    assert w['bars'] == 4 and w['amount_yi'] == pytest.approx(4.0)
    assert w['volume_wan_shares'] == pytest.approx(4 * 100 / 1e4)
    assert w['close_raw'] == pytest.approx(12)             # 末根的名义收
    assert w['turnover_sum'] == pytest.approx(4.0)


def test_跨除权日的高低不会被拉成假影():
    """每根按自己的因子折算后再取极值：周四除权（因子变小）不会把周三的高点算进同一根里放大。"""
    raw = [day('2026-06-30', 100, 110, 95, 105, af=1.0), day('2026-07-01', 60, 62, 58, 61, af=0.55)]
    bars = [P._bar([r]) for r in raw]
    w = P._rollup(bars)
    assert w['high'] == pytest.approx(110.0)
    assert w['low'] == pytest.approx(58 * 0.55, rel=1e-3)   # 低点在除权之后那天，用它自己的因子


# ------------------------------------------------------------------ 分组边界

def test_周与月按自然边界分组不跨周():
    """同一周里连着的五天归一根周线，跨到下周一下月就换组（全用交易日形状：周五在下、周一在下）。"""
    dates = ['2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30',   # 周一~周五
             '2026-11-02', '2026-11-03']                                              # 下周一、下下日，且换月
    bars = [P._bar([day(d, 1, 2, 0.5, 1.5)]) for d in dates]
    weeks = P._grouped(bars, P._iso_week)
    assert [len(g) for g in weeks] == [5, 2], '10-30 是周五收在这根里，11-02 起新周'
    months = P._grouped(bars, P._month)
    assert [len(g) for g in months] == [5, 2]
    assert P._week_start('2026-11-03') == '2026-11-02' and P._month_start('2026-11-03') == '2026-11-01'


def test_月线标签是当月1号周线标签是周一():
    """屏上那根柱子代表"哪一期"必须无歧义：月线给 1 号、周线给周一，都是期间的起点。"""
    assert P._month_start('2026-10-15') == '2026-10-01'
    assert P._week_start('2026-10-15') == '2026-10-12'      # 周四 → 这周周一


# ------------------------------------------------------------------ 真库（不打桩）

def test_真库三块同一只票同一个最新一场():
    d = P.board('600519.SH')
    assert d['available'] is True, d.get('reason')
    # 日线的标签就是交易日；周/月给的是期间起点，所以"同一场"要按期间起点核对
    assert d['as_of'] == d['daily']['rows'][-1]['date']
    assert d['weekly']['rows'][-1]['date'] == P._week_start(d['as_of'])
    assert d['monthly']['rows'][-1]['date'] == P._month_start(d['as_of'])
    for k in ('daily', 'weekly', 'monthly'):
        rows = d[k]['rows']
        assert rows and all(r['close'] and r['high'] >= r['low'] for r in rows), k
        assert len(rows) == d[k]['drawn']
    # 第一期没有基期 ⇒ pct 是 None，不当 0%（取全史那一发才看得到第一期）
    full = P.board('600519.SH', days=9999)
    assert full['daily']['rows'][0]['pct'] is None
    assert d['daily']['rows'][-1]['partial'] is False
    assert d['weekly']['rows'][-1]['partial'] is True and d['monthly']['rows'][-1]['partial'] is True
    assert d['weekly']['rows'][-1]['bars'] <= 5 and d['monthly']['rows'][-1]['bars'] <= 24


def test_真库聚合把每一根日线都用上不多不少():
    """把三块的 `bars` 各自加总，必须等于日线条数 —— 漏一天或多算一天都会在这里露出来。"""
    full = P.board('600519.SH', days=250, weeks=400, months=200)
    n = full['daily']['bars_total']
    assert n > 1000, f'本机只有 {n} 根日线？先查 stock_daily'
    assert sum(r['bars'] for r in full['monthly']['rows']) == n
    assert sum(r['bars'] for r in full['weekly']['rows']) == n
    assert full['monthly']['bars_total'] <= n and full['weekly']['bars_total'] <= n


def test_均线用整段算完再截断最左边也有值():
    d = P.board('600519.SH')
    first = d['monthly']['rows'][0]
    assert first.get('ma3') is not None and first.get('ma6') is not None, \
        '最左边的月线没有均线 ⇒ 均线是截完再算的，会被整段前 N 根空掉'
    assert d['daily']['rows'][0].get('ma20') is not None


def test_序列开头凑不满一整根窗口时不给均线():
    """三根的均值挂上 MA6 的牌子就是假读数：不足 n 根一律 null。"""
    full = P.board('600519.SH', days=9999)
    rows = full['daily']['rows']
    assert rows[0]['ma5'] is None and rows[3]['ma5'] is None
    assert rows[4]['ma5'] is not None
    assert rows[18]['ma20'] is None and rows[19]['ma20'] is not None
    # 手算核对：那一根就是前 5 根复权收的算术平均，不是别的窗口
    import statistics
    assert rows[4]['ma5'] == pytest.approx(statistics.mean(
        r['close'] for r in rows[0:5]), abs=1e-3)


def test_payload干净无NaN无粗体并交齐口径():
    d = P.board('600519.SH')
    s = json.dumps({'data': d}, ensure_ascii=False, allow_nan=False)
    assert 'NaN' not in s and 'NaT' not in s and '**' not in s
    assert d['caliber'] and d['definitions'] and d['caveats']
    assert all(c['code'] and c['text'] for c in d['caveats'])
    assert '前复权' in d['caliber'], '复权口径没写进 caliber，屏幕那句就是页面自己编的'


def test_没有日线的代码老实说而不是交空图():
    d = P.board('999999.SH')
    assert d['available'] is False and d['reason']
    for k in ('daily', 'weekly', 'monthly', 'rows'):
        assert k not in d, '不可用还带着块键，页面会读成"有但是空图"'
