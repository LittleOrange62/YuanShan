# -*- coding: utf-8 -*-
"""
个股质量·趋势分项。

判据：均线多头排列 + 20 日回归斜率 + 站上 MA20 的天数。
斜率用回归而不是首末两点之差 —— 后者一根插针就能把趋势判反。
"""
from __future__ import annotations

import pandas as pd

EXPLAIN = {
    'basis': '均线多头排列 + 20 日回归斜率 + 站上 MA20 的天数',
    'terms': (
        {'name': '多头排列', 'w': 0.40,
         'expr': '(MA(close,5) > MA(close,10)) + (MA(close,10) > MA(close,20))'
                 ' + (MA(close,20) > MA(close,60)) + (close > MA(close,20))',
         'norm': '截断映射 0 ~ 4 → -100 ~ +100',
         'note': '四层各算一分，满分 4。四层全对＝+100，全不对＝-100'},
        {'name': '回归斜率', 'w': 0.40,
         'expr': 'SLOPE(close, 20) / close * 100',
         'norm': '全市场截面百分位 → -100 ~ +100',
         'note': '斜率用 20 日最小二乘回归、不是首末两点之差 —— 后者一根插针'
                 '就能把趋势判反。除以收盘价换成"每天涨百分之几"，'
                 '否则茅台涨 1% 的斜率远大于 2 元票'},
        {'name': '站上 MA20 天数', 'w': 0.20,
         'expr': 'COUNT(close > MA(close, 20), 20)',
         'norm': '截断映射 0 ~ 20 → -100 ~ +100',
         'note': '衡量趋势的持续性，而不是今天这一个瞬时状态'},
    ),
    'combine': '三项加权求和，落在 -100 ~ +100',
}


def run(ctx, params: dict) -> pd.Series:
    ma5 = ctx.cs('MA(close, 5)')
    ma10 = ctx.cs('MA(close, 10)')
    ma20 = ctx.cs('MA(close, 20)')
    ma60 = ctx.cs('MA(close, 60)')
    close = ctx.today['close'].astype('float64')

    # 多头排列打分：每满足一层加一分，满分 4
    align = (
        (ma5 > ma10).astype(float)
        + (ma10 > ma20).astype(float)
        + (ma20 > ma60).astype(float)
        + (close > ma20).astype(float)
    )
    align_score = ctx.clip_score(align, 0, 4)

    # 斜率按截面百分位，避免不同价位的票不可比（茅台涨 1% 的斜率远大于 2 元票）
    slope_pct = ctx.pct_score(ctx.cs('SLOPE(close, 20) / close * 100'))

    # 站上 MA20 的天数（20 日内），衡量趋势的持续性而非瞬时状态
    above = ctx.cs('COUNT(close > MA(close, 20), 20)')
    above_score = ctx.clip_score(above, 0, 20)

    return (align_score * 0.4 + slope_pct * 0.4 + above_score * 0.2).round(4)
