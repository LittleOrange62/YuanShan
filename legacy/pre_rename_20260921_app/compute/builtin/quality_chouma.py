# -*- coding: utf-8 -*-
"""
个股质量·筹码分项。

**口径说明（重要）**：真正的筹码分布要逐笔或分价成交数据，本系统目前只有日线，
所以这里是**代理指标**，不是真筹码分布：
    1. 换手率的 20 日均值 —— 换手充分说明浮筹已经换手
    2. 换手率的稳定性（标准差越小越好）—— 忽高忽低是资金在博弈
    3. 20 日内收盘价的集中度 —— 价格窄幅震荡说明成本区集中
等 md.stock_minute 补齐后应换成真实分价数据。**不要因为它叫「筹码」就当真筹码用。**
"""
from __future__ import annotations

import pandas as pd

EXPLAIN = {
    'basis': '换手充分度 + 换手稳定性 + 20 日价格集中度（**代理指标，不是真筹码分布**）',
    'terms': (
        {'name': '换手充分', 'w': 0.40,
         'expr': 'MA(turnover_rate_pct, 20)',
         'norm': '截断映射 0.5 ~ 8.0 → -100 ~ +100',
         'note': '换手率 20 日均值。低于 0.5% 是无人问津，高于 8% 是过热博弈'},
        {'name': '换手稳定', 'w': 0.30,
         'expr': 'STD(turnover_rate_pct, 20)',
         'norm': '全市场截面百分位（反向）→ -100 ~ +100',
         'note': '标准差越小越好 —— 换手忽高忽低是资金在博弈，不是筹码沉淀'},
        {'name': '成本集中', 'w': 0.30,
         'expr': '(HHV(high,20) - LLV(low,20)) / MA(close,20) * 100',
         'norm': '全市场截面百分位（反向）→ -100 ~ +100',
         'note': '20 日价格区间越窄，成本区越集中'},
    ),
    'combine': '三项加权求和，落在 -100 ~ +100',
    'caveat': '真正的筹码分布要逐笔或分价成交数据，本系统目前只有日线，'
              '所以这三项是**代理指标**。等 md.stock_minute 补齐后应换成真实分价'
              '数据 —— 不要因为它叫「筹码」就当真筹码用。',
}


def run(ctx, params: dict) -> pd.Series:
    turn_ma = ctx.cs('MA(turnover_rate_pct, 20)')
    # 换手 1%~8% 给高分：低于 1% 是无人问津，高于 8% 是过热博弈
    turn_score = ctx.clip_score(turn_ma, 0.5, 8.0)

    turn_std = ctx.cs('STD(turnover_rate_pct, 20)')
    # 稳定性：标准差越小越好，所以反向取百分位
    stable_score = ctx.pct_score(turn_std, reverse=True)

    # 成本集中度：20 日振幅越窄，筹码成本越集中
    concentration = ctx.cs('(HHV(high,20) - LLV(low,20)) / MA(close,20) * 100')
    conc_score = ctx.pct_score(concentration, reverse=True)

    return (turn_score * 0.4 + stable_score * 0.3 + conc_score * 0.3).round(4)
