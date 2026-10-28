# -*- coding: utf-8 -*-
"""
页面注册表。

分两类：

1. **专属页**（`PAGES`）—— 有按钮、有回调、有自己的交互逻辑，必须写代码。
2. **配置页** —— 只是把卡片摆在栅格里，交给 `generic.render(route)` 按
   `config.page_layout` 渲染，加页面不用改代码。

以前这里是一个硬编码字典，`config.menu` 有 15 项、字典只有 6 项，
剩下 9 项在侧栏点开显示"还没实现" —— 菜单是数据、页面是代码，
两边靠人记着对齐，结果就是对不齐。现在兜底走 generic，
只要菜单里有、卡片配了，页面就一定打得开。
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from urllib.parse import parse_qsl

from app.web.pages import (
    admin,
    blocks,
    dashboard,
    data,
    datasource,
    generic,
    jobs,
    schedule,
    scores,
    screen,
    sql_console,
    stock,
    watchlist,
)

PAGES: dict[str, dict] = {
    # 复盘首页就是唯一的复盘页：2026-08-29 把 /review 整个并进来了，
    # 所以它现在也带交易日选择和按卡片异步加载。
    '/': {'render': dashboard.render,
          'subtitle': '复盘首页 · 体检 · 可选交易日回看',
          'register_callbacks': dashboard.register_callbacks},
    '/stock': {'render': stock.render, 'subtitle': '个股详情 · K线 · 评分归因',
               'register_callbacks': stock.register_callbacks},
    '/watchlist': {'render': watchlist.render,
                   'subtitle': '自选股组分析 · 组内加权分 · 建组改组 · 加票移组',
                   'register_callbacks': watchlist.register_callbacks},
    '/jobs': {'render': jobs.render, 'subtitle': '任务与调度 · 作业 · 执行历史',
              'register_callbacks': jobs.register_callbacks},
    # /schedule 是从 /jobs 里分出来的：/jobs 讲"跑过什么"（ops.task），
    # 这一页讲"什么时候该跑、为什么没跑"（ops.schedule_state + schedule_log）。
    '/schedule': {'render': schedule.render,
                  'subtitle': '调度器 · 触发判定 · 决策流水',
                  'register_callbacks': schedule.register_callbacks},
    # /data 2026-08-30 并进了原来的 /quality「数据质量」，页内分两个标签。
    # quality.py 还在，但它只提供 `panel()`，不再是一个路由。
    # 有回调：② 数据质量那半截会在打开页面时自动补齐落后的质量审计。
    '/data': {'render': data.render,
              'subtitle': '数据加工 · 批次与覆盖度 · 数据质量与口径',
              'register_callbacks': data.register_callbacks},
    '/screen': {'render': screen.render,
                'subtitle': '筛选器 · 条件组合 · 方案',
                'register_callbacks': screen.register_callbacks},
    # 2026-08-29 从配置页升级成专属页：散点图看得见"右上角有一堆"，
    # 但点不动。现在上半截是按象限选票框，下半截还是原来那几张卡。
    '/scores': {'render': scores.render,
                'subtitle': '全市场个股分析 · 四象限选票 · 双高 · 个股明细',
                'register_callbacks': scores.register_callbacks},
    # /datasource 2026-08-30 分两个标签：① 监控与采集（现在能不能用）、
    # ② 数据源管理（该先用谁 —— 层级/职责/费用/定位/启用，就地改就落库）。
    '/datasource': {'render': datasource.render,
                    'subtitle': '数据获取 · 监控与采集 · 层级与配置',
                    'register_callbacks': datasource.register_callbacks},
    '/blocks': {'render': blocks.render, 'subtitle': '积木管理 · 注册表 · 页面搭建',
                'register_callbacks': blocks.register_callbacks},
    '/sql': {'render': sql_console.render, 'subtitle': 'SQL 查询台 · 只读',
             'register_callbacks': sql_console.register_callbacks},
    '/admin': {'render': admin.render, 'subtitle': '系统管理 · 用户与角色',
               'register_callbacks': admin.register_callbacks},
}

# 兜底页面的副标题：配置驱动的页没有专属模块，副标题从 config.menu 的 label 取。
GENERIC_SUBTITLE = '配置驱动页面 · 卡片来自 config.page_layout'


def resolve(route: str) -> tuple[Callable, str]:
    """
    路由 → (渲染函数, 副标题)。**永远返回可调用的东西**，不返回 None ——
    路由解析不该有"找不到"这个分支，找不到专属页就是配置页。
    """
    page = PAGES.get(route)
    if page is not None:
        return page['render'], page.get('subtitle', '')
    return (lambda: generic.render(route)), GENERIC_SUBTITLE


def call(render: Callable, search: str | None = None):
    """
    调用页面渲染函数，**只在它自己声明了 `params` 时**才把 URL 查询串传进去。

    为什么用 inspect 而不是给所有页面统一加参数：11 个页面里只有 /stock 需要
    深链（`/stock?code=600519.SH`，见 `ui.detail_link`），为了一个页面把另外
    十个的签名都改一遍、以后每加一页都得记着带上这个参数，代价更大。
    """
    if 'params' not in inspect.signature(render).parameters:
        return render()
    return render(params=dict(parse_qsl((search or '').lstrip('?'))))


def register_all(app) -> None:
    for page in PAGES.values():
        if page.get('register_callbacks'):
            page['register_callbacks'](app)
