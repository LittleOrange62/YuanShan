# -*- coding: utf-8 -*-
"""「走完三条的个股 · 近六个月名单」那一叠的读侧测试（`duanxian/system_entry_cohort.py`）。

判据不在这一块（母体读 `analytics.system_entry`、位次读 `analytics.confluence_daily`），
所以这里钉的全是**最容易写错又不报错**的那几类：

· 月份往回推的月末收小（3 月 31 日 −6 个月是 9 月 30 日，不是 3 月 3 日）；
· 缺数交 `None` 而不是 0（0 会被屏幕念成"平盘"，方向正好反）；
· 累计与单日两把尺不许混（`c1` 与 `day1` 恒等，往后可以分叉）；
· 位次序列的对齐（各数组长度 = 格数、信号日落在格内、越界不补）；
· "交叉榜没跑到那几天"与"这只票没进榜"必须分得开（两种都空，只有一种是坏了）；
· 一张输入表读失败时不许整列空着冒充"没数"；
· 缓存自己交出年龄，且"重新取数"真的绕开它；
· 源清单/列名不硬编码（读 `CF.SOURCES` 与 `SPAN_COLUMNS` 那两份）。

最后几条是**不打桩**的真库用例：拿独立 SQL 复算一只票的第 2/5 场累计涨幅，与接口给的那个数对一遍。
"""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from app.compute import confluence as CF
from duanxian import system_entry_cohort as K


# ---------------------------------------------------------------- 纯函数

def test_月份往回推是自然月且月末收小():
    assert K._start_day(dt.date(2026, 10, 21), 6) == dt.date(2026, 4, 21)
    assert K._start_day(dt.date(2026, 3, 31), 6) == dt.date(2025, 9, 30), '3 月 31 日往回 6 个月是去年 9 月 30 日'
    assert K._start_day(dt.date(2028, 2, 29), 12) == dt.date(2027, 2, 28), '闰日往回一年落到平年 2 月 28'
    assert K._start_day(dt.date(2026, 5, 31), 1) == dt.date(2026, 4, 30)


def test_缺数交None不交0():
    assert K._pct(105, 100) == 5.0
    assert K._pct(None, 100) is None and K._pct(105, None) is None
    assert K._pct(105, 0) is None, '基期 0 没有"涨幅"可言，不许除出 inf 或 0'
    assert K._pct(100, 100) == 0.0, '真的是平盘才给 0'


def test_json_dict读不出形状交None而不是空dict():
    assert K._json_dict('{"a": 1}') == {'a': 1}
    assert K._json_dict(None) is None
    assert K._json_dict('not json') is None, '读坏要交 None，空 dict 会被下游写成"一个榜都没进"'
    assert K._json_dict(b'{"a": 2}') == {'a': 2}


def _sessions_list(n=10):
    return [f'2026-01-{d:02d}' for d in range(1, n + 1)]


def test_后N日是累计而单日是另一把尺():
    sessions = _sessions_list(8)
    # 基期 100 → 第 1 场 90 → 第 2 场 99：累计仍是负的，单日却已经反弹，两把尺方向能相反
    bars = {('X', sessions[0]): 100.0, ('X', sessions[1]): 90.0, ('X', sessions[2]): 99.0,
            ('X', sessions[3]): 99.0, ('X', sessions[4]): None, ('X', sessions[5]): 110.0}
    got = K._forward(bars, sessions, 0, 'X')
    assert got['c1'] == -10.0 and got['day1'] == -10.0, '次日那一格累计与单日恒等'
    assert got['c2'] == -1.0, '第 2 日累计＝相对符合日收盘'
    assert got['day2'] == 10.0, '第 2 日单日＝相对第 1 场'
    assert got['c2'] < 0 < got['day2'], '两把尺可以反向，所以必须都交出（只交一把就会被读成另一把）'
    assert got['c3'] == -1.0 and got['day3'] == 0.0
    assert got['c5'] == 10.0
    assert got['fwd_at'] == [1, 2, 3, 5], '第 4 场那天它没行情 ⇒ 不算进"有行情的那几档"'
    assert got['c10'] is None and got['day10'] is None, '第 10 场在场次表右端之外 ⇒ 空，不拿最后一根顶上'
    assert got['has_base'] is True and got['fwd_sessions'] == 7 and got['fwd_bars'] == 4, \
        '右端能数到第 7 场，其中只有 4 场它有行情（第 4、6、7 场没有行）'


