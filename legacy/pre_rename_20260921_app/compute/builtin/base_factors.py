# -*- coding: utf-8 -*-
"""
基础因子层：config.indicator 的 27 条公式 → fct.indicator_value。

**这是全链路的最上游**：config.capability.depends_on 里 stock_quality、
sector_heat、market_mood、temp_score 全部依赖它。它的 output_kind 是
`table` 而不是 `score` —— 产出是一张长表（ts_code × ind_key × value），
不是一只票一个分数，所以评分驱动 compute_date 会跳过它，
由编排层单独作为一个步骤调度。

本文件只做一件事：把 ctx 里已经读好的面板转交给 app.compute.indicators。
指标计算的逻辑不重复实现 —— d1_indicators.py 的 --verify 已经拿
独立的 pandas 实现对 ma20/bias20/std20/atr14 做过零误差比对，
再写第二份实现就等于让那份验证失效。
"""
from __future__ import annotations

import pandas as pd

from app.compute import indicators as ind_mod

# 「当前有效权重」那张表上这一行的说明。**它不是评分脚本**，所以 terms 写的是
# "产出什么"而不是"哪几项加权"，combine 明写不参与加权 —— 一张列着 19 个脚本的
# 表里独独两行空着，看的人只会以为是漏了。
EXPLAIN = {
    'basis': '**不是评分脚本**：把 `config.indicator` 里的 27 条公式算成一张'
             '长表（票 × 指标 × 值）落进 `fct.indicator_value`，'
             '是 stock_quality / sector_heat / market_mood / temp_score '
             '四个功能点的**共同上游**。',
    'terms': ({'name': '27 条指标公式', 'expr': 'config.indicator.formula',
               'norm': '各指标自带量纲，不归一化',
               'note': '公式是注册表里的数据，加指标是插一行、不是改代码'},),
    'combine': '不参与加权（`output_kind = table`）。评分驱动 `compute_date` '
               '会跳过它，由编排层单独作为一步调度；这一行的权重 1.0 '
               '只是占位。',
    'caveat': '它是全链路最上游 —— 这一步没跑，下游四个功能点算出来的分全是空的。',
}



def run(ctx, params: dict) -> pd.DataFrame:
    """
    返回每个指标算出多少个有效值的汇总表（一行一个 ind_key）。

    返回汇总而不是全部数值：真正的数值已经由 indicators 落进
    fct.indicator_value 了，再把 15 万个数搬回内存给调用方没有意义。
    调用方需要的是「27 个指标都算出来了吗、有没有哪个全是 NaN」。
    """
    stat = ind_mod.compute_date(
        ctx.trade_date,
        ind_keys=params.get('ind_keys'),
        task_id=params.get('task_id'),
        dry_run=bool(params.get('dry_run')),
        panel=ctx.panel,
    )
    if 'error' in stat:
        raise RuntimeError(stat['error'])

    failed = stat.get('failed') or {}
    rows = [{'ind_key': k, 'value': v, 'error': failed.get(k)}
            for k, v in sorted((stat.get('values') or {}).items())]
    rows += [{'ind_key': k, 'value': 0, 'error': msg}
             for k, msg in sorted(failed.items())
             if k not in (stat.get('values') or {})]
    df = pd.DataFrame(rows, columns=['ind_key', 'value', 'error'])
    df.attrs['stat'] = stat
    return df
