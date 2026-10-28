# -*- coding: utf-8 -*-
"""
板块热度·动量分项。

判据：板块 20 日动量 + 5 日相对 20 日的加速度。
加速度是关键：20 日涨幅高但已经减速的板块正在退潮，
只看动量会在退潮时给出最高分——这是追高的经典成因。
"""
from __future__ import annotations

import pandas as pd

EXPLAIN = {
    'basis': '板块 20 日动量 + 5 日相对 20 日的加速度',
    'terms': (
        {'name': '20 日动量', 'w': 0.45,
         'expr': 'MA(sector_daily.pct_change, 20)',
         'norm': '**板块之间**的截面百分位 → -100 ~ +100',
         'note': '板块的中期强弱'},
        {'name': '加速度', 'w': 0.55,
         'expr': 'MA(pct_change, 5) - MA(pct_change, 20)',
         'norm': '**板块之间**的截面百分位 → -100 ~ +100',
         'note': '权重比动量本身还高：20 日涨幅高但已经减速的板块正在退潮，'
                 '只看动量会在退潮时给出最高分 —— 追高的经典成因'},
    ),
    'combine': '两项加权求和得到板块分，再按 reference_data.sector_member 摊给成员个股',
}


def run(ctx, params: dict) -> pd.Series:
    if ctx.sector_daily.empty:
        return pd.Series(index=ctx.codes, dtype='float64')

    m5 = ctx.sector_roll('pct_change', 5, 'mean')
    m20 = ctx.sector_roll('pct_change', 20, 'mean')
    mom_score = ctx.pct_raw(m20)
    accel_score = ctx.pct_raw(m5 - m20)

    by_sector = (mom_score * 0.45 + accel_score * 0.55)
    return ctx.broadcast(by_sector).round(4)
