# -*- coding: utf-8 -*-
"""
量能阶段判别（离散码 1~5）。

温度是连续量，回答"多热"；阶段是离散量，回答"处在生命周期的哪一段"。
这两个不能合成一个数：温度 85 度可能是主升高潮（该持有），
也可能是天量之后的缩量新高（该跑），操作方向完全相反。

    1  冷却    量在 20 日均量以下，没人管
    2  酝酿    **温和放量**：量一天一天变大，K 线扎实，站在 5 日线上
    3  高潮    **持续放量**：天天大红量柱，价格在高位
    4  背离    **天量之后缩量上涨**：价格还在创新高，量接不上了 ← 最危险
    5  退潮    价格跌破 10 日线且量在收缩

**判定顺序是 4 → 3 → 2 → 5 → 1，不能改。**
背离必须最先判：缩量新高同时也满足"价格在高位"，若先判高潮，
最该警告的那一类会被标成"持有"。这正是这套判别唯一的价值所在 ——
按涨幅排序永远排不出 4 和 3 的区别。

阈值全部走 params，可以在 config.binding.params 里改而不用改代码。
默认值的来路写在各自的注释里。
"""
from __future__ import annotations

import pandas as pd

from app.compute.builtin.vol_phase import metrics

STAGE = {1: '冷却', 2: '酝酿', 3: '高潮', 4: '背离', 5: '退潮'}

DEFAULTS = {
    # 酝酿：量比 1.05~2.0 是"温和"。低于 1.05 谈不上放量；
    # 高于 2.0 已经是市场都看见了，不再是"悄然进场"
    'incub_vr_lo': 1.05,
    'incub_vr_hi': 2.0,
    # 实体占全幅 45% 以上算扎实。长上影的放量是试盘或出货，不是吸筹
    'incub_solid': 0.45,
    # 高潮：量比 ≥2 或过去 10 天里有 ≥3 天是 1.5 倍量
    'climax_vr': 2.0,
    'climax_days': 3,
    # 高潮要求价格在 20 日高点的 95% 以内 —— 放量但价格在半山腰是反弹不是主升
    'climax_high': 0.95,
    # 背离：价格创新高（≥20 日最高收盘的 99.5%）
    'div_high': 0.995,
    # 但量只有 20 日天量的 60% 以下。0.6 这个数是"量接不上"的分界：
    # 天量当日 peak_ratio=1.0，缩到 6 成意味着接盘资金少了四成
    'div_peak': 0.60,
    # 退潮：跌破 10 日线且量在 20 日均量之下
    'ebb_vr20': 0.90,
}

