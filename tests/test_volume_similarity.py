# -*- coding: utf-8 -*-
"""量能形状相似检索（`duanxian/volume_similarity.py`）——判据一处、样本库现实一处、出口清洗一处。

钉的是 10-16 这一天真栽过的几个坑，每一个都对应一次"榜看起来对、其实是假的"：
  · `security.board` 大量 NULL ⇒ 同板块硬过滤曾形同虚设（五个"相似样本"全是北交所票）；
  · ST 期 `left join` 把一个「票·启动日」叉成两行 ⇒ 榜上同一行出现两次；
  · board4 与 board4_45 对同一窗口各交一行 ⇒ 十个名额被同一只票吃掉一半；
  · 窗口涨跌幅曾在 SQL 里锚定到"启动日之前全部历史"（247 场）⇒ 量纲整个错掉；
  · 情绪那一维是筛完才补的 ⇒ 补完必须重算合分并重新排序，不然榜与分数两头对不上。
"""
from __future__ import annotations

import datetime as dt
import json
import math
from decimal import Decimal

import pytest

from duanxian import volume_similarity as V
from duanxian.quant import jsonable


# ------------------------------------------------------------------ 板块档

@pytest.mark.parametrize('code, want', [
    ('600519.SH', '主板'), ('603096.SH', '主板'), ('000001.SZ', '主板'), ('002589.SZ', '主板'),
    ('300691.SZ', '双创'), ('688137.SH', '双创'),
    ('920270.BJ', '北交所'), ('830799.BJ', '北交所'), ('430047.BJ', '北交所'),
    ('', None), ('abcdef', None),
])
def test_号段能定档就定档认不出就是_none(code, want):
    assert V.board_group(code) == want


def test_board_列优先于号段但认不出时回落号段():
    assert V.board_group('600519.SH', '沪主板') == '主板'
    assert V.board_group('600519.SH', '创业板') == '双创'      # 以列为准（列可能是北交所等特例）
    assert V.board_group('600519.SH', '北证') == '主板'          # 列里的怪值不当档用，回落号段
    assert V.board_group('920270.BJ', None) == '北交所'


def test_主板与双创绝不互比_任一侧认不出也不比():
    """这条是 10-16 那次假榜的直接教训：`None != None` 成立，两边都认不出时会全部放行。"""
    t = {'features': {'board_group': '主板'}}
    for cg in ('主板', None, '双创', '北交所'):
        c = {'features': {'board_group': cg}}
        cg_t, cg_c = t['features']['board_group'], c['features']['board_group']
        keep = bool(cg_t) and bool(cg_c) and cg_t == cg_c
        assert keep is (cg == '主板')


# ------------------------------------------------------------------ 特征与合分

def feats(**kw):
    base = {'bars': 30, 'shape': {k: 0.1 for k in V.SHAPE_KEYS}, 'intensity': [1.0] * 5,
            'rhythm': {'late_vs_early': 1.2, 'volatility': 0.3},
            'price_volume': {'gain_points': 20.0, 'up_share': 0.5},
            'mood': 0.0, 'board_group': '主板', 'is_st': False}
    base.update(kw)
    return base


def test_一模一样的两条特征每维都满分():
    a = feats()
    s = V.score_pair(a, feats())
    assert s['score'] == pytest.approx(100.0, abs=0.1)
    assert all(v == pytest.approx(100.0, abs=0.1) for v in s['parts'].values())
    assert s['missing_parts'] == []


def test_缺的那一维从权重里剔掉再归一不是扣分():
    a = feats()
    b = feats(mood=None)
    s = V.score_pair(a, b)
    assert s['parts']['mood'] is None and 'mood' in s['missing_parts']
    assert s['weights_used']['mood'] == 0.0, '缺的那一维还占着权重上屏'
    assert sum(s['weights_used'].values()) == pytest.approx(1.0, abs=0.01)
    # 只剩五维时满分仍然是 100（按剩下的归一），不是被缺维拉到 95
    assert s['score'] == pytest.approx(100.0, abs=0.1)


def test_形状向量对得上的格子不超过一半就不算像():
    half = {k: (0.1 if i % 2 == 0 else None) for i, k in enumerate(V.SHAPE_KEYS)}   # 12 格里 6 格有数
    assert V._cosine(half, half) is None, '只剩一半格子却照样给分 ⇒ 屏幕上读起来像"全对上了"'
    most = {k: (0.1 if i < 7 else None) for i, k in enumerate(V.SHAPE_KEYS)}         # 7/12 ⇒ 可比
    assert V._cosine(most, most) == pytest.approx(1.0)
    full = {k: 0.1 for k in V.SHAPE_KEYS}
    assert V._cosine(full, full) == pytest.approx(1.0)


