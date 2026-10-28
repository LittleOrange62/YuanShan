# -*- coding: utf-8 -*-
"""
`EXPLAIN`（「评分归因」卡上「怎么算的」的数据源）必须和脚本实现一致。

这套说明唯一的风险是**烂掉**：有人改了公式或权重、没改说明，页面上从此
理直气壮地写着错的算法 —— 那比没有说明更坏。所以这里拿脚本源码反查：

1. 脚本里每一个 `ctx.cs('…')` 表达式都必须出现在某个 term 的 `expr` 里
   （加了新公式忘了写说明 → 红）；
2. 内部权重要么等于模块里的 `WEIGHTS` / `DEFAULTS` 常量，要么等于源码里
   `* 0.xx` 这些字面量的集合（改了权重忘了改说明 → 红）；
3. 每个在 `fct.score_detail` 里真的出现过的 script_key 都得有 EXPLAIN
   （加了新分项忘了写说明 → 红）。
"""
from __future__ import annotations

import importlib
import pathlib
import re

import pytest

from app.compute import explain as ex_mod
from app.db import engine

BUILTIN = pathlib.Path('app/compute/builtin')

# 参与评分归因的脚本模块（透传项也要有说明，它们在页面上也是一行）
MODULES = (
    'quality_xingtai', 'quality_qushi', 'quality_weizhi', 'quality_chouma',
    'quality_liangneng', 'sector_zhangfu', 'sector_guangdu',
    'sector_dongliang', 'sector_chengjiao', 'market_mood', 'temp_score',
    'vol_phase', 'vol_stage', 'env_mood', 'env_sector', 'oper_x', 'oper_y',
)


def _mod(name: str):
    return importlib.import_module('app.compute.builtin.' + name)


def _src(name: str) -> str:
    return (BUILTIN / f'{name}.py').read_text(encoding='utf-8')


def _nows(s: str) -> str:
    return re.sub(r'\s+', '', s)


@pytest.mark.parametrize('name', MODULES)
def test_module_has_explain(name):
    ex = getattr(_mod(name), 'EXPLAIN', None)
    assert isinstance(ex, dict), f'{name} 没有 EXPLAIN'
    assert ex.get('basis'), f'{name}.EXPLAIN 缺 basis'
    assert ex.get('terms'), f'{name}.EXPLAIN 缺 terms'
    for t in ex['terms']:
        assert t.get('name'), f'{name} 有 term 没写 name'
        assert t.get('expr'), f"{name}.{t.get('name')} 没写 expr"


@pytest.mark.parametrize('name', MODULES)
def test_every_formula_in_source_is_documented(name):
    """脚本里 ctx.cs('…') 的每个表达式都要在 EXPLAIN 里出现过。"""
    src = _src(name)
    doc = _nows(' '.join(t['expr'] for t in _mod(name).EXPLAIN['terms']))
    for expr in re.findall(r"ctx\.cs\(\s*'([^']+)'", src):
        assert _nows(expr) in doc, f'{name} 里的公式 {expr!r} 没写进 EXPLAIN'


@pytest.mark.parametrize('name', MODULES)
def test_inner_weights_match_the_implementation(name):
    """
    EXPLAIN 里的内部权重必须能在实现里找到依据。

    两种写法：模块常量（WEIGHTS / DEFAULTS）或 `分数 * 0.35` 这样的字面量。
    """
    mod = _mod(name)
    ws = [t['w'] for t in mod.EXPLAIN['terms'] if t.get('w') is not None]
    if not ws:
        pytest.skip(f'{name} 是离散判别，没有内部权重')
    if all(w == 1.0 for w in ws):
        return                      # 透传项，权重恒为 1
    const = getattr(mod, 'WEIGHTS', None) or getattr(mod, 'DEFAULTS', None)
    if isinstance(const, dict):
        vals = [float(v) for v in const.values() if 0 < float(v) <= 1]
        for w in ws:
            assert float(w) in vals, f'{name} 的权重 {w} 不在模块常量里'
        return
    lit = sorted(float(x) for x in re.findall(r'\*\s*(0\.\d+)', _src(name)))
    assert sorted(float(w) for w in ws) == lit, (
        f'{name}：EXPLAIN 权重 {sorted(ws)} 与源码里的 {lit} 不一致')


@pytest.mark.parametrize('name', MODULES)
def test_weights_sum_to_one(name):
    ws = [float(t['w']) for t in _mod(name).EXPLAIN['terms']
          if t.get('w') is not None]
    if not ws:
        pytest.skip(f'{name} 是离散判别，没有内部权重')
    assert abs(sum(ws) - 1.0) < 1e-6, f'{name} 内部权重之和是 {sum(ws)}，不是 1'


def test_explain_map_covers_every_scored_part():
    """库里真的产出过明细的 script_key，页面上都必须能点开说明。"""
    keys = [r['script_key'] for r in engine.query(
        'SELECT DISTINCT script_key FROM fct.score_detail')]
    assert keys, 'fct.score_detail 是空的，这条测试失去意义'
    got = ex_mod.explain_map(keys)
    missing = sorted(set(keys) - set(got))
    assert not missing, f'这些分项没有 EXPLAIN：{missing}'


def test_explain_of_rejects_paths_outside_builtin():
    """path 是页面上可编辑的字段，不能变成任意模块导入。"""
    assert ex_mod.explain_of('../../os.py') is None
    assert ex_mod.explain_of('app/compute/scoring.py') is None


def test_binding_map_keys_are_cap_and_script():
    bm = ex_mod.binding_map()
    assert bm, 'config.v_effective_binding 是空的'
    for k, v in bm.items():
        assert isinstance(k, tuple) and len(k) == 2
        assert 'weight' in v and 'params' in v
    assert ('stock_quality', 'xingtai_v1') in bm


# ---------------------------------------------------------------- 页面渲染


def _walk(node):
    yield node
    ch = getattr(node, 'children', None)
    if isinstance(ch, (list, tuple)):
        for c in ch:
            yield from _walk(c)
    elif ch is not None:
        yield from _walk(ch)


def _texts(root) -> str:
    return '\n'.join(n for n in _walk(root) if isinstance(n, str))


def test_panel_gives_every_part_a_formula():
    """
    「评分归因」这张卡原来只有"分项名 + 数字 + 条形"，看不出这个数是哪几个量
    算出来的 —— 而这张卡的意义正是"让评分不是黑盒"。
    """
    from app.web import queries, ui
    rows = queries.run('q_score_parts', ts_code='600519.SH')
    assert rows, '茅台当天没有评分明细，这条测试失去意义'
    panel = ui.score_panel(rows)
    details = [n for n in _walk(panel) if type(n).__name__ == 'Details']
    assert len(details) == len(rows), '每个分项都该有一个可展开的说明'
    txt = _texts(panel)
    assert '怎么算的' in txt
    assert '还没写说明' not in txt
    assert '判据' in txt and '合成' in txt
    assert '贡献 = 分值' in txt


def test_panel_flags_a_weight_that_no_longer_matches_the_registry():
    """改了权重没重算，页面上必须看得见 —— 否则调完权重看不出没生效。"""
    from app.web import ui
    row = {'cap_key': 'stock_quality', 'script_key': 'xingtai_v1',
           'part_key': 'xingtai', 'part_label': '形态分',
           'part_value': 20.0, 'weight': 0.99, 'contribution': 19.8}
    txt = _texts(ui.score_panel([row]))
    assert '注册表当前权重' in txt and '0.99' in txt

