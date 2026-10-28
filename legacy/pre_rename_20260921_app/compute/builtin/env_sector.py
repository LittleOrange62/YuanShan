# -*- coding: utf-8 -*-
"""
环境共振·板块维度（权重 0.65）。

直接取上游 sector_heat 的得分。**不重算一遍** —— 同一个量算两次
迟早会因为一处改了另一处没改而出现两个不同的"板块热度"。
上游没算出来（例如板块日线缺当日数据）就返回空，让驱动按缺失处理。
"""
from __future__ import annotations

import pandas as pd

EXPLAIN = {
    'basis': '**透传上游 sector_heat（板块热度），不重算**',
    'terms': (
        {'name': '板块热度', 'w': 1.0, 'expr': 'fct.score[sector_heat]',
         'norm': '原值直接用，已经是 -100 ~ +100',
         'note': '它本身＝涨幅 0.30 + 广度 0.30 + 动量 0.25 + 成交 0.15'},
    ),
    'combine': '一项，原值透传',
    'caveat': '同一个量算两次，迟早会因为一处改了另一处没改而出现两个不同的'
              '"板块热度"。上游没算出来（例如板块日线缺当日数据）就返回空，'
              '让驱动按缺失处理。',
}


def run(ctx, params: dict) -> pd.Series:
    prior = params.get('_prior') or {}
    s = prior.get('sector_heat')
    if s is None:
        return pd.Series(index=ctx.codes, dtype='float64')
    return pd.to_numeric(s, errors='coerce').reindex(ctx.codes).round(4)
