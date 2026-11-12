# -*- coding: utf-8 -*-
"""「近期倍量表走势分析」（`duanxian/energy_trend.py` + 端点 `/api/quant/energy-trend`）。

用户 2026-11-08 推翻上一版逐票热力表后的原话拆成四件事，四件都要钉：

1. **名次每 10 名一档**（1-10 / 11-20 / …），边界同分并列一起进同一档；名次取不到数的事件
   不占档、不当最后一档。
2. 看的是**名次日之后第 k 场**的累计涨跌幅（后复权、按市场场次），不是各票各铺各的日历。
3. **加权**按名次日那天的加权分：`Σ(涨幅×分)/Σ(分)`，等权并排；分母为 0 交 None，
   **不悄悄退回等权**（两列长得一样却说是加权，屏幕上看不出来）。
4. 三种「没有数」分开：`pending`（还没走到）、`missing`（走到了读不到）、`ok`。
   任何一类都不许折成 0%。

外加两条真库不打桩的：档位表与一份纯 Python 的独立复算逐位对上；SQL 里名次窗口不许再
被母体筛参污染（上一版就是这么把「首次进榜日」算假的）。
"""
import re
from pathlib import Path

import pytest

from duanxian import energy_trend as et
from duanxian import pg, ttlcache

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ 档位

def test_名次每十名一档且边界归前一档():
    assert [et.bucket_of(r) for r in (1, 10, 11, 20, 21, 120, 121)] == [0, 0, 1, 1, 2, 11, 12]
    assert et.bucket_label(0) == '1-10'
    assert et.bucket_label(1) == '11-20'
    assert et.bucket_label(11) == '111-120'
    assert et.bucket_of(7, size=5) == 1 and et.bucket_label(1, size=5) == '6-10'


def test_名次取不到数不占档也不当最后一名():
    for bad in (None, float('nan'), 0, -3, 'x'):
        assert et.bucket_of(bad) is None, bad
    assert et.bucket_of(5, size=0) is None, '档宽为 0 不许除零'


def test_并列挤过名次上限时那一档照样交出来():
    """N 是上限不是恰好 N 只：第 11 名并列 4 只 ⇒ 11-20 这一档有 4 笔，不是被切掉。"""
    events = [{'code': f'{i}.SH', 'rank': r, 'weight': 1.0,
               'rets': {1: {'ret': 2.0, 'state': et.STATE_OK}}}
              for i, r in enumerate([1, 2, 10, 11, 11, 11, 11])]
    out = et.agg_buckets(events, horizons=(1,))
    assert [b['label'] for b in out] == ['1-10', '11-20']
    assert [b['events'] for b in out] == [3, 4]
    assert out[1]['codes'] == 4, '独立票数按 code 去重，不是笔数'


def test_名次为空的事件整笔不进档():
    events = [{'code': 'A.SH', 'rank': None, 'weight': 1.0, 'rets': {}},
              {'code': 'B.SH', 'rank': 3, 'weight': 1.0, 'rets': {}}]
    out = et.agg_buckets(events, horizons=(1,))
    assert len(out) == 1 and out[0]['events'] == 1 and out[0]['label'] == '1-10'


# ------------------------------------------------------------------ 三态

def test_缺基期优先判读不到而不是还没走到():
    """没有基期就永远算不出；说成「等几天就有数」是另一句假话。"""
    assert et.classify(None, 10.0, 99, 10) == et.STATE_MISSING
    assert et.classify(0.0, 10.0, 99, 10) == et.STATE_MISSING
    assert et.classify(10.0, None, 99, 10) == et.STATE_PENDING
    assert et.classify(10.0, None, 5, 10) == et.STATE_MISSING
    assert et.classify(10.0, 11.0, 5, 10) == et.STATE_OK
    assert et.classify(float('nan'), 11.0, 5, 10) == et.STATE_MISSING


# ------------------------------------------------------------------ 一格的读数

def test_还没走到与读不到都不进分子分母():
    samples = [{'ret': 4.0, 'weight': 2.0, 'state': et.STATE_OK},
               {'ret': None, 'weight': 9.0, 'state': et.STATE_PENDING},
               {'ret': None, 'weight': 9.0, 'state': et.STATE_MISSING}]
    c = et.cell_stats(samples)
    assert (c['n_all'], c['n_ok'], c['n_pending'], c['n_missing']) == (3, 1, 1, 1)
    assert c['equal'] == 4.0 and c['weighted'] == 4.0
    assert c['n_w'] == 1 and c['weight_sum'] == 2.0, '缺数那两笔的权重不许进分母'
    assert c['win_pct'] == 100.0