def test_停牌与没有基期都不冒充数字():
    sessions = _sessions_list(8)
    no_base = K._forward({('Y', sessions[1]): 100.0}, sessions, 0, 'Y')
    assert no_base['has_base'] is False
    assert all(no_base[c['cum']] is None for c in K.SPAN_COLUMNS), '没有基期就没有"累计"'
    # 基期 close=0（坏数据）：_bars 那句 SQL 已用 close > 0 挡掉，这里按"没有行"处理
    zero = K._forward({('Z', sessions[0]): None, ('Z', sessions[1]): 10.0}, sessions, 0, 'Z')
    assert zero['c1'] is None and zero['has_base'] is False


def test_位次序列的格数与长度对齐():
    sessions = _sessions_list(8)
    conf = {('X', sessions[2]): {'hits': 2, 'ranks': {'voldbl': 114}, 'sizes': {'voldbl': 150},
                                 'metrics': {'voldbl': {'metric': 6.0}}},
            ('X', sessions[1]): {'hits': 1, 'ranks': {'voldbl': 113}, 'sizes': {'voldbl': 150},
                                 'metrics': {'voldbl': {'metric': 6.0}}}}
    scores = {('X', d): {'value': -4.2, 'rank': 822, 'quadrant': 'Q2', 'stale': d == sessions[1]}
              for d in sessions}
    seq = K._sequence(conf, scores, sessions, 2, 'X')
    assert seq['n'] == 8 and seq['signal_at'] == 2 and seq['i'] == 0
    keys = [s['key'] for s in seq['sources']]
    assert 'quadrant' in keys and 'voldbl' in keys
    assert 'dgt' not in keys, '这一窗一次都没出现的源不该占一行'
    q = next(s for s in seq['sources'] if s['key'] == 'quadrant')
    v = next(s for s in seq['sources'] if s['key'] == 'voldbl')
    assert len(q['tier']) == len(q['rank']) == len(q['value']) == seq['n']
    assert q['tier'][2] == 'Q2' and q['rank'][2] == 822, '象限那行念档位，名次是全市场名次（来自 score）'
    assert v['rank'][:3] == [None, 113, 114] and v['value'][1] == 6.0
    assert seq['hits'][:3] == [None, 1, 2]
    assert seq['in_conf'] == [False, True, True, False, False, False, False, False]


def test_窗口左端不够时序列短一截但不越界():
    sessions = _sessions_list(8)
    seq = K._sequence({}, {}, sessions, 0, 'X')
    assert seq['n'] == 6 and seq['signal_at'] == 0, '前面只有 0 场可看时格子会少，但不许往负里数'
    seq2 = K._sequence({}, {}, sessions, 7, 'X')
    assert seq2['n'] == 3 and seq2['signal_at'] == 2, '右端到了头也只能给到那一场'


def test_check_cohort抓对齐坏():
    sessions = _sessions_list(8)
    good = {'code': 'X', 'date': sessions[2], 'has_base': True, 'c1': 1.0, 'day1': 1.0,
            'c2': None, 'day2': None, 'c3': None, 'day3': None, 'c5': None, 'day5': None,
            'c10': None, 'day10': None, 'c15': None, 'day15': None,
            'fwd_at': [1], 'fwd_sessions': 5, 'fwd_bars': 5,
            'seq': {'i': 0, 'n': 8, 'signal_at': 2, 'hits': [None] * 8, 'in_conf': [True] * 8,
                   'sources': [{'key': 'quadrant', 'label': '四维象限前 200', 'metric': 'oper_value',
                                'tier': ['Q2'] * 8, 'rank': [1] * 8, 'value': [0.5] * 8}]}}
    assert K.check_cohort([good]) == []
    assert K.check_cohort([{**good, 'seq': {**good['seq'], 'n': 3}}]), '数组长度与格数不齐要抓到'
    assert K.check_cohort([{**good, 'seq': {**good['seq'], 'signal_at': 9}}]), '信号日越界要抓到'
    assert K.check_cohort([{**good, 'seq': {**good['seq'], 'n': 0}}]), '一格都没有要抓到'
    assert K.check_cohort([{**good, 'fwd_at': [1, 2]}]), '说有第 2 场行情却没交出 c2 —— 要抓到'
    assert K.check_cohort([{**good, 'has_base': False, 'c1': 1.0}]), '没有基期却有涨幅是自相矛盾'


