# -*- coding: utf-8 -*-
"""首板四层过滤深析（`duanxian/first_board_deep.py`）：判据、诚实性与契约。

两条纪律在测试里钉住（都是本项目反复登记过的那一类）：
· **不做合成评分** —— 四层量纲互不相干，交出来的只能是一个个读数，不许出现 `score`/`total_score`；
· **缺数交 null 并说清是哪种缺** —— 源池那一族只有约 28% 有行，那是"源站没给"，
  不是"这只票不强"；补 0 会把"没数据"排成"很差"（`allocator.py` 那条老教训）。
"""
from __future__ import annotations

import datetime as dt

import pytest

from duanxian import first_board_deep as FD

DAY = '2026-10-30'

#: 桩里那只"标准首板"：封单额、首封、炸板、量能倍数都有，改哪一格就在测试里 `{**SAMPLE, 'x': None}`
SAMPLE = {'d': DAY, 'c': '000995.SZ', 'name': '样例股份', 'board': '主板', 'is_st': False,
          'consec': 1, 'pct_change': 9.98, 'turnover_pct': 12.3, 'amount_yuan': 3.0e8,
          'order_amount': 4.6e7, 'float_cap_yuan': 3.0e9, 'float_cap_basis': 'local_snapshot',
          'first_seal': '09:31:02', 'reopen_count': 0, 'at_limit_minutes': 220,
          'reason': '三季报增长+样例概念', 'src_has_row': True, 'missing': None, 'vol_ratio': 1.9}


def _rows_for(**over):
    """按 SQL 里的表名分发桩数据：只认这几张表，认不出来就报错（不静默交空表）。"""
    sample = over.get('sample', [dict(SAMPLE)])
    concept = over.get('concept', [
        {'d': DAY, 'c': '000995.SZ', 'name': '样例概念', 'n': 6},
        {'d': '2026-10-29', 'c': '000995.SZ', 'name': '样例概念', 'n': 3},
    ] + [{'d': f'2026-10-{10 + i:02d}', 'c': '000995.SZ', 'name': '样例概念', 'n': 2 + (i % 3)}
         for i in range(19)])
    breadth = over.get('breadth', [{'d': DAY, 'promotion_rate': 23.0, 'broken_rate': 41.0,
                                    'highest_board': 4, 'up_ratio': 14.0, 'median_pct': -2.6,
                                    'limit_up_count': 39, 'limit_down_count': 53,
                                    'prev_limit_up': 73}])
    dtl = over.get('dtl', [])
    dti = over.get('dti', [])
    prev = over.get('prev', [{'d': '2026-10-29'}])
    premium = over.get('premium', [{'avg_pct': -0.68, 'n': 39}])
    days = over.get('days', [{'d': f'2026-10-{i:02d}'} for i in range(1, 31)])

    def _rows(sql, params=()):
        if 'select max(trade_date)::text as d' in sql:
            return prev
        if 'distinct trade_date::text as d' in sql:
            return days
        if 'from analytics.limit_up_pool_daily p' in sql:
            return sample
        if 'reference_data.sector_member' in sql:
            return concept
        if 'from market_data.dragon_tiger_detail' in sql:
            return dtl
        if 'from market_data.dragon_tiger_institution' in sql:
            return dti
        if 'from analytics.market_breadth' in sql:
            return breadth
        if 'avg(sd.pct_change)' in sql:
            return premium
        raise AssertionError('深析读了一张测试没登记的表：' + sql[:80])
    return _rows


def _stub(monkeypatch, **over):
    monkeypatch.setattr(FD.pg, 'enabled', lambda *a, **k: True)
    monkeypatch.setattr(FD, '_rows', _rows_for(**over))


# ---------------------------------------------------------------- 纯判据

def test_分钟数只用于早晚排序_午休不占格子():
    assert FD._minutes('09:30:00') == 0
    assert FD._minutes('11:30:00') == 120
    assert FD._minutes('13:00:00') == 120, '13:00 与 11:30 之间是午休，不该多出 90 分钟'
    assert FD._minutes('14:00:00') == 180
    assert FD._minutes(None) is None and FD._minutes('nan') is None


def test_常态样本不够时不给倍数_不补零():
    """第一档没有历史就没有"抬升"：给 0 会被读成"这只票的题材在收缩"。"""
    rows = [{'d': DAY, 'c': 'A', 'name': '只有一个样本的概念', 'n': 9}]
    FD._all_days = [DAY]
    v = FD._concept_views(rows, DAY)['只有一个样本的概念']
    assert v['lift'] is None and v['base_median'] is None and v['count'] == 9, v