def test_加权与等权是两把尺不许混():
    samples = [{'ret': 10.0, 'weight': 3.0, 'state': et.STATE_OK},
               {'ret': -2.0, 'weight': 1.0, 'state': et.STATE_OK}]
    c = et.cell_stats(samples)
    assert c['equal'] == 4.0
    assert c['weighted'] == round((10.0 * 3 + -2.0 * 1) / 4, 4) == 7.0
    assert c['win_pct'] == 50.0 and (c['up'], c['down'], c['flat']) == (1, 1, 0)


def test_加权分母为零交空而不是退回等权():
    """这一条是屏幕上唯一看不出来的假话：两列一样却写着「加权」。"""
    for w in (None, 0.0, -1.0, float('nan')):
        c = et.cell_stats([{'ret': 5.0, 'weight': w, 'state': et.STATE_OK}])
        assert c['equal'] == 5.0
        assert c['weighted'] is None, w
        assert c['n_w'] == 0 and c['weight_sum'] is None, w


def test_真零是读数不是缺数():
    c = et.cell_stats([{'ret': 0.0, 'weight': 1.0, 'state': et.STATE_OK},
                       {'ret': 0.0, 'weight': 1.0, 'state': et.STATE_OK}])
    assert c['equal'] == 0.0 and c['weighted'] == 0.0
    assert c['win_pct'] == 0.0, '平盘不算上涨'
    assert (c['up'], c['down'], c['flat']) == (0, 0, 2), '平盘进分母、不进上涨也不进下跌'


def test_一格全缺时读数全空计数照实():
    c = et.cell_stats([{'ret': None, 'weight': None, 'state': et.STATE_PENDING}] * 3)
    assert c['n_all'] == 3 and c['n_ok'] == 0 and c['n_pending'] == 3
    for k in ('weighted', 'equal', 'win_pct', 'weight_sum'):
        assert c[k] is None, k
    empty = et.cell_stats([])
    assert empty['n_all'] == 0 and empty['equal'] is None


def test_权重必须跟着每一笔进聚合():
    """回归钉：`agg_buckets` 早先只把 `rets[k]` 传进 `cell_stats` ⇒ 加权列 60 格全 None。"""
    events = [{'code': 'A.SH', 'rank': 1, 'weight': 3.0,
               'rets': {1: {'ret': 10.0, 'state': et.STATE_OK}}},
              {'code': 'B.SH', 'rank': 2, 'weight': 1.0,
               'rets': {1: {'ret': -2.0, 'state': et.STATE_OK}}}]
    c = et.agg_buckets(events, horizons=(1,))[0]['cells'][0]
    assert c['n_w'] == 2 and c['weighted'] == 7.0 and c['equal'] == 4.0


# ------------------------------------------------------------------ 超额与增量

def test_超额两把尺减的是同一个等权基准():
    """基准自己没有「加权」那一份；拿 baseline.weighted 去减会整列交 None（实测过）。"""
    cells = [{'k': 1, 'weighted': 3.0, 'equal': 2.0}]
    base = [{'k': 1, 'weighted': None, 'equal': 0.5}]
    et.attach_derived(cells, base, horizons=(1,))
    assert cells[0]['excess_weighted'] == 2.5
    assert cells[0]['excess_equal'] == 1.5


def test_基准算不出时超额交空不交零():
    cells = [{'k': 1, 'weighted': 3.0, 'equal': 2.0}]
    et.attach_derived(cells, [{'k': 1, 'equal': None}], horizons=(1,))
    assert cells[0]['excess_weighted'] is None and cells[0]['excess_equal'] is None
    own = [{'k': 1, 'weighted': None, 'equal': 2.0}]
    et.attach_derived(own, [{'k': 1, 'equal': 0.5}], horizons=(1,))
    assert own[0]['excess_weighted'] is None and own[0]['excess_equal'] == 1.5


