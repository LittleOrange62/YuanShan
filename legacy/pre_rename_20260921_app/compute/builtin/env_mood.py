# -*- coding: utf-8 -*-
"""
环境共振·大盘情绪分项。

**这是一个透传，不重算。** market_mood 已经算过一遍全市场情绪，
这里再算一遍等于同一份数据出两个不同的结论 —— 页面上一个显示 62、
一个显示 58，复盘时没人说得清该信哪个。

透传而不是把 market_mood 直接绑到 env_resonance 上，是为了留一个改写点：
将来如果要给情绪加权（比如"情绪对小盘票的影响更大"），改这一个文件就行，
不用动 market_mood 本身。
"""
from __future__ import annotations

import pandas as pd

EXPLAIN = {
    'basis': '**透传上游 market_mood（大盘情绪），不重算**',
    'terms': (
        {'name': '大盘情绪', 'w': 1.0, 'expr': 'fct.score[market_mood]',
         'norm': '原值直接用，已经是 -100 ~ +100',
         'note': '想看这个数怎么来的，看「大盘情绪」那个功能点的说明'},
    ),
    'combine': '一项，原值透传',
    'caveat': '为什么透传而不重算：market_mood 已经算过一遍全市场情绪，'
              '再算一遍等于同一份数据出两个结论 —— 页面上一个显示 62 一个显示 58，'
              '复盘时没人说得清该信哪个。上游没算出来时给**空值而不是 0**，'
              '0 在 -100~100 里是"情绪中性"，会把"不知道"伪装成"一般"。',
}


def run(ctx, params: dict) -> pd.Series:
    prior = params.get('_prior') or {}
    s = prior.get('market_mood')
    if s is None:
        # 上游没算出来时给空值而不是 0：0 在 -100~100 里是"情绪中性"，
        # 会把"不知道"伪装成"一般"。驱动那边会按缺失处理、不计权重。
        return pd.Series(index=ctx.codes, dtype='float64')
    return pd.to_numeric(s, errors='coerce').reindex(ctx.codes).round(4)
