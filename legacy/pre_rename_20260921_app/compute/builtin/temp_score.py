# -*- coding: utf-8 -*-
"""
个股温度计（0~100）。

和 stock_quality 的区别：质量分回答"这只票好不好"，温度回答
"**现在**热不热、烫不烫手"。同一只票可以是质量 80 分、温度 95 度 ——
好票冲过头了，该减不该加。这两个维度混成一个分数是旧系统最大的问题：
80 分的票买进去第二天吃跌停，事后看温度已经 97 度了，但分数里看不出来。

四个分项在这一个脚本里算完，因为它们要共用一套 0~100 的量纲：
    位置  pos250，越高越热
    量比  当日量 / 5 日均量，放量即升温
    趋势  均线多头排列的完整度
    危险  振幅、换手、偏离 MA20 —— **反向**，越危险温度越高

危险项是加分不是减分：温度高本身不是坏事，是提示"这里波动大、
需要更小的仓位"。把它做成减分就变成了另一个质量分。
"""
from __future__ import annotations

import pandas as pd

WEIGHTS = {'weizhi': 0.30, 'liangbi': 0.25, 'qushi': 0.25, 'weixian': 0.20}

EXPLAIN = {
    'basis': '位置 + 量比 + 趋势 + 危险，四项换算到 0~100 后加权。'
             '**值域是 0~100（度），不是 -100~100**',
    'terms': (
        {'name': '位置', 'w': WEIGHTS['weizhi'],
         'expr': '(close - LLV(low,250)) / (HHV(high,250) - LLV(low,250)) * 100',
         'norm': '截断映射 0 ~ 100 → 0 ~ 100 度',
         'note': '一年区间里越高越热'},
        {'name': '量比', 'w': WEIGHTS['liangbi'],
         'expr': 'vol_shares / MA(vol_shares, 5)',
         'norm': '截断映射 0.5 ~ 3.0 → 0 ~ 100 度',
         'note': '用绝对倍数而不是截面百分位 —— 全市场缩量的日子里，'
                 '百分位会把"最不缩量"的票判成放量'},
        {'name': '趋势', 'w': WEIGHTS['qushi'],
         'expr': '(MA(close,5) > MA(close,10)) + (MA(close,10) > MA(close,20))'
                 ' + (MA(close,20) > MA(close,60)) + (close > MA(close,20))',
         'norm': '截断映射 0 ~ 4 → 0 ~ 100 度',
         'note': '均线多头排列的完整度'},
        {'name': '危险', 'w': WEIGHTS['weixian'],
         'expr': '振幅 MA((high - low) / pre_close * 100, 5) [2~12]、'
                 '换手 MA(turnover_rate_pct, 5) [1~15]、'
                 '乖离 |(close - MA(close,20)) / MA(close,20) * 100| [0~20]'
                 ' 三项**等权取均值**',
         'norm': '各自截断映射到 0 ~ 100 度后取均值',
         'note': '★危险是**加分不是减分**：温度高本身不是坏事，是提示"这里'
                 '波动大、要更小的仓位"。做成减分它就变成第二个质量分了。'
                 '振幅和换手用 5 日均值，单日一根大阳线不该被判成"持续高危"',
         'derived': True},
    ),
    'combine': '四项加权平均（分母只累计**有值**的分项权重），最后截到 0~100',
    'caveat': '缺分项只摊掉它自己的权重，不按 0 计入 —— 换手对北交所和部分'
              '老票是空的（缺 float_share），按 0 算会把整只票的温度拉低。',
}



def _t100(score: pd.Series) -> pd.Series:
    """把 ctx 的 -100~100 打分换算到 0~100 的温度量纲。"""
    return (pd.to_numeric(score, errors='coerce') + 100.0) / 2.0


def run(ctx, params: dict) -> pd.Series:
    w = dict(WEIGHTS)
    for k, v in (params.get('weights') or {}).items():
        if k in w:
            w[k] = float(v)

    # 位置：250 日区间内的相对高度
    pos = ctx.cs('(close - LLV(low,250)) / (HHV(high,250) - LLV(low,250)) * 100')
    weizhi = _t100(ctx.clip_score(pos, 0, 100))

    # 量比：1 倍是常态，3 倍已经是异动。用绝对倍数而不是截面百分位——
    # 全市场缩量的日子里，百分位会把"最不缩量"的票判成放量
    ratio = ctx.cs('vol_shares / MA(vol_shares, 5)')
    liangbi = _t100(ctx.clip_score(ratio, 0.5, 3.0))

    align = (
        (ctx.cs('MA(close, 5)') > ctx.cs('MA(close, 10)')).astype(float)
        + (ctx.cs('MA(close, 10)') > ctx.cs('MA(close, 20)')).astype(float)
        + (ctx.cs('MA(close, 20)') > ctx.cs('MA(close, 60)')).astype(float)
        + (ctx.today['close'].astype('float64') > ctx.cs('MA(close, 20)')).astype(float)
    )
    qushi = _t100(ctx.clip_score(align, 0, 4))

    # 危险：三项等权。振幅和换手用 5 日均值而非当日值，
    # 单日一根大阳线不该被判成"持续高危"
    amp = ctx.cs('MA((high - low) / pre_close * 100, 5)')
    amp_t = _t100(ctx.clip_score(amp, 2.0, 12.0))
    turn = ctx.cs('MA(turnover_rate_pct, 5)')
    turn_t = _t100(ctx.clip_score(turn, 1.0, 15.0))
    bias = ctx.cs('(close - MA(close, 20)) / MA(close, 20) * 100').abs()
    bias_t = _t100(ctx.clip_score(bias, 0.0, 20.0))
    # 换手对北交所和部分老票是空的（缺 float_share），只按有值的项取均值，
    # 否则整只票的危险分会被拉低
    weixian = pd.concat([amp_t, turn_t, bias_t], axis=1).mean(axis=1, skipna=True)

    parts = {'weizhi': weizhi, 'liangbi': liangbi, 'qushi': qushi,
             'weixian': weixian}
    num = pd.Series(0.0, index=ctx.codes)
    den = pd.Series(0.0, index=ctx.codes)
    for key, s in parts.items():
        v = s.reindex(ctx.codes)
        ok = v.notna()
        num = num.add(v.where(ok, 0.0) * w[key], fill_value=0.0)
        den = den.add(pd.Series(w[key], index=ctx.codes).where(ok, 0.0),
                      fill_value=0.0)
    return (num / den.replace(0.0, pd.NA)).astype('float64').clip(0, 100).round(4)
