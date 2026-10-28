# -*- coding: utf-8 -*-
"""`GET /api/quant/sector-members`（板块成分明细）的口径测试。

这张表存在的理由很具体：右侧「AI分析」的预设题要按"股价位置/涨幅/市值"给题材内部
分层，而热度榜只到板块一级 —— 没有个股明细，模型只能回答"材料里没有个股"
（2026-09-27 实测到就是这个）。所以这里钉三件事：

1. 能按**板块代码**和**板块名**两种写法取到同一批成分；
2. 位置/涨幅/市值是**算出来的**且带分母（`bars`/`window_days`），不是拍出来的；
3. **成分没有时点**这条幸存者偏差必须随数据交出 —— 否则"按当前成分回看历史"
   会被读成"当时就是这么分组的"。
"""
from __future__ import annotations

import pytest

from duanxian import quant

pytestmark = pytest.mark.unit


def test_members_come_back_with_position_and_denominator():
    got = quant.sector_members(sector='诊断服务', limit=40)
    assert got['available'] is True, got
    rows = got['rows']
    assert rows, '诊断服务当天没有成分明细 = 取数断了，不是市场没票'
    first = rows[0]
    for key in ('code', 'name', 'close', 'pos60', 'gain60', 'float_mcap_yi', 'bars', 'sealed'):
        assert key in first, f'少了 {key}：{list(first)}'
    assert got['window_days'] == 60 and first['bars'] >= 1, '位置的分母（在库 K 线根数）必须一起给'
    # 排序就是这道题的用途：低位在前
    pos = [r['pos60'] for r in rows if r['pos60'] is not None]
    assert pos == sorted(pos), '没按 pos60 升序，模型拿到的"低位"就不是低位'


def test_sector_code_and_sector_name_agree():
    by_name = quant.sector_members(sector='诊断服务', limit=10)
    code = by_name['sector']
    by_code = quant.sector_members(sector=code, limit=10)
    assert [r['code'] for r in by_name['rows']] == [r['code'] for r in by_code['rows']]


def test_survivorship_caveat_travels_with_the_rows():
    got = quant.sector_members(sector='诊断服务', limit=5)
    text = ' '.join(str(c.get('text') or c) for c in (got.get('caveats') or []))
    assert '时点' in text or '幸存者' in text, f'成分无时点这条必须随数据交出：{text[:120]}'


def test_empty_sector_is_not_a_fake_success():
    for bad in ('', '不存在的板块名'):
        got = quant.sector_members(sector=bad)
        assert got.get('available') is not True, f'{bad!r} 不该报成功'
