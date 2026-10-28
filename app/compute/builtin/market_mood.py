# -*- coding: utf-8 -*-
"""
大盘情绪。

**这是一个全市场标量，摊给每只票同一个值** —— 情绪是环境属性，不是个股属性。
存成每只票一行是为了让 analytics.score 的结构保持统一（一只票一天一个功能点一行），
查询和展示都不用为它开特例。

四个维度：
    涨跌家数比 —— 最直接的情绪温度
    涨停家数   —— 情绪的高度（用回算好的 limit_up，不是旧系统的 pct_change>9.8 近似）
    中位涨幅   —— 用中位数而非均值，避免几只妖股把整体情绪拉高
    成交额环比 —— 增量资金是否进场
"""
from __future__ import annotations

import pandas as pd

EXPLAIN = {
    'basis': '涨跌家数比 + 涨停家数 + 中位涨幅 + 成交额环比，'
             '**四项都是全市场标量，每只票拿到同一个值**',
    'terms': (
        {'name': '涨跌家数比', 'w': 0.35,
         'expr': '上涨家数 / (上涨 + 下跌) × 100',
         'norm': '截断映射 30 ~ 70 → -100 ~ +100',
         'note': '最直接的情绪温度。50% 附近≈0 分'},
        {'name': '涨停家数', 'w': 0.25,
         'expr': 'count(close >= limit_up - 0.01)',
         'norm': '截断映射 20 ~ 120 → -100 ~ +100',
         'note': '情绪的高度。用回算好的 limit_up 而不是"涨幅 > 9.8%" —— '
                 '后者会把创业板 20%、北交所 30% 的涨停全部漏掉'},
        {'name': '中位涨幅', 'w': 0.25,
         'expr': 'median(pct_change)',
         'norm': '截断映射 -1.5 ~ +1.5 → -100 ~ +100',
         'note': '用中位数而非均值：几只妖股就能把均值拉高，中位数拉不动'},
        {'name': '成交额环比', 'w': 0.15,
         'expr': 'sum(amount_yuan) / sum(MA(amount_yuan, 5))',
         'norm': '截断映射 0.8 ~ 1.3 → -100 ~ +100',
         'note': '增量资金是否进场'},
    ),
    'combine': '四项加权求和，落在 -100 ~ +100',
    'caveat': '统计口径是**全市场个股**，不是指数 —— 指数被权重股主导，'
              '反映不了赚钱效应。',
}


def run(ctx, params: dict) -> pd.Series:
    t = ctx.today
    pct = t['pct_change'].astype('float64')
    n = int(pct.notna().sum())
    if not n:
        return pd.Series(index=ctx.codes, dtype='float64')

    up = int((pct > 0).sum())
    down = int((pct < 0).sum())
    breadth = up / max(up + down, 1) * 100
    breadth_score = _lin(breadth, 30.0, 70.0)

    # 涨停：收盘价触及回算出的涨停价。这比"涨幅超过 9.8%"准确 ——
    # 创业板 20%、北交所 30% 的票用 9.8% 判会全部漏掉
    limit_up = t['limit_up'].astype('float64')
    close = t['close'].astype('float64')
    touched = int(((limit_up > 0) & (close >= limit_up - 0.01)).sum())
    limit_score = _lin(touched, 20.0, 120.0)

    med = float(pct.median())
    med_score = _lin(med, -1.5, 1.5)

    amt_today = float(t['amount_yuan'].astype('float64').sum())
    amt_ma5 = ctx.series('MA(amount_yuan, 5)')
    mask = amt_ma5.index.get_level_values('trade_date') == ctx.trade_date
    amt_base = float(amt_ma5[mask].sum())
    ratio = amt_today / amt_base if amt_base else None
    amt_score = _lin(ratio, 0.8, 1.3) if ratio else 0.0

    mood = (breadth_score * 0.35 + limit_score * 0.25
            + med_score * 0.25 + amt_score * 0.15)
    return pd.Series(round(mood, 4), index=ctx.codes, dtype='float64')


def _lin(v: float, lo: float, hi: float) -> float:
    """把标量线性映射到 -100~100 并截断。"""
    if v is None or pd.isna(v):
        return 0.0
    v = min(max(float(v), lo), hi)
    return (v - lo) / (hi - lo) * 200 - 100