# ---------------------------------------------------------------- 打桩的 read()

COHORT = [{'code': 'X', 'name': '甲', 'board': '沪主板', 'date': '2026-01-03', 'gap_pct': 2.5,
           'gap_date': '2025-12-30', 'gap_ago': 5, 'gap_unfilled': 3, 'stop_dist_pct': -6.0,
           'close': 10.0, 'pct_change': 3.0, 'limit_up_ago': 9, 'pullback_max_vol_ratio': 0.7,
           'calculated_at': '2026-01-03 17:00:00+08:00'},
          {'code': 'Y', 'name': '乙', 'board': '深主板', 'date': '2026-01-08', 'gap_pct': 1.2,
           'gap_date': '2026-01-05', 'gap_ago': 4, 'gap_unfilled': 3, 'stop_dist_pct': -2.0,
           'close': 8.0, 'pct_change': 1.0, 'limit_up_ago': 3, 'pullback_max_vol_ratio': 0.9,
           'calculated_at': '2026-01-08 17:00:00+08:00'}]
BARS = [{'c': 'X', 'd': '2026-01-0%d' % i, 'hfq': 100.0 + i} for i in range(1, 9)] + \
       [{'c': 'Y', 'd': '2026-01-08', 'hfq': 80.0}, {'c': 'Y', 'd': '2026-01-09', 'hfq': 88.0}]
CONFR = [{'c': 'X', 'd': '2026-01-02', 'hits': 1, 'ranks': '{"quadrant": 9}',
          'sizes': '{"quadrant": 200}', 'metrics': '{"quadrant": {"metric": 1.5}}'},
         {'c': 'X', 'd': '2026-01-03', 'hits': 2, 'ranks': '{"quadrant": 7, "voldbl": 12}',
          'sizes': '{"quadrant": 200, "voldbl": 150}',
          'metrics': '{"quadrant": {"metric": 2.1}, "voldbl": {"metric": 5.0}}'}]
SCORER = [{'c': 'X', 'd': '2026-01-0%d' % i, 'value': 1.0 * i, 'rank_in_market': 500 - i,
           'quadrant': 'Q2', 'is_stale': i == 2} for i in range(1, 9)]


def _install(monkeypatch, *, fail=(), universe=None, conf_days=None):
    """一条假 `pg.fetch_df`：按 SQL 里最特征的串分发（**先认最具体的**，同 test_scores_readside 那条教训）。

    `fail` 里点名哪张输入表本轮"读失败"（返回 None），用来验"坏了不许冒充没数"那几条闸。
    """
    import duanxian.pg as PG

    calls = {'n': 0}
    days = ['2026-01-0%d' % i for i in range(1, 9)]

    def fake(sql, params=()):
        calls['n'] += 1
        s = ' '.join(sql.split())
        if 'max(trade_date)' in s:
            return pd.DataFrame([{'d': days[-1]}])
        if 'close * coalesce(adjustment_factor' in s:
            return None if '行情' in fail else pd.DataFrame(BARS)
        if 'entry_ok' in s and 'from analytics.system_entry s' in s:
            return pd.DataFrame(COHORT)
        if 'from market_data.stock_daily' in s and 'order by d desc' in s:
            # "往前 N 场"：假库里这 8 天之前没有别的场次（真实场景里左端本来就够）
            return pd.DataFrame([])
        if 'distinct trade_date::text d from market_data.stock_daily' in s:
            return None if '场次' in fail else pd.DataFrame([{'d': d} for d in days])
        if 'from analytics.confluence_daily' in s and 'security_code = any(' in s:
            return None if '交叉命中榜' in fail else pd.DataFrame(CONFR)
        if 'from analytics.confluence_daily' in s:
            return None if '交叉榜跑到的天' in fail else \
                pd.DataFrame([{'d': d} for d in (conf_days or days)])
        if "'oper_score'" in s and 'security_code = any(' in s:
            return None if '趋势评分' in fail else pd.DataFrame(SCORER)
        if "'oper_score'" in s and 'count(*)' in s:
            return pd.DataFrame([{'d': d, 'n': (universe or {}).get(d, 5000)} for d in days])
        raise AssertionError(f'假 fetch_df 没准备好的 SQL：{s[:140]}')

    monkeypatch.setattr(PG, 'fetch_df', fake)
    monkeypatch.setattr(PG, 'session_has', lambda t, d: True)
    K.clear_cache()
    return calls