def test_每场增量除的是真实间隔():
    """3→5 除 2；中间那列算不出时 2→5 除 3。恒除 1 会把趋势念成两倍陡。"""
    cells = [{'k': 1, 'weighted': 1.0, 'equal': 1.0},
             {'k': 2, 'weighted': 3.0, 'equal': 3.0},
             {'k': 3, 'weighted': None, 'equal': None},
             {'k': 5, 'weighted': 9.0, 'equal': 9.0},
             {'k': 10, 'weighted': 14.0, 'equal': 14.0}]
    et.attach_derived(cells, None, horizons=(1, 2, 3, 5, 10))
    assert [c['step_weighted'] for c in cells] == [1.0, 2.0, None, 2.0, 1.0]
    assert cells[0]['step_equal'] == 1.0, '起点＝名次日当天 0 场 0%'
    assert cells[3]['step_equal'] == 2.0, '(9−3)/(5−2)＝2，不是 (9−3)/1'


def test_增量两把尺各记各的上一列():
    cells = [{'k': 1, 'weighted': None, 'equal': 2.0},
             {'k': 2, 'weighted': 6.0, 'equal': 4.0}]
    et.attach_derived(cells, None, horizons=(1, 2))
    assert cells[1]['step_weighted'] == 3.0, '加权那把尺上一列是空的 ⇒ 从 0 场 0% 起算除 2'
    assert cells[1]['step_equal'] == 2.0


# ------------------------------------------------------------------ 事件矩阵 → 档位表＋基准

def _px(code, day_sr, price):
    return {'code': code, 'd': f'd{day_sr}', 'hfq': price}


def test_右端还没走到的那些笔判pending而不是零():
    sr_of = {'d1': 1, 'd2': 2, 'd3': 3}
    events = [{'code': 'A.SH', 'd': 'd2', 'rk': 1, 'weight': 2.0}]
    prices = [_px('A.SH', 1, 10.0), _px('A.SH', 2, 10.0), _px('A.SH', 3, 11.0)]
    buckets, extra = et._build(events, prices, sr_of, last_sr=3, window_srs=[1, 2],
                               horizons=(1, 2), size=10)
    cells = {c['k']: c for c in buckets[0]['cells']}
    assert cells[1]['equal'] == 10.0 and cells[1]['n_ok'] == 1
    assert cells[2]['n_pending'] == 1 and cells[2]['n_ok'] == 0
    assert cells[2]['equal'] is None and cells[2]['weighted'] is None
    assert extra['dropped_events'] == 0


def test_名次日没有行整笔丢掉并计数():
    sr_of = {'d1': 1, 'd2': 2}
    events = [{'code': 'A.SH', 'd': 'd1', 'rk': 1, 'weight': 1.0},
              {'code': 'Z.SH', 'd': 'd1', 'rk': 2, 'weight': 1.0}]
    prices = [_px('A.SH', 1, 10.0), _px('A.SH', 2, 12.0)]
    buckets, extra = et._build(events, prices, sr_of, last_sr=2, window_srs=[1],
                               horizons=(1,), size=10)
    assert extra['dropped_events'] == 1, '价格里找不到这只票 ⇒ 整笔不参与，不拿 0 顶'
    assert buckets[0]['events'] == 1 and buckets[0]['cells'][0]['equal'] == 20.0


def test_第k场它没有行判missing():
    sr_of = {'d1': 1, 'd2': 2, 'd3': 3}
    events = [{'code': 'A.SH', 'd': 'd1', 'rk': 1, 'weight': 1.0}]
    prices = [_px('A.SH', 1, 10.0), _px('A.SH', 3, 12.0)]
    buckets, _ = et._build(events, prices, sr_of, last_sr=3, window_srs=[1],
                           horizons=(1, 2), size=10)
    cells = {c['k']: c for c in buckets[0]['cells']}
    assert cells[1]['n_missing'] == 1 and cells[1]['equal'] is None
    assert cells[2]['equal'] == 20.0, '第 2 场按市场场次找，不拿它自己上一根 K 线顶'


def test_基准分母只算名次日真有收盘的票日():
    """把「票 × 场」全笛卡尔积当分母，n_missing 会被当天压根没上市的格子灌成虚高的数。"""
    sr_of = {'d1': 1, 'd2': 2}
    prices = [_px('A.SH', 1, 10.0), _px('A.SH', 2, 11.0),
              _px('B.SH', 2, 20.0)]
    _, extra = et._build([], prices, sr_of, last_sr=2, window_srs=[1, 2],
                         horizons=(1,), size=10)
    b = extra['baseline']
    assert b['stock_days'] == 4 and b['no_base'] == 1, '2 只 × 2 场里只有 3 格真有价'
    c = b['cells'][0]
    assert c['n_all'] == 3 and c['n_ok'] == 1 and c['n_pending'] == 2
    assert c['n_missing'] == 0
    assert c['equal'] == 10.0
    assert c['weighted'] is None, '全市场基准只有等权那一份'
    assert b['codes'] == 2


