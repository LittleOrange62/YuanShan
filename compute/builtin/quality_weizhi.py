# -*- coding: utf-8 -*-
"""
个股质量·位置分项。

判据：250 日价格位置 + 距 250 日最高的回撤幅度。
位置高分给"在高位但不是刚冲顶"的票：pos250 高说明趋势在，
回撤适中说明还有空间，紧贴最高点反而是追高。
"""
from __future__ import annotations

import pandas as pd

EXPLAIN = {
    'basis': '250 日价格位置 + 距 250 日最高的回撤幅度',
    'terms': (
        {'name': '250 日位置', 'w': 0.60,
         'expr': '(close - LLV(low,250)) / (HHV(high,250) - LLV(low,250)) * 100',
         'norm': '截断映射 0 ~ 100 → -100 ~ +100',
         'note': '一年区间里的相对高度。贴着一年最低＝-100，贴着最高＝+100'},
        {'name': '回撤适中', 'w': 0.40,
         'expr': '-|(HHV(high,250) - close) / HHV(high,250) * 100 - 3|',
         'norm': '截断映射 -22 ~ 0 → -100 ~ +100',
         'note': '回撤 3% 附近满分：紧贴新高（0%）是追高，跌超 25% 是趋势已坏。'
                 '所以位置高分给的是"在高位但不是刚冲顶"'},
    ),
    'combine': '两项加权求和，落在 -100 ~ +100',
}


def run(ctx, params: dict) -> pd.Series:
    pos = ctx.cs('(close - LLV(low,250)) / (HHV(high,250) - LLV(low,250)) * 100')
    pos_score = ctx.clip_score(pos, 0, 100)

    # 距 250 日高点的回撤（%），0 表示正在新高
    dd = ctx.cs('(HHV(high,250) - close) / HHV(high,250) * 100')
    # 回撤 3%~25% 给高分：紧贴新高(0%)是追高，跌超 25% 是趋势已坏
    ideal = (dd - 3.0).abs()
    dd_score = ctx.clip_score(-ideal, -22.0, 0.0)

    return (pos_score * 0.6 + dd_score * 0.4).round(4)