def test_打桩读数交出涨幅位次与四份全局表(monkeypatch):
    _install(monkeypatch)
    d = K.read()
    assert d['available'] is True and d['total'] == 2 and d['codes'] == 2
    assert d['self_check'] == [], d['self_check']
    x = next(r for r in d['rows'] if r['code'] == 'X')
    assert x['date'] == '2026-01-03'
    assert x['c1'] == round((104.0 / 103.0 - 1) * 100, 2), '第 3 格是 104、基期是 103'
    assert x['seq']['signal_at'] == 2 and x['seq']['n'] == 8
    q = next(s for s in x['seq']['sources'] if s['key'] == 'quadrant')
    assert q['tier'][2] == 'Q2' and q['rank'][2] == 497
    assert d['sizes']['voldbl']['2026-01-03'] == 150, '榜长按"一天一榜"交一份全局表，不逐行重复'
    assert d['universe']['2026-01-03'] == 5000 and d['stale_days'] == ['2026-01-02']
    assert d['generated_at'] and d['cached'] is False
    assert json.dumps(d, allow_nan=False, ensure_ascii=False)
    assert d['months'] == K.MONTHS and d['page_size'] == K.PAGE_SIZE


def test_列名与档位口径都只交后端那一份(monkeypatch):
    _install(monkeypatch)
    d = K.read()
    assert [c['label'] for c in d['span_columns']] == ['次日', '第2日', '第3日', '5日', '10日', '15日']
    assert [s['key'] for s in d['sources']] == list(CF.SOURCE_KEYS), '源清单必须照 CF.SOURCES，不在这里增删'
    assert set(d['calibers']) >= {'cohort', 'forward', 'seq', 'tier', 'stale', 'edge'}
    for k, v in d['calibers'].items():
        assert '**' not in v, f'{k} 里有裸星号（悬停不过 plain() 会原样念出来）'
        assert v.strip(), f'{k} 是空句子'
    assert d['seq']['cells'] == d['seq']['back'] + d['seq']['fwd'] + 1


def test_字典上屏念中文标题不念机器键(monkeypatch):
    _install(monkeypatch)
    d = K.read()
    items = d['caliber_items']
    assert [i['key'] for i in items] == list(d['calibers']), '标题清单与句子清单分叉了'
    for i in items:
        assert i['title'] and i['title'] != i['key'], f'{i["key"]} 没配中文标题，屏幕会念机器码'
        assert i['text'] == d['calibers'][i['key']], '两处各存一份句子就会漂'


def test_缓存第二次不重算且refresh真的绕开(monkeypatch):
    calls = _install(monkeypatch)
    K.read()
    n1 = calls['n']
    again = K.read()
    assert calls['n'] == n1, '缓存里就该一次库都不打'
    assert again['cached'] is True and again['cache_age_seconds'] >= 0
    K.read(refresh=True)
    assert calls['n'] > n1, '带 refresh 还说"这是缓存"就是假按钮'


def test_输入表读失败时念读失败而不是没数(monkeypatch):
    for which, code in (('行情', 'cohort_inputs_unread'), ('交叉命中榜', 'cohort_inputs_unread'),
                        ('趋势评分', 'cohort_inputs_unread'), ('场次', 'cohort_sessions_unread')):
        _install(monkeypatch, fail=(which,))
        d = K.read()
        got = [c for c in d['caveats'] if c['code'] == code]
        assert got, f'{which} 读失败时没出声：{[c["code"] for c in d["caveats"]]}'
        assert '读不到' in got[0]['text'] or '没读到' in got[0]['text']
        assert got[0]['label'] and got[0]['label'] != got[0]['code'], '徽标要念中文，不念机器码'
        if code == 'cohort_inputs_unread':
            assert any('整列空着' in c['text'] or '空着' in c['text'] for c in [got[0], *d['caveats']])
            assert d['self_check'] == [], '输入表坏了时不该再报对齐坏（那一轮不算）'