# ------------------------------------------------------------------ 出口与缓存

@pytest.fixture
def stubbed(monkeypatch):
    from app.compute import volume_energy as venc

    cap = {'calls': []}
    monkeypatch.setattr(venc, 'fingerprint', lambda: 'ev-test000000')
    monkeypatch.setattr(venc, 'scale', lambda: {'max_score': 40.0})

    def _compute(date, window, top_n, fp):
        cap['calls'].append((date, window, top_n, fp))
        return {'available': True, 'date': date or '2026-11-06', 'top_n': top_n,
                'window': window, 'window_used': window, 'window_from': '2026-05-01',
                'window_to': date or '2026-11-06', 'fingerprint': fp,
                'buckets': [{'index': 0, 'label': '1-10', 'rank_lo': 1, 'rank_hi': 10,
                             'events': window * 10, 'codes': 30, 'cells': [
                                 {'k': k, 'n_all': window * 10, 'n_ok': window * 10,
                                  'n_pending': 0, 'n_missing': 0, 'n_w': window * 10,
                                  'weight_sum': 100.0, 'weighted': -1.0 * k,
                                  'equal': -0.5 * k, 'win_pct': 40.0,
                                  'excess_weighted': 0.0, 'excess_equal': 0.0,
                                  'step_weighted': -1.0, 'step_equal': -0.5,
                                  'up': 4, 'down': 6, 'flat': 0} for k in et.HORIZONS]}],
                'baseline': {'label': '同窗全市场等权', 'events': 100, 'codes': 50,
                             'stock_days': 120, 'no_base': 20, 'cells': []},
                'dropped_events': 0,
                'coverage': {'lo': '2025-12-02', 'hi': '2026-11-06', 'sessions': 225}}

    monkeypatch.setattr(et, '_compute', _compute)
    ttlcache.clear('energy_trend:')
    yield cap
    ttlcache.clear('energy_trend:')


def test_参数夹在本模块上下界里且默认只有一处(stubbed):
    out = et.board()
    assert stubbed['calls'][-1][1:3] == (et.DEFAULT_WINDOW, 120)
    assert out['window_default'] == et.DEFAULT_WINDOW
    assert out['top_n_default'] == 120
    assert (out['window_min'], out['window_max']) == (et.MIN_WINDOW, et.MAX_WINDOW)
    assert (out['top_n_min'], out['top_n_max']) == (et.MIN_TOP_N, et.MAX_TOP_N)
    et.board(window=1, top_n=99999)
    assert stubbed['calls'][-1][1:3] == (et.MIN_WINDOW, et.MAX_TOP_N)
    et.board(window=99999, top_n=1)
    assert stubbed['calls'][-1][1:3] == (et.MAX_WINDOW, et.MIN_TOP_N)


def test_缓存键带口径版本与档表指纹(stubbed):
    out = et.board(window=30, top_n=60)
    key = out['cache_key']
    assert et.SPEC_VERSION in key and 'ev-test000000' in key and ':30:60:' in key
    assert out['cache_ttl'] == et.TTL
    assert out['generated_at'], '缓存要自报生成时刻，不许留一个恒 None 的年龄占位'
    assert out['cached'] is False and out['cache_age_seconds'] is not None
    again = et.board(window=30, top_n=60)
    assert again['cached'] is True, '同一组参数第二发应当命中（前后 status.at 不变）'
    assert again['generated_at'] == out['generated_at']
    assert len(stubbed['calls']) == 1, '命中就不该再算一遍'


def test_重取只清这一组参数的键(stubbed):
    et.board(window=30, top_n=60)
    et.board(window=40, top_n=60)
    assert len(stubbed['calls']) == 2
    et.board(window=30, top_n=60, refresh=True)
    assert len(stubbed['calls']) == 3, 'refresh 要真的重算'
    et.board(window=40, top_n=60)
    assert len(stubbed['calls']) == 3, '别的参数组那一键不许被一锅端'


