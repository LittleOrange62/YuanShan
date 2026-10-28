# -*- coding: utf-8 -*-
"""
板块热度·成交分项。

判据：板块成交额的截面分位 + 成交额相对自身 20 日均值的放大倍数。
两者缺一不可：绝对成交额永远是大板块领先，只看它等于给权重板块送分；
只看放大倍数则会把小板块的一日异动排到最前。

放量倍数**必须先除掉全市场的放量倍数**，再去比固定区间。
原来的写法是 `clip_raw(amount/MA20, 0.8, 2.5)`，实测 2026-08-25 的 649 个
板块里，amount/MA20 的中位数只有 0.796、p95 只有 1.195 —— 整个分布压在
下界 0.8 以下，于是 5,413 只票的成交分项全是负的（区间 -93.71 ~ -0.91，
最大值都还是负数）。原因不是板块的问题，是"全市场今天缩量"这件事被
算进了每一个板块头上：MA20 里含着前面的高量日，市场量能一收缩，
所有板块的倍数一起掉到 1 以下。固定区间衡量的是"市场量能周期"，
不是"这个板块相对别人放量"。

除以当日全部板块倍数的中位数之后，1.0 就是"跟大盘一样",
>1 才是真的相对放量，固定区间才重新有意义。校准也对得上：
p05/中位数 = 0.70、p95/中位数 = 1.50，正好落在下面的默认上下界上。
"""
from __future__ import annotations

import pandas as pd

DEFAULTS = {
    'w_abs': 0.40,      # 绝对成交额分位的权重
    'w_ratio': 0.60,    # 相对放量的权重
    'ma_n': 20,         # 放量倍数的基准窗口
    'rel_lo': 0.70,     # 相对放量下界 → -100
    'rel_hi': 1.50,     # 相对放量上界 → +100
    'min_ref': 30,      # 少于这么多个板块有数，中位数不可信，退回绝对分位
}

EXPLAIN = {
    'basis': '板块成交额的截面分位 + 成交额相对自身 20 日均值的放大倍数'
             '（**再除掉全市场的放量倍数**）',
    'terms': (
        {'name': '绝对成交额', 'w': DEFAULTS['w_abs'],
         'expr': 'market_data.sector_daily.amount_yuan（当日）',
         'norm': '**板块之间**的截面百分位 → -100 ~ +100',
         'note': '只看这一维等于给权重板块送分，所以必须配相对放量一起看'},
        {'name': '相对放量', 'w': DEFAULTS['w_ratio'],
         'expr': '(amount / MA(amount, 20)) ÷ 当日全部板块该倍数的中位数',
         'norm': f"截断映射 {DEFAULTS['rel_lo']} ~ {DEFAULTS['rel_hi']}"
                 ' → -100 ~ +100',
         'note': '1.0 就是"跟大盘一样"，>1 才是真的相对放量'},
    ),
    'combine': '两项加权求和后除以权重之和；有数的板块不足 '
               f"{DEFAULTS['min_ref']} 个时中位数是噪声，退回只用绝对分位这一维",
    'caveat': '★除以全市场中位数这一步是 2026-08-29 修的缺陷。原写法是 '
              'clip_raw(amount/MA20, 0.8, 2.5)，实测 2026-08-25 的 649 个板块里 '
              'amount/MA20 的中位数只有 0.796、p95 只有 1.195，整个分布压在下界'
              '以下，于是 5,413 只票的成交分项**全是负的**。原因不是板块的问题，'
              '是"全市场今天缩量"被算到了每一个板块头上 —— 固定区间衡量的是'
              '市场量能周期，不是"这个板块相对别人放量"。',
}



def run(ctx, params: dict) -> pd.Series:
    p = {**DEFAULTS, **(params or {})}
    st = ctx.sector_today
    if st.empty:
        return pd.Series(index=ctx.codes, dtype='float64')

    amount = st['amount_yuan'].astype('float64')
    abs_score = ctx.pct_raw(amount)

    amt_n = ctx.sector_roll('amount_yuan', int(p['ma_n']), 'mean')
    ratio = amount / amt_n.reindex(st.index).replace(0, pd.NA)

    ref = ratio.median()
    if ratio.notna().sum() < int(p['min_ref']) or not ref or ref <= 0:
        # 板块数太少（比如历史早期只有几十个板块有日线），中位数是噪声，
        # 这时候只用绝对分位，宁可少一维也不要给一个假的相对分
        by_sector = abs_score
    else:
        rel = ratio / ref
        ratio_score = ctx.clip_raw(rel, float(p['rel_lo']), float(p['rel_hi']))
        w = float(p['w_abs']) + float(p['w_ratio'])
        by_sector = (abs_score * float(p['w_abs'])
                     + ratio_score * float(p['w_ratio'])) / w

    return ctx.broadcast(by_sector).round(4)