def test_母体为空时说的是这一窗没人走完三条(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(K, '_cohort', lambda a, b: [])
    d = K.read()
    assert d['available'] is True and d['rows'] == []
    codes = [c['code'] for c in d['caveats']]
    assert 'cohort_empty_window' in codes, codes
    assert '没人走完三条' in next(c for c in d['caveats']
                                if c['code'] == 'cohort_empty_window')['text']


def test_那天交叉榜整张没算与这只票没进榜分开念(monkeypatch):
    _install(monkeypatch, conf_days=['2026-01-0%d' % i for i in range(1, 7)])
    d = K.read()
    codes = [c['code'] for c in d['caveats']]
    assert 'cohort_conf_day_missing' in codes, codes
    txt = next(c for c in d['caveats'] if c['code'] == 'cohort_conf_day_missing')['text']
    assert '加工链没跑到' in txt and '不是这些票没进榜' in txt


# ---------------------------------------------------------------- 真库（不打桩）

@pytest.fixture(scope='module')
def payload():
    K.clear_cache()
    d = K.read()
    assert d.get('available'), d.get('reason')
    return d


def test_真库这一批确实有数且形状自洽(payload):
    assert payload['total'] > 200 and payload['codes'] > 100, payload['total']
    assert payload['self_check'] == [], payload['self_check']
    assert payload['window']['sessions'] >= 100
    assert [s['key'] for s in payload['sources']] == list(CF.SOURCE_KEYS)
    assert len(payload['sessions']) == payload['window']['sessions']
    assert payload['universe'] and payload['sizes']
    json.dumps(payload, allow_nan=False, ensure_ascii=False)
    for r in payload['rows']:
        if r['has_base']:
            assert r['c1'] == r['day1'], f'{r["code"]}@{r["date"]} 次日那格两把尺必须恒等'
        n = r['seq']['n']
        assert 0 < n <= K.SEQ_BACK + K.SEQ_FWD + 1
        assert r['seq']['i'] + n <= len(payload['sessions'])
        for s in r['seq']['sources']:
            assert len(s['rank']) == len(s['value']) == n


def test_真库独立复算一只票的第2与第5场累计(payload):
    """不复用同一份内存里的数，另起一句 SQL 按场次表自己走一遍（对不上就是有一边算错）。"""
    from app.db import engine
    cand = [r for r in payload['rows'] if r['c5'] is not None and r['has_base']]
    assert cand, '这一批里连一行都凑不出第 5 场？'
    r = cand[0]
    days = engine.query("""select trade_date::text d from market_data.stock_daily
                            where trade_date >= %s::date group by 1 order by d limit 6""",
                        (r['date'],), role='reader')
    idx = {x['d']: i for i, x in enumerate(days)}
    hfq = {(x['c'], x['d']): x['h'] for x in engine.query(
        """select security_code c, trade_date::text d,
                  (close * coalesce(adjustment_factor, 1))::float h
             from market_data.stock_daily
            where security_code = %s and trade_date = any(%s::date[]) and close > 0""",
        (r['code'], [x['d'] for x in days]), role='reader')}
    base = hfq.get((r['code'], r['date']))
    assert base, f'{r["code"]} 在 {r["date"]} 没有可用的收盘'
    for n in (2, 5):
        day = next((d for d, i in idx.items() if i == n), None)
        if day is None or (r['code'], day) not in hfq:
            continue
        want = round((hfq[(r['code'], day)] / base - 1) * 100, 2)
        got = r['c2'] if n == 2 else r['c5']
        assert got == want, f'{r["code"]}@{r["date"]} 第 {n} 场累计：接口 {got} vs 复算 {want}'


def test_真库的缓存年龄与refresh出口(payload):
    assert payload['generated_at'] and payload['cached'] is False
    again = K.read()
    assert again['cached'] is True and again['cache_age_seconds'] > 0
    fresh = K.read(refresh=True)
    assert fresh['cached'] is False and fresh['total'] == payload['total']
    assert 0 < len(json.dumps(fresh, ensure_ascii=False)) < 3_000_000, 'payload 大到该考虑分页了'