def test_口径与列名只从后端描述符交出(stubbed):
    out = et.board()
    assert out['spec_version'] == et.SPEC_VERSION
    assert out['bucket_size'] == et.BUCKET_SIZE == 10
    assert out['horizons'] == list(et.HORIZONS)
    assert out['weight_rule'] == et.WEIGHT_RULE
    assert out['baseline_rule'] == et.BASELINE_RULE
    assert out['sample_rule'] == et.SAMPLE_RULE
    assert out['pct_rule'] == et.PCT_RULE
    assert out['guide'] == list(et.GUIDE) and len(et.GUIDE) >= 6
    assert [c['key'] for c in out['columns']] == [c['key'] for c in et.COLUMNS]
    assert out['rank']['by'] == 'dbl_energy'
    assert out['rank']['rule'], '名次规则原文走 ranking 那一份'


def test_声明一律带中文标签且当场计数那几条不进折叠白名单(stubbed):
    out = et.board()
    for c in out['caveats']:
        assert c.get('label') and re.search(r'[\u4e00-\u9fff]', c['label']), c
    codes = {c['code'] for c in out['caveats']}
    assert 'energy_trend_overlap' in codes and 'energy_trend_not_backtest' in codes
    kind = (ROOT / 'frontend/src/lib/caveatKind.ts').read_text(encoding='utf-8')
    for code in ('energy_trend_short_window', 'energy_trend_pending', 'energy_trend_missing',
                 'energy_trend_dropped', 'energy_trend_ties', 'energy_trend_over_top',
                 'energy_trend_unavailable'):
        assert f"'{code}'" not in kind, f'{code} 带着当场计数，折起来就是把「这几格不能横比」藏掉'
    for code in ('energy_trend_overlap', 'energy_trend_not_backtest'):
        assert f"'{code}'" in kind, f'{code} 是固定散文，应当可折'


def test_不可用时只交原因不交空档位表(monkeypatch):
    from app.compute import volume_energy as venc

    monkeypatch.setattr(venc, 'fingerprint', lambda: 'ev-test000000')
    monkeypatch.setattr(venc, 'scale', lambda: {})
    monkeypatch.setattr(et, '_compute',
                        lambda *a: {'available': False, 'reason': '这一段压根没数'})
    ttlcache.clear('energy_trend:')
    out = et.board()
    assert out['available'] is False and out['reason'] == '这一段压根没数'
    assert 'buckets' not in out
    assert [c['code'] for c in out['caveats']] == ['energy_trend_unavailable']
    ttlcache.clear('energy_trend:')


# ------------------------------------------------------------------ 端点上下夹＝模块常量

def test_端点上下夹与本模块常量同源():
    """界面 7 / 接口收 0.07 那一族：上下夹写两份，改一处另一处静默 422。"""
    src = (ROOT / 'vr/app.py').read_text(encoding='utf-8')
    seg = src[src.index('@app.get("/api/quant/energy-trend")'):]
    seg = seg[:seg.index('\n@app.')]
    assert f'ge={et.MIN_WINDOW}, le={et.MAX_WINDOW}' in seg, seg[:900]
    assert f'ge={et.MIN_TOP_N}, le={et.MAX_TOP_N}' in seg
    assert 'energy_trend.board(date, window=window, top_n=top_n, refresh=refresh)' in seg
    assert 'refresh: bool = Query(' in seg


def test_名次窗口不许被母体筛参污染():
    """上一版的真缺陷：`security_code = any(...)` 放进 rank() 的窗口里面 ⇒ 名次只在那批票内部排。"""
    sql = et._EV_SQL
    assert 'partition by trade_date' in sql
    inner = sql[sql.index('with r as ('):sql.index('select code, d, weight, rk from r')]
    assert 'security_code = any' not in inner and 'security_code in' not in inner
    assert 'where rk <= %s' in sql, '名次线要筛在窗口之后'
    assert 'energy_fp = %s' in sql and 'dbl_energy is not null' in sql
    assert 'nulls last' in sql, '没有分的行不许折成 0 分挤进名次'
    assert 'adjustment_factor' in et._PX_SQL, '涨跌幅一律后复权'
    assert 'pct_change' not in et._PX_SQL, '不读日线里那列未复权的涨跌幅'


# ------------------------------------------------------------------ 真库（不打桩）

