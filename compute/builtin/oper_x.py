# -*- coding: utf-8 -*-
"""
操作评分·X 轴：个股质量。

四象限的横坐标。透传 stock_quality，不重算 —— 象限图上的 X 值必须和
/stock 页面上显示的质量分是同一个数，否则一只票在象限图里落在 Q1、
在明细页却是负分，用户只会认为系统在骗人。
"""
from __future__ import annotations

import pandas as pd

EXPLAIN = {
    'basis': '四象限的**横坐标**：透传上游 stock_quality（个股质量）',
    'terms': (
        {'name': '个股质量', 'w': 1.0, 'expr': 'ys.score[stock_quality]',
         'norm': '原值直接用，已经是 -100 ~ +100',
         'note': '它本身＝形态 0.25 + 趋势 0.25 + 位置 0.20 + 筹码 0.20 '
                 '+ 量能 0.10'},
    ),
    'combine': '一项，原值透传',
    'caveat': '不重算：象限图上的 X 必须和 /stock 页面显示的质量分是同一个数，'
              '否则一只票在象限图里落在 Q1、在明细页却是负分，'
              '看到的人只会认为系统在骗人。',
}


def run(ctx, params: dict) -> pd.Series:
    prior = params.get('_prior') or {}
    s = prior.get('stock_quality')
    if s is None:
        return pd.Series(index=ctx.codes, dtype='float64')
    return pd.to_numeric(s, errors='coerce').reindex(ctx.codes).round(4)
