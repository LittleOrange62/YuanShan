# -*- coding: utf-8 -*-
"""
个股质量·量能分项。

判据：量比、成交额截面分位、以及"放量是否伴随上涨"。
放量下跌和放量上涨是两回事，只看量比会把出货当成进场。
"""
from __future__ import annotations

import pandas as pd

EXPLAIN = {
    'basis': '量比 + 成交额截面分位 + "放量是否伴随上涨"',
    'terms': (
        {'name': '量比', 'w': 0.35,
         'expr': 'vol_shares / MA(vol_shares, 5)',
         'norm': '截断映射 0.5 ~ 3.0 → -100 ~ +100',
         'note': '当日量 / 5 日均量。0.5~3 是常态区间，超过 3 已经是异常放量、'
                 '再大没有额外含义'},
        {'name': '成交额', 'w': 0.30,
         'expr': 'amount_yuan（当日）',
         'norm': '全市场截面百分位 → -100 ~ +100',
         'note': '绝对成交额的排名，衡量"有多少钱在这只票上"'},
        {'name': '放量方向', 'w': 0.35,
         'expr': '(vol_shares / MA(vol_shares, 5) - 1) × clip(pct_chg, -10, 10)',
         'norm': '全市场截面百分位 → -100 ~ +100',
         'note': '放量上涨为正、放量下跌为负 —— 只看量比会把出货当成进场。'
                 '涨跌幅先截到 ±10%，免得 20cm/30cm 的票把这一项完全主导',
         'derived': True},
    ),
    'combine': '三项加权求和，落在 -100 ~ +100',
}


def run(ctx, params: dict) -> pd.Series:
    vol_ratio = ctx.cs('vol_shares / MA(vol_shares, 5)')
    # 量比 0.5~3 是常态区间，超过 3 已经是异常放量，再大没有额外含义
    ratio_score = ctx.clip_score(vol_ratio, 0.5, 3.0)

    amt_score = ctx.pct_score(ctx.today['amount_yuan'])

    # 放量方向：量比 × 涨跌幅符号。放量上涨为正，放量下跌为负
    pct = ctx.today['pct_chg'].astype('float64')
    signed = (vol_ratio - 1.0) * pct.clip(-10, 10)
    dir_score = ctx.pct_score(signed)

    return (ratio_score * 0.35 + amt_score * 0.3 + dir_score * 0.35).round(4)