def test_真库档位表与独立复算逐位一致():
    """用一份纯 Python 的直白循环重算同一批事件，与矩阵化那条路径逐位对上。

    取数 SQL 共用（它的正确性由 `.local/_et_redo_probe*` 那两份整段 SQL 探针对过），
    这里验的是「事件 → 三态 → 分档 → 加权/等权/占比」这一段。窗口取最小档，跑得快。
    """
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    from app.compute import volume_energy as venc

    w, n = et.MIN_WINDOW, 30
    out = et.board(window=w, top_n=n)
    if not out.get('available'):
        pytest.skip('本机库没有倍量场')
    d, fp = out['date'], out['fingerprint']
    assert fp == venc.fingerprint()
    cal = et._sessions(d, et.MAX_WINDOW + max(et.HORIZONS) + 2)
    sr_of = {str(r['d']): int(r['sr']) for r in cal}
    d_of = {int(r['sr']): str(r['d']) for r in cal}
    dates = [str(r['d']) for r in cal]
    win = dates[-w:] if len(dates) >= w else dates
    last_sr = sr_of[d]
    ev = et._events(fp, win[0], d, n)
    px = et._prices(win[0], d)
    assert ev and px

    by_code: dict[str, dict[str, float]] = {}
    for r in px:
        v = r.get('hfq')
        if v is not None and v == v:
            by_code.setdefault(str(r['code']), {})[str(r['d'])] = float(v)

    # 直白循环：一笔一笔判三态、算涨跌，再按 (档, k) 聚合
    tally: dict[tuple[int, int], dict] = {}
    for e in ev:
        rk = e.get('rk')
        if rk is None or rk != rk or int(rk) < 1:
            continue
        b = (int(rk) - 1) // et.BUCKET_SIZE
        code, day = str(e['code']), str(e['d'])
        sr = sr_of.get(day)
        book = by_code.get(code)
        if sr is None or book is None:
            continue
        base = book.get(day)
        weight = e.get('weight')
        for k in et.HORIZONS:
            t = tally.setdefault((b, k), {'all': 0, 'ok': [], 'w': [], 'pending': 0, 'missing': 0})
            t['all'] += 1
            tgt = sr + k
            if base is None or not base:
                t['missing'] += 1
                continue
            if tgt > last_sr:
                t['pending'] += 1
                continue
            later = book.get(d_of.get(tgt, ''))
            if later is None:
                t['missing'] += 1
                continue
            ret = round((later / base - 1) * 100.0, 2)
            t['ok'].append(ret)
            if weight is not None and weight == weight and float(weight) > 0:
                t['w'].append((ret, float(weight)))

    checked = 0
    for bucket in out['buckets']:
        for c in bucket['cells']:
            t = tally.get((bucket['index'], c['k']))
            assert t is not None, (bucket['label'], c['k'])
            rets, pairs = t['ok'], t['w']
            assert c['n_all'] == t['all'], (bucket['label'], c['k'], 'n_all')
            assert c['n_ok'] == len(rets)
            assert c['n_pending'] == t['pending'] and c['n_missing'] == t['missing']
            eq = round(sum(rets) / len(rets), 4) if rets else None
            ws = sum(x[1] for x in pairs)
            wt = round(sum(r * x for r, x in pairs) / ws, 4) if ws > 0 else None
            win_pct = round(sum(1 for r in rets if r > 0) * 100.0 / len(rets), 2) if rets else None
            assert c['equal'] == eq, (bucket['label'], c['k'], 'equal', c['equal'], eq)
            assert c['weighted'] == wt, (bucket['label'], c['k'], 'weighted', c['weighted'], wt)
            assert c['win_pct'] == win_pct
            assert c['n_w'] == len(pairs)
            checked += 1
    assert checked == len(out['buckets']) * len(et.HORIZONS) and checked > 0
    # 派生两列也按同一份基准复核一遍（超额减的是等权基准，不是它自己那份恒 None 的加权）
    base_eq = {c['k']: c['equal'] for c in out['baseline']['cells']}
    for bucket in out['buckets']:
        for c in bucket['cells']:
            for key in ('weighted', 'equal'):
                v, bv = c[key], base_eq.get(c['k'])
                want = None if v is None or bv is None else round(v - bv, 4)
                assert c['excess_' + key] == want, (bucket['label'], c['k'], key)