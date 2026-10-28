# -*- coding: utf-8 -*-
"""
通用页面：按 `config.menu` + `config.page_layout` 渲染，**不写死任何一页**。

这是"积木城堡"真正落地的地方。以前 `PAGES` 是硬编码字典，`config.menu` 里
有 15 项、字典里只有 6 项，剩下 9 项在侧栏可点、点开是"还没实现" ——
菜单是数据、页面是代码，两边对不上就是这个结果。

现在反过来：路由先查专属页面白名单（那些带按钮、要写回调的页），
查不到就交给这里，按 `config.page_layout` 里那一页的卡片配置渲染出来。
于是新增一页 = 在 `/blocks` 里插一行菜单 + 几行卡片，不改代码不重启。

`page_key` 的取法：`config.menu.menu_key`。约定路由 `/sector` 对应
`menu_key='sector'`，所以配卡片时 `page_key` 填 `sector`。
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html

from app.db.repo import registry as reg_repo
from app.web import queries, ui


def page_key_of(route: str) -> str | None:
    """路由 → page_key。走 config.menu，不靠字符串裁剪。"""
    for m in reg_repo.menu():
        if m['route'] == route:
            return m['menu_key']
    return None


def _charts() -> dict[str, dict]:
    return {c['chart_key']: c for c in reg_repo.charts()}


def _empty_hint(route: str, key: str | None):
    """
    没配卡片时给的不是"未实现"，而是**怎么把它配出来**。

    这条提示是刻意写长的：这一页空着不是 bug 而是还没搭，
    看到提示的人应该知道下一步点哪里，而不是回来问"为什么打不开"。
    """
    if key is None:
        return ui.alert(
            f'路由 {route} 不在 config.menu 里。到「积木管理 → menu」加一行，'
            f'再到「page_layout」给它配卡片。', 'warning')
    return dbc.Card(dbc.CardBody([
        html.H6(f'这一页（page_key = {key}）还没有配卡片'),
        html.P(['到 ', dbc.Badge('积木管理', color='primary'),
                ' → page_layout 标签，用 page_key = ',
                html.Code(key), ' 加卡片，保存后刷新本页即可。'],
               className='mb-2 small'),
        html.P('卡片类型：stat 指标块 / chart 图表 / table 表格 / '
               'score_panel 评分归因 / task_list 任务列表 / '
               'markdown 说明 / iframe 外链', className='text-muted small mb-2'),
        html.Div([
            html.Div('可用的命名查询：', className='text-muted small'),
            html.Div(' '.join(queries.names()),
                     className='small font-monospace text-muted'),
        ]),
    ]), className='shadow-sm')


def render(route: str = '/', **params):
    key = page_key_of(route)
    rows = reg_repo.layout(key) if key else []
    if not rows:
        return _empty_hint(route, key)
    return ui.render_page(rows, _charts(), params=params or None)
