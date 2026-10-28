# -*- coding: utf-8 -*-
"""
个股质量·形态分项。

判据：突破形态（收盘接近近期高点）+ 振幅收敛（蓄势）+ 乖离适中（不过热）。
BIAS20 太大反而扣分 —— 涨过头的票形态再好也不是买点，这是旧系统
只看"越涨越高分"的反面。
"""
from __future__ import annotations

import pandas as pd

# 页面上「评分归因」那张卡的说明就是这个常量渲染出来的（app/compute/explain.py）。
# 写在脚本里而不是数据库里：这描述的是**代码怎么算**，不是可配项 ——
# 可配的那部分（分项权重、参数）在 config.binding 里，页面另外标出来。
# tests/test_explain.py 会核对 expr 与内部权重和下面的实现一致，改公式忘了改说明会红。
EXPLAIN = {
    'basis': '突破形态（收盘接近近期高点）+ 振幅收敛（蓄势）+ 乖离适中（不过热）',
    'terms': (
        {'name': '突破', 'w': 0.40,
         'expr': '(close - HHV(high,20)) / HHV(high,20) * 100',
         'norm': '截断映射 -15 ~ 0 → -100 ~ +100',
         'note': '离 20 日最高价越近越高分；低于 20 日高点 15% 以上一律记 -100'},
        {'name': '振幅收敛', 'w': 0.25,
         'expr': 'STD((high - low) / pre_close * 100, 20)',
         'norm': '全市场截面百分位（反向）→ -100 ~ +100',
         'note': '20 日振幅的标准差越小＝越蓄势。用百分位是因为振幅的绝对'
                 '水平随行情整体涨落，固定区间跨日不可比'},
        {'name': '乖离适中', 'w': 0.35,
         'expr': '-|(close - MA(close,20)) / MA(close,20) * 100 - 4|',
         'norm': '截断映射 -20 ~ 0 → -100 ~ +100',
         'note': 'BIAS20 落在 4% 附近满分，偏离 4% 越远越扣 —— 负太多是弱势，'
                 '正太多是过热。涨过头的票形态再好也不是买点'},
    ),
    'combine': '三项加权求和，落在 -100 ~ +100',
}


def run(ctx, params: dict) -> pd.Series:
    # 距 20 日高点的距离，越近说明越接近突破
    near_high = ctx.cs('(close - HHV(high,20)) / HHV(high,20) * 100')
    break_score = ctx.clip_score(near_high, -15.0, 0.0)

    # 振幅收敛：20 日振幅标准差小 = 蓄势，配合突破才有意义
    amp_std = ctx.cs('STD((high - low) / pre_close * 100, 20)')
    conv_score = ctx.pct_score(amp_std, reverse=True)

    # 乖离：BIAS20 在 0~8 之间最好，负太多是弱势，正太多是过热
    bias = ctx.cs('(close - MA(close, 20)) / MA(close, 20) * 100')
    over = (bias - 4.0).abs()
    bias_score = ctx.clip_score(-over, -20.0, 0.0)

    return (break_score * 0.4 + conv_score * 0.25 + bias_score * 0.35).round(4)