EXPLAIN = {
    'basis': '按量能形态判一个**离散码 1~5**（不是分数）：'
             '1 冷却 / 2 酝酿 / 3 高潮 / 4 背离 / 5 退潮',
    'terms': (
        {'name': '4 背离 ← 最危险',
         'expr': f"close/HHV(close,20) ≥ {DEFAULTS['div_high']} 且 "
                 f"vol/HHV(vol,20) ≤ {DEFAULTS['div_peak']} 且 "
                 '量斜率 < 0 且 价斜率 > 0',
         'note': '天量之后缩量上涨：价格还在创新高，量已经接不上了。'
                 '要求价斜率 > 0 是为了把高位横盘的正常缩量排除掉'},
        {'name': '3 高潮',
         'expr': f"(量比 ≥ {DEFAULTS['climax_vr']} 或 10 日内放量天数 ≥ "
                 f"{DEFAULTS['climax_days']}) 且 close/HHV(close,20) ≥ "
                 f"{DEFAULTS['climax_high']} 且 站上 MA5",
         'note': '天天大红量柱且价格在高位。放量但价格在半山腰是反弹不是主升'},
        {'name': '2 酝酿',
         'expr': f"量比 ∈ [{DEFAULTS['incub_vr_lo']}, {DEFAULTS['incub_vr_hi']}]"
                 f" 且 量斜率 > 0 且 实体占比 ≥ {DEFAULTS['incub_solid']}"
                 ' 且 站上 MA5',
         'note': '温和放量：量一天一天变大，K 线扎实。量比高于 2 说明市场都'
                 '看见了、不再是"悄然进场"；长上影的放量是试盘或出货'},
        {'name': '5 退潮',
         'expr': '跌破 MA10 且 量斜率 < 0 且 '
                 f"vol/MA(vol,20) < {DEFAULTS['ebb_vr20']}",
         'note': '价跌破 10 日线且量在收缩'},
        {'name': '1 冷却',
         'expr': '以上都不成立',
         'note': '量在 20 日均量以下，没人管'},
    ),
    'combine': '★判定顺序固定 4 → 3 → 2 → 5 → 1，**不能改**。背离必须最先判：'
               '缩量新高同时也满足"价格在高位"，若先判高潮，最该警告的那一类'
               '会被标成"持有" —— 这正是这套判别唯一的价值，按涨幅排序永远'
               '排不出 4 和 3 的区别。',
    'caveat': '中间量算不出来的（上市不足 20 日）留空，不判成"冷却"。'
              '阈值全部走 config.binding.params，改数不用改代码。'
              '量能中间量与 vol_temp 共用 vol_phase.EXPRS，口径不会分叉。',
}



def run(ctx, params: dict) -> pd.Series:
    p = dict(DEFAULTS)
    for k, v in (params or {}).items():
        if k in p:
            p[k] = float(v)

    m = metrics(ctx)
    vr5 = pd.to_numeric(m['vr5'], errors='coerce')
    vr20 = pd.to_numeric(m['vr20'], errors='coerce')
    ramp = pd.to_numeric(m['ramp'], errors='coerce')
    days = pd.to_numeric(m['chixu_days'], errors='coerce')
    solid = pd.to_numeric(m['solid'], errors='coerce')
    peak = pd.to_numeric(m['peak_ratio'], errors='coerce')
    high = pd.to_numeric(m['high_ratio'], errors='coerce')
    pslope = pd.to_numeric(m['pslope'], errors='coerce')
    ma5 = pd.to_numeric(m['above_ma5'], errors='coerce')
    ma10 = pd.to_numeric(m['above_ma10'], errors='coerce')

    # 背离：价在新高 + 量接不上 + 量能斜率朝下。三条同时成立才算，
    # 只看"缩量新高"会把高位横盘的正常缩量也算进来，所以要 pslope > 0
    diverge = (high >= p['div_high']) & (peak <= p['div_peak']) & \
              (ramp < 0) & (pslope > 0)
    climax = ((vr5 >= p['climax_vr']) | (days >= p['climax_days'])) & \
             (high >= p['climax_high']) & (ma5 >= 1.0)
    incub = (vr5 >= p['incub_vr_lo']) & (vr5 <= p['incub_vr_hi']) & \
            (ramp > 0) & (solid >= p['incub_solid']) & (ma5 >= 1.0)
    ebb = (ma10 < 1.0) & (ramp < 0) & (vr20 < p['ebb_vr20'])

    out = pd.Series(1.0, index=ctx.codes)
    # 从低优先级往高优先级覆盖，最终等价于 4 → 3 → 2 → 5 → 1 的判定顺序
    out = out.mask(ebb.reindex(ctx.codes, fill_value=False), 5.0)
    out = out.mask(incub.reindex(ctx.codes, fill_value=False), 2.0)
    out = out.mask(climax.reindex(ctx.codes, fill_value=False), 3.0)
    out = out.mask(diverge.reindex(ctx.codes, fill_value=False), 4.0)

    # 中间量算不出来的（上市不足 20 日）不给阶段，留空而不是判成"冷却"
    known = vr5.notna() & ramp.notna() & peak.notna()
    return out.where(known.reindex(ctx.codes, fill_value=False)).round(0)
