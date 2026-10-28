# -*- coding: utf-8 -*-
"""
页面合并后的不变量。

2026-08-30 撤掉了两个路由：`/market` 并进复盘首页、`/quality` 并进 `/data`。
"撤一个页面"要同时改四处（菜单、布局/代码、路由表、整页说明），漏一处的表现
各不相同：菜单还在但点开空白、路由没了但菜单还在、说明还在讲一个不存在的页。
所以这里按"结果"断言，不按"改了哪几行"断言。
"""
from __future__ import annotations

from app.db import engine
from app.web import pages as pages_mod
from app.web.pages import data


def _walk(node):
    yield node
    ch = getattr(node, 'children', None)
    if isinstance(ch, (list, tuple)):
        for c in ch:
            yield from _walk(c)
    elif ch is not None:
        yield from _walk(ch)


def test_retired_routes_are_gone_everywhere():
    for route, menu_key in (('/market', 'market'), ('/quality', 'quality')):
        assert route not in pages_mod.PAGES, f'{route} 还在路由表里'
        assert not engine.scalar(
            'SELECT count(*) FROM config.menu WHERE menu_key = %s OR route = %s',
            (menu_key, route)), f'{route} 还在菜单里'
        assert not engine.scalar(
            "SELECT count(*) FROM config.help_note "
            " WHERE scope = 'page' AND target = %s", (route,)), \
            f'{route} 的整页说明还在，但页面已经不存在'


def test_home_carries_the_fund_cards_and_task_list_last():
    rows = {r['slot']: r for r in engine.query(
        "SELECT slot, card_kind, card_ref FROM config.page_layout "
        " WHERE page_key = 'home' AND visible ORDER BY slot")}
    # 原 /market 的四张卡
    assert [rows[s]['card_ref'] for s in (16, 17, 18, 19)] == [
        'index_line', 'q_etf_flow', 'q_margin_trend', 'q_lhb_recent']
    # 今日任务在最后一张，且说明按「页面.槽位」跟着它走
    assert rows[max(rows)]['card_kind'] == 'task_list'
    assert engine.scalar(
        "SELECT count(*) FROM config.help_note WHERE target = %s",
        (f'home.{max(rows)}',)) == 1


def test_data_page_has_both_tabs():
    labels = [n.label for n in _walk(data.render())
              if getattr(n, 'label', None)]
    assert labels == ['① 数据现状', '② 数据质量']
