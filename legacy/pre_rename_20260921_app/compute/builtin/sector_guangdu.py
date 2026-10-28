# -*- coding: utf-8 -*-
"""
板块热度·广度分项。

判据：板块内上涨家数占比。广度是热度里最难造假的一维 ——
指数可以被几只权重股拉起来，但上涨家数占比骗不了人。
"""
from __future__ import annotations

import pandas as pd

EXPLAIN = {
    'basis': '板块内上涨家数占比（当日 + 5 日平均）',
    'terms': (
        {'name': '当日广度', 'w': 0.60,
         'expr': 'up_count / (up_count + down_count) * 100',
         'norm': '截断映射 20 ~ 80 → -100 ~ +100',
         'note': '分母用"涨+跌"而不是成分数 —— 停牌的票不该算进分母。'
                 '广度是热度里最难造假的一维：指数可以被几只权重股拉起来，'
                 '上涨家数占比骗不了人'},
        {'name': '5 日广度', 'w': 0.40,
         'expr': 'MA(up_count,5) / (MA(up_count,5) + MA(down_count,5)) * 100',
         'norm': '截断映射 30 ~ 70 → -100 ~ +100',
         'note': '衡量热度的持续性。区间比当日窄，因为 5 日均值本身波动更小'},
    ),
    'combine': '两项加权求和得到板块分，再按 ref.sector_member 摊给成员个股',
    'caveat': '成分不足 5 只的板块广度没有统计意义，直接置空而不是给一个分数。',
}


def run(ctx, params: dict) -> pd.Series:
    st = ctx.sector_today
    if st.empty:
        return pd.Series(index=ctx.codes, dtype='float64')

    up = st['up_count'].astype('float64')
    down = st['down_count'].astype('float64')
    member = st['member_count'].astype('float64').replace(0, pd.NA)
    # 用 up/(up+down) 而不是 up/member：停牌的票不该算进分母
    traded = (up + down).replace(0, pd.NA)
    ratio = (up / traded * 100)
    ratio_score = ctx.clip_raw(ratio, 20.0, 80.0)

    # 5 日平均广度，衡量热度的持续性
    up5 = ctx.sector_roll('up_count', 5, 'mean')
    dn5 = ctx.sector_roll('down_count', 5, 'mean')
    ratio5 = (up5 / (up5 + dn5).replace(0, pd.NA) * 100)
    ratio5_score = ctx.clip_raw(ratio5, 30.0, 70.0)

    # 成分数太少的板块（不足 5 只）广度没有统计意义，置空
    thin = member.fillna(0) < 5
    by_sector = (ratio_score * 0.6 + ratio5_score * 0.4).mask(thin)
    return ctx.broadcast(by_sector).round(4)
