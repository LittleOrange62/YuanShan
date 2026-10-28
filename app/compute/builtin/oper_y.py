# -*- coding: utf-8 -*-
"""
操作评分·Y 轴：环境共振。

四象限的纵坐标，透传 env_resonance。

权重上 Y 占 0.60、X 占 0.40 —— 环境比个股质量重要。这是刻意的：
弱环境里的强票（Q4）大概率是补跌前的最后一段，强环境里的弱票（Q2）
反而经常轮到。旧系统 X/Y 各占一半，实测在指数下行段会持续推荐强票。
"""
from __future__ import annotations

import pandas as pd

EXPLAIN = {
    'basis': '四象限的**纵坐标**：透传上游 env_resonance（环境共振）',
    'terms': (
        {'name': '环境共振', 'w': 1.0, 'expr': 'analytics.score[env_resonance]',
         'norm': '原值直接用，已经是 -100 ~ +100',
         'note': '它本身＝板块热度 × 0.65 + 大盘情绪 × 0.35'},
    ),
    'combine': '一项，原值透传',
    'caveat': 'Y 在操作分里占 0.60、X 占 0.40 —— 环境比个股质量重要，这是刻意的：'
              '弱环境里的强票（Q4）大概率是补跌前的最后一段，强环境里的弱票（Q2）'
              '反而经常轮到。旧系统 X/Y 各占一半，实测在指数下行段会持续推荐强票。',
}


def run(ctx, params: dict) -> pd.Series:
    prior = params.get('_prior') or {}
    s = prior.get('env_resonance')
    if s is None:
        return pd.Series(index=ctx.codes, dtype='float64')
    return pd.to_numeric(s, errors='coerce').reindex(ctx.codes).round(4)
