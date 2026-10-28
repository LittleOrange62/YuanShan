# -*- coding: utf-8 -*-
"""
评分分项的「怎么算的」——「评分归因」那张卡上每一行的说明来自这里。

**为什么说明写在脚本里、不写进注册表**：它描述的是**代码怎么算**，
不是可配项。可配的那部分（分项权重 `config.binding.weight`、阈值
`config.binding.params`）本来就在注册表里，页面上另外标出来 ——
把公式也塞进 `config.script` 只会多一份能和代码对不上的副本。

`EXPLAIN` 的形状（每个 `app/compute/builtin/*.py` 里一个模块常量）：

    EXPLAIN = {
        'basis':   '一句话判据',
        'terms':   ({'name': 子项名, 'w': 内部权重, 'expr': 公式,
                     'norm': 归一化方式, 'note': 为什么这么算}, ...),
        'combine': '几个子项怎么合成一个分值',
        'caveat':  '口径警告（可选，页面上标红）',
    }

`tests/test_explain.py` 会拿脚本源码核对 `expr` 与内部权重 ——
改了公式忘了改说明，测试会红，这是这套说明不会烂掉的唯一保证。
"""
from __future__ import annotations

from app.compute import scoring
from app.db import engine


def explain_of(path: str) -> dict | None:
    """按 config.script.path 取模块里的 EXPLAIN。取不到返回 None。"""
    try:
        mod = scoring.load_module(path)
    except Exception:
        # 路径非法或模块删了，说明缺失不该让整页 500
        return None
    ex = getattr(mod, 'EXPLAIN', None)
    return ex if isinstance(ex, dict) else None


def explain_map(script_keys=None) -> dict[str, dict]:
    """
    script_key → EXPLAIN。一次查库拿全部路径，避免每行一次查询。

    `script_keys` 给 None 就取全部启用脚本（页面上一只票的分项撑死十几行，
    全取也不贵，而且缓存友好）。
    """
    if script_keys is not None:
        keys = sorted({k for k in script_keys if k})
        if not keys:
            return {}
        rows = engine.query(
            'SELECT script_key, path FROM config.script WHERE script_key = ANY(%s)',
            (keys,))
    else:
        rows = engine.query('SELECT script_key, path FROM config.script')
    out = {}
    for r in rows:
        ex = explain_of(r['path'] or '')
        if ex:
            out[r['script_key']] = ex
    return out


def binding_map() -> dict[tuple[str, str], dict]:
    """
    (cap_key, script_key) → 注册表里**当前**的权重和参数。

    页面拿它和 `fct.score_detail.weight`（算这一行时用的权重）比：
    两者不一致就说明改过权重之后没重算，这种"页面上的分数不是按当前配置
    算出来的"必须让人看见，否则调完权重看不出没生效。
    """
    rows = engine.query(
        """
        SELECT cap_key, script_key, eff_weight, params
          FROM config.v_effective_binding
        """)
    return {(r['cap_key'], r['script_key']):
            {'weight': r['eff_weight'], 'params': r['params'] or {}}
            for r in rows}
