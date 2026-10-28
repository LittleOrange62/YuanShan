# -*- coding: utf-8 -*-
"""
量能情绪温度（0~100）。

**回答的问题**：一个题材/个股现在处在情绪的哪个阶段，光看涨幅看不出来，
量的形态才能。同样一根 5% 阳线，放量在 5 日均量 1.3 倍、量一天一天变大，
和天量之后缩到 4 成量硬拉新高，是两件相反的事。

四个分项各自对应一条经验判据，权重可以在 config.binding 里改，不用改代码：

    liangbi  量比      当日量 / 5 日均量。1 倍常态，3 倍已是异动，再大无额外含义
    jiezou   放量节奏  5 日均量的斜率 ÷ 20 日均量。**这一项是"温和放量"的量化**：
                      斜率为正且不极端 = 量在一天一天变大；为负 = 量接不上了
    chixu    持续性    过去 10 天里量 > 1.5 倍 20 日均量的天数。高潮期是"天天大红量柱"，
                      单日一根天量不算高潮，算异动
    weizhi   位置      60 日区间内的相对高度。同样的放量，在底部是进场，在顶部是出货

**为什么不复用 temp_score**：那个是"这只票现在烫不烫手"，混了振幅、换手、
乖离这些波动项。这里只问量，且要能被 vol_stage 复用同一套中间量 ——
两个功能点共享 `metrics()`，避免同一个量比在两个脚本里用两套阈值算出两个答案。

**量的口径**：用 vol_shares 而不是 amount_yuan。金额会被价格涨幅本身放大 ——
价格涨 10% 时成交额自然多 10%，把价格信息混进了量指标里。
"""
from __future__ import annotations

import pandas as pd

WEIGHTS = {'liangbi': 0.30, 'jiezou': 0.25, 'chixu': 0.25, 'weizhi': 0.20}

# 中间量的表达式集中在这里，vol_stage 直接引用同一份，口径不会分叉
EXPRS = {
    # 量比：当日量 / 5 日均量
    'vr5': 'vol_shares / MA(vol_shares, 5)',
    # 量位：当日量 / 20 日均量。判"缩量"用这个，比量比稳
    'vr20': 'vol_shares / MA(vol_shares, 20)',
    # 放量节奏：5 日均量的斜率，用 20 日均量归一化成"每天增加常量的百分之几"，
    # 否则大盘股和小盘股的斜率没法用同一个阈值
    'ramp': 'SLOPE(MA(vol_shares, 5), 5) / MA(vol_shares, 20)',
    # 持续放量天数：过去 10 天里量超过 1.5 倍 20 日均量的天数
    'chixu_days': 'COUNT(vol_shares > MA(vol_shares, 20) * 1.5, 10)',
    # K 线扎实度：实体占全幅的比例，5 日均。上下影线长 = 分歧大 = 不扎实
    'solid': 'MA(ABS(close - open) / (high - low), 5)',
    # 当前量 / 20 日天量。**判"天量之后缩量"的核心量**
    'peak_ratio': 'vol_shares / HHV(vol_shares, 20)',
    # 价格相对 20 日最高收盘。1.0 即创新高
    'high_ratio': 'close / HHV(close, 20)',
    # 价格斜率，用价格归一化
    'pslope': 'SLOPE(close, 5) / MA(close, 5)',
    # 位置：60 日区间相对高度（0~100）
    'pos60': '(close - LLV(low, 60)) / (HHV(high, 60) - LLV(low, 60)) * 100',
    'above_ma5': 'close / MA(close, 5)',
    'above_ma10': 'close / MA(close, 10)',
}


def metrics(ctx) -> dict[str, pd.Series]:
    """目标交易日的量能中间量截面。结果由 ctx 自己缓存，重复调用不重复算。"""
    return {k: ctx.cs(v) for k, v in EXPRS.items()}


EXPLAIN = {
    'basis': '量比 + 放量节奏 + 持续性 + 位置，四项换算到 0~100 后加权。'
             '**值域是 0~100（度），不是 -100~100**',
    'terms': (
        {'name': '量比', 'w': WEIGHTS['liangbi'], 'expr': EXPRS['vr5'],
         'norm': '截断映射 0.5 ~ 3.0 → 0 ~ 100 度',
         'note': '1 倍常态，3 倍已是异动，再大没有额外含义'},
        {'name': '放量节奏', 'w': WEIGHTS['jiezou'], 'expr': EXPRS['ramp'],
         'norm': '截断映射 -0.10 ~ +0.30 → 0 ~ 100 度',
         'note': '这一项是"温和放量"的量化：斜率为正且不极端＝量一天一天变大；'
                 '为负＝量接不上了。超过 0.3 是一次性天量，不是逐日放大。'
                 '用 20 日均量归一化，大盘股小盘股才能用同一套阈值'},
        {'name': '持续性', 'w': WEIGHTS['chixu'], 'expr': EXPRS['chixu_days'],
         'norm': '截断映射 0 ~ 6 天 → 0 ~ 100 度',
         'note': '高潮期是"天天大红量柱"；单日一根天量只算异动，不算高潮'},
        {'name': '位置', 'w': WEIGHTS['weizhi'], 'expr': EXPRS['pos60'],
         'norm': '截断映射 0 ~ 100 → 0 ~ 100 度',
         'note': '同样的放量，在底部是进场，在顶部是出货'},
    ),
    'combine': '四项加权平均（分母只累计**有值**的分项权重），最后截到 0~100',
    'caveat': '量的口径用 vol_shares 而不是 amount_yuan：金额会被价格涨幅本身'
              '放大，价格涨 10% 成交额自然多 10%，那等于把价格信息混进量指标。'
              '缺分项只摊掉自己的权重 —— 上市不足 60 日的新股 pos60 是空的，'
              '按 0 算会让它看起来像"没人关注"。',
}



def _t100(score: pd.Series) -> pd.Series:
    """ctx 的打分是 -100~100，温度用 0~100。"""
    return (pd.to_numeric(score, errors='coerce') + 100.0) / 2.0


def run(ctx, params: dict) -> pd.Series:
    w = dict(WEIGHTS)
    for k, v in (params.get('weights') or {}).items():
        if k in w:
            w[k] = float(v)

    m = metrics(ctx)
    parts = {
        'liangbi': _t100(ctx.clip_score(m['vr5'], 0.5, 3.0)),
        # 斜率区间 -0.10 ~ +0.30：实测放量启动期的量斜率普遍落在 0.05~0.25，
        # 超过 0.3 是一次性天量，不是"一天一天变大"
        'jiezou': _t100(ctx.clip_score(m['ramp'], -0.10, 0.30)),
        'chixu': _t100(ctx.clip_score(m['chixu_days'], 0, 6)),
        'weizhi': _t100(ctx.clip_score(m['pos60'], 0, 100)),
    }

    # 缺分项只摊掉它的权重，不能按 0 计入 —— 否则新股（不足 60 日）
    # 会因为 pos60 是空值而整体降温，看起来像"没人关注"
    num = pd.Series(0.0, index=ctx.codes)
    den = pd.Series(0.0, index=ctx.codes)
    for key, s in parts.items():
        v = s.reindex(ctx.codes)
        ok = v.notna()
        num = num.add(v.where(ok, 0.0) * w[key], fill_value=0.0)
        den = den.add(pd.Series(w[key], index=ctx.codes).where(ok, 0.0),
                      fill_value=0.0)
    return (num / den.replace(0.0, pd.NA)).astype('float64').clip(0, 100).round(4)