# ---------------------------------------------------------------- panel 的信封

def test_库不通与表里没行是两句不同的话(monkeypatch):
    monkeypatch.setattr(FD.pg, 'enabled', lambda *a, **k: False)
    d = FD.panel()
    assert d['available'] is False and '库不通' in d['reason']
    assert 'rows' not in d, '不可用时不许带空数组（界面会画出一张假表）'

    monkeypatch.setattr(FD.pg, 'enabled', lambda *a, **k: True)
    monkeypatch.setattr(FD, '_rows', lambda sql, params=(): [])
    d2 = FD.panel()
    assert d2['available'] is False and 'limit_up_pool' in d2['reason'], d2


def test_母体只取封住的首板_并交覆盖率与那条不能当筛子的声明(monkeypatch):
    _stub(monkeypatch)
    d = FD.panel(date=DAY)
    assert d['available'] is True and d['date'] == DAY
    assert 'consec = 1' in FD._SQL_SAMPLE and 'p.sealed' in FD._SQL_SAMPLE, \
        '母体写成别的形状就与「涨停分析」不是同一把尺了'
    assert d['parent']['total'] == 1
    cov = d['coverage']
    assert cov['first_seal']['have'] == 1 and cov['first_seal']['total'] == 1
    assert '不能当筛子' in cov['note'] and '源站' in cov['note'], cov['note']


def test_缺数那一格交null不补零(monkeypatch):
    """北证/ST 池子不给封单额与首封 ⇒ null（界面 —）。写 0 就是"这票封得最少"。"""
    row = _rows_for(sample=[{**SAMPLE, 'order_amount': None, 'first_seal': None,
                             'reopen_count': None, 'vol_ratio': None}])
    monkeypatch.setattr(FD.pg, 'enabled', lambda *a, **k: True)
    monkeypatch.setattr(FD, '_rows', row)
    r = FD.panel(date=DAY)['rows'][0]
    assert r['seal_ratio_pct'] is None and r['first_seal'] is None and r['reopen'] is None
    assert r['vol_mult'] is None, '量能倍数缺（新股/长停牌凑不出上一根量）也不许当 0 倍'


def test_外部阈值与本机分位并排交_并且那两条被本机否掉的写在文字里(monkeypatch):
    _stub(monkeypatch)
    d = FD.panel(date=DAY)
    b = {x['key']: x for x in d['bounds']}
    assert b['seal_ratio']['external'] and '>1%' in b['seal_ratio']['external']
    assert 'p50 0.86%' in b['seal_ratio']['local'], '外部那 1% 在本机是 p70 —— 这句必须说破'
    assert '99.8%' in b['theme_lift']['local'], '「≥2-3 只同步涨停」零区分力那条要留在屏上'
    codes = {c['code'] for c in d['caveats']}
    assert {'no_composite', 'external_thresholds', 'concept_tie_useless', 'seat_detail_missing'} <= codes