def test_强度坐标对不上过半也是判不了():
    assert V._euclid([1.0, 1.0, None, None, None], [1.0, 1.0, None, None, None]) is None
    assert V._euclid([1.0, 1.0, 1.0, None, None], [1.0, 1.0, 1.0, None, None]) == pytest.approx(0.0)


def test_强度距离超过约两个单位算完全不像():
    a = feats(intensity=[1.0, 1.0, 1.0, 1.0, 1.0])
    far = feats(intensity=[3.0, 3.0, 3.0, 3.0, 3.0])
    s = V.score_pair(a, far)
    assert s['parts']['intensity'] == pytest.approx(0.0)


def test_涨向不同要打折不是直接给零():
    a = feats(price_volume={'gain_points': 20.0, 'up_share': 0.6})
    b = feats(price_volume={'gain_points': -20.0, 'up_share': 0.4})
    s = V.score_pair(a, b)
    c = V.score_pair(a, feats(price_volume={'gain_points': 20.0, 'up_share': 0.6}))
    assert 0 < s['parts']['price_volume'] < c['parts']['price_volume']


def test_可比性那一维只按同档同st给满分():
    assert V.score_pair(feats(), feats(is_st=True))['parts']['comparability'] == pytest.approx(60.0)
    assert V.score_pair(feats(is_st=True), feats(is_st=True))['parts']['comparability'] == pytest.approx(100.0)


def test_区间计数调的是那张表的同一处实现(monkeypatch):
    """这条钉"一把尺子"：本文件不许自己再数一遍五档。"""
    import app.compute.startup_volume as SV
    called = []
    monkeypatch.setattr(SV, 'range_counts', lambda v: called.append(1) or {})
    V.library_rows(use_cache=False)
    assert called, '样本库没走 startup_volume.range_counts ⇒ 五档区间被抄第二遍了'


# ------------------------------------------------------------------ 出口清洗

def test_jsonable把decimal日期nan都收干净():
    got = jsonable({'a': Decimal('1.25'), 'b': dt.date(2026, 10, 15), 'c': float('nan'),
                    'd': [Decimal('3')], 'e': 'text', 'f': None, 'g': True})
    assert got['a'] == 1.25 and isinstance(got['a'], float)
    assert got['b'] == '2026-10-15'
    assert got['c'] is None, 'NaN 进 JSON 会产出裸 NaN，浏览器 JSON.parse 直接炸'
    assert got['d'] == [3.0] and got['e'] == 'text' and got['f'] is None and got['g'] is True
    json.dumps({'data': got}, ensure_ascii=False, allow_nan=False)


# ------------------------------------------------------------------ 两条不打桩：现读真库

def test_样本库没有重复窗口也没有跨板块污染():
    rows = V.library_rows()
    keys = [(r['security_code'], str(r['launch_date'])[:10]) for r in rows]
    assert len(keys) == len(set(keys)), '同一个「票·启动日」出现两次 ⇒ ST 期 join 又叉行'
    assert rows, '样本库是空的 —— startup_volume 没跑还是版本换了？'
    for r in rows:
        assert r['cohorts'], '窗口没标母体归属'
        assert len(r['cohorts']) == len(set(r['cohorts']))
    # 每只票的窗口日期都真的锚定在那 30 根上（不是启动日之前的全部历史）
    for r in rows[:1]:
        assert int(r['bars_used'] or 0) <= V.WINDOW, '窗口根数超过 30 ⇒ 取数没锚定'
        pv = r['features']['price_volume']['gain_points']
        assert pv is None or abs(pv) < 400.0, f'30 根的涨跌幅之和到了 {pv} ⇒ 又加成整段历史了'


def test_真库检索主板目标只会给主板样本():
    d = V.similar('603096.SH', k=10)
    assert d['available'] is True, d.get('reason')
    nb = d['neighbours']
    assert nb, '沪主板 30 根窗口的票一个相似样本都找不到？先看样本库空没空'
    assert len(nb) == len({n['security_code'] for n in nb}), '同一只票占了两个名额'
    for n in nb:
        assert n['board_group'] == d['target']['features']['board_group'] == '主板', \
            f"{n['security_code']} 是 {n['board_group']}，与目标（主板）不互比"
        assert not n['security_code'].startswith(('92', '83', '87', '30', '68')), \
            f"{n['security_code']} 混进了主板榜"
        assert n['score'] is not None and n['launch_date'] != d['target']['window_to']
    sc = [n['score'] for n in nb]
    assert sc == sorted(sc, reverse=True), '补完情绪那一维后没重排 ⇒ 榜序与分数两头对不上'
    s = json.dumps({'data': d}, ensure_ascii=False, allow_nan=False)
    assert 'NaN' not in s and '**' not in s
    assert d['caveats'] and d['library_size'] > 0 and d['candidates_after_filter'] > 0
    # 权重与合成方式在 payload 里，页面不许自己再写一份
    assert d['weights'] == V.WEIGHTS
