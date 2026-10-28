# -*- coding: utf-8 -*-
"""
板块热度·涨幅分项。

板块当日涨幅 + 5 日累计涨幅的截面分位，再摊到成员个股。
两个窗口一起看：只看当日会把一日游的脉冲当成热点。
"""
from __future__ import annotations

import pandas as pd

EXPLAIN = {
    'basis': '板块当日涨幅 + 5 日平均涨幅的截面分位，再摊到成员个股',
    'terms': (
        {'name': '当日涨幅', 'w': 0.45,
         'expr': 'md.sector_daily.pct_chg（当日）',
         'norm': '**板块之间**的截面百分位 → -100 ~ +100',
         'note': '排名是在当天所有板块里排，不是在个股里排'},
        {'name': '5 日涨幅', 'w': 0.55,
         'expr': 'MA(sector_daily.pct_chg, 5)',
         'norm': '**板块之间**的截面百分位 → -100 ~ +100',
         'note': '权重比当日高：只看当日会把一日游的脉冲当成热点'},
    ),
    'combine': '两项加权求和得到板块分，再按 ref.sector_member 摊给成员个股',
    'caveat': '行业板块只覆盖约 54% 的个股，摊不到的票这一项是**空值**而不是 0'
              '—— 0 在 -100~100 里表示"中性"，会把"不知道"伪装成"一般"。'
              '空值分项不计权重，不会拉低总分。',
}


def run(ctx, params: dict) -> pd.Series:
    st = ctx.sector_today
    if st.empty:
        return pd.Series(index=ctx.codes, dtype='float64')

    day = ctx.pct_raw(st['pct_chg'])
    ma5 = ctx.pct_raw(ctx.sector_roll('pct_chg', 5, 'mean'))
    by_sector = (day * 0.45 + ma5 * 0.55)
    return ctx.broadcast(by_sector).round(4)