def test_不做合成评分_载荷里不许有总分字段(monkeypatch):
    _stub(monkeypatch)
    d = FD.panel(date=DAY)

    def pairs(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k, v
                yield from pairs(v)
        elif isinstance(o, list):
            for v in o:
                yield from pairs(v)

    # 查的是「有没有一个装着合成值的键」：`definitions.no_score` 这种**说明文字**的键不该被打，
    # 而 `score`/`total_score`/`composite` 后面挂一个数才是真把四层加权成一个没有读法的数。
    bad = {k for k, v in pairs(d)
           if isinstance(v, (int, float)) and not isinstance(v, bool)
           and ('score' in k or 'composite' in k)}
    assert not bad, f'冒出 {sorted(bad)} 就是把四层加权成一个没有读法的数'
    assert any('没有合成总分' in c['text'] for c in d['caveats'])


def test_席位明细那一族明说不做_不用合计数冒充(monkeypatch):
    _stub(monkeypatch)
    d = FD.panel(date=DAY)
    what = ' '.join(c['what'] + c['why'] for c in d['cannot'])
    assert '席位' in what and '拉萨' in what, '买一/卖一、知名游资、拉萨天团那一族要说清为什么没有'
    assert '分时' in what and '分钟线' in what, '一波/两波封板要分钟线，本机只覆盖一段'
    assert '驱动硬度' in what or '硬度' in what, '资料里那句"驱动逻辑硬度"本机给不出评级'


def test_龙虎榜与机构那一层只在有的那场给数(monkeypatch):
    _stub(monkeypatch,
          dtl=[{'c': '000995.SZ', 'd': DAY, 'buy_amount': 2.8e8, 'sell_amount': 1.0e8,
                'net_buy': 1.8e8, 'net_buy_ratio': 12.3, 'reason': '日振幅值达15%'}],
          dti=[{'c': '000995.SZ', 'buy_institution_count': 2, 'sell_institution_count': 0,
                'institution_net': 5.7e7}])
    r = FD.panel(date=DAY)['rows'][0]
    assert r['on_dt'] is True and r['dt_buy_sell'] == 2.8
    assert r['dt_net_yi'] == 1.8 and r['inst_net_yi'] == 0.57, r
    # 没上榜的那些票：on_dt=False，比值是 null，不写 0 也不写"资金弱"
    _stub(monkeypatch, dtl=[], dti=[])
    r2 = FD.panel(date=DAY)['rows'][0]
    assert r2['on_dt'] is False and r2['dt_buy_sell'] is None and r2['inst_net_yi'] is None


def test_昨日涨停今日溢价走日线不走涨停池(monkeypatch):
    """池里只有当日涨停的行 ⇒ 拿池算"隔日溢价"必然≈+10%（10-29 实测那个假象）。"""
    _stub(monkeypatch)
    d = FD.panel(date=DAY)
    assert 'avg(sd.pct_change)' in FD._SQL_PREMIUM and 'market_data.stock_daily' in FD._SQL_PREMIUM
    assert d['session']['premium']['avg_pct'] == -0.68
    assert '昨日' in d['session']['premium']['text'] and d['session']['prev_day'] == '2026-10-29'


def test_溢价算不出时说明是还没到不是零(monkeypatch):
    _stub(monkeypatch, premium=[{'avg_pct': None, 'n': 0}])
    d = FD.panel(date=DAY)
    assert d['session']['premium']['avg_pct'] is None
    assert '还没到' in d['session']['premium']['text'], d['session']['premium']['text']


def test_场次卡给的是数与近20场位置_不给正逆向的定性词(monkeypatch):
    _stub(monkeypatch)
    d = FD.panel(date=DAY)
    pr = d['session']['promotion_rate']
    assert pr['current'] == 23.0 and pr['sessions'] == 1
    assert 0 <= pr['percentile_in_20'] <= 100
    flat = str(d['session'])
    for bad in ('正向周期', '逆向周期', '该打板', '不该打板'):
        assert bad not in flat, f'本屏不下这个定性判断：{bad}'


def test_出口清洗不许把NaN带进json(monkeypatch):
    import math
    assert FD._num(float('nan')) is None and FD._num(float('inf')) is None
    import pandas as pd
    assert FD._num(pd.NaT) is None, 'NaT 是 datetime 实例，isna 的闸排在日期判断后面就会漏'
    assert FD._pctl([], 0.5) is None and math.isclose(FD._pctl([1, 2, 3, 4], 0.5), 2.5)


def test_真库跑一趟_载荷自洽且母体与涨停分析同一把尺():
    """不打桩：真库上跑一次，核"覆盖率自洽 + 不做评分 + 外部阈值那两把尺并排"。"""
    if not FD.pg.enabled():
        pytest.fail('库不通：这条真库核对不许兜成跳过')
    d = FD.panel()
    assert d['available'] is True, d.get('reason')
    rows = d['rows']
    assert rows, '真库最近一场连一只首板都没有 ⇒ 先看涨停池那一步跑没跑'
    n = d['parent']['total']
    assert len(rows) == n and d['rows_shown'] == n
    for key, c in d['coverage'].items():
        if isinstance(c, dict):
            assert c['have'] <= c['total'] == n, (key, c)
    assert '封住' in d['parent']['text'] and 'consec=1' in d['parent']['text']
    assert len(d['bounds']) >= 8 and len(d['cannot']) >= 3
    assert 'total_score' not in str(d).lower()
    lifts = [r['concept_lift'] for r in rows if r['concept_lift'] is not None]
    assert len(lifts) >= max(1, int(0.8 * n)), f'概念抬升判得了的比例太低：{len(lifts)}/{n}'
    assert d['generated_at'][:4].isdigit()
    assert not dt.datetime.fromisoformat(d['generated_at']).tzinfo is None
