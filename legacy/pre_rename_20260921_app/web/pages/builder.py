# -*- coding: utf-8 -*-
"""
页面搭建器 —— "积木城堡"里真正动手搭的那一块。

在这里做的事：新建一个菜单项、给它加几张卡片、调宽高顺序，保存后刷新
就是一个新页面。不改代码、不重启、不需要写 Python。

底座是 `app/web/pages/generic.py`：任何 `config.menu` 的路由只要在
`config.page_layout` 里有卡片行就会被渲染出来。所以"加页面"这件事被拆成了
两条 INSERT，而这一页就是那两条 INSERT 的表单。

**卡片能引用什么是有边界的**：`chart` 只能选 `config.chart` 里已登记的图，
`table`/`stat`/`score_panel`/`task_list` 只能选 `queries.py` 里注册过的
命名查询。下拉框就是按这两个来源生成的 —— 不给自由文本输入，
因为自由文本等于允许配置里塞任意 SQL，那就把 `/blocks` 变成了远程执行入口。
"""
from __future__ import annotations

import json

import dash_bootstrap_components as dbc
from dash import Input, Output, State, html, no_update

from app.db import engine
from app.db.repo import registry as reg_repo
from app.web import queries, ui

# 卡片类型 → 这种卡片的 card_ref 该从哪里挑
KIND_SOURCE = {
    'stat': 'query',
    'chart': 'chart',
    'table': 'query',
    'score_panel': 'query',
    'task_list': 'query',
    'markdown': 'none',
    'iframe': 'text',
}

KIND_LABEL = {
    'stat': 'stat 指标块（取查询第一行的 value）',
    'chart': 'chart 图表（引用 config.chart）',
    'table': 'table 表格（引用命名查询）',
    'score_panel': 'score_panel 评分归因条',
    'task_list': 'task_list 任务列表',
    'markdown': 'markdown 说明文字',
    'iframe': 'iframe 外部页面',
}


def _menu_options() -> list[dict]:
    return [{'label': f"{m['label']}（{m['route']}）", 'value': m['menu_key']}
            for m in reg_repo.menu()]


def _ref_options(kind: str) -> list[dict]:
    src = KIND_SOURCE.get(kind, 'query')
    if src == 'chart':
        return [{'label': f"{c['chart_key']} · {c['name']}",
                 'value': c['chart_key']} for c in reg_repo.charts()]
    if src == 'query':
        return [{'label': n, 'value': n} for n in queries.names()]
    return []


def _cards_table(page_key: str | None):
    if not page_key:
        return html.Div('先在上面选一个页面', className='text-muted p-2')
    rows = engine.query(
        """
        SELECT slot AS 位置, card_kind AS 类型, card_ref AS 引用, title AS 标题,
               width AS 宽度, height AS 高度, visible AS 显示
          FROM config.page_layout WHERE page_key = %s ORDER BY slot
        """, (page_key,))
    if not rows:
        return html.Div(f'页面 {page_key} 还没有卡片。用下面的表单加第一张。',
                        className='text-muted p-2')
    return ui.table(rows, page_size=15)


def _add_card_form():
    return dbc.Row([
        dbc.Col(dbc.Select(id='bld-kind', size='sm', value='table',
                           options=[{'label': v, 'value': k}
                                    for k, v in KIND_LABEL.items()]),
                width=12, lg=3),
        dbc.Col(dbc.Select(id='bld-ref', size='sm',
                           options=_ref_options('table')), width=12, lg=3),
        dbc.Col(dbc.Input(id='bld-title', placeholder='卡片标题', size='sm'),
                width=12, lg=2),
        dbc.Col(dbc.InputGroup([
            dbc.InputGroupText('宽', className='py-1'),
            dbc.Input(id='bld-width', type='number', value=6, min=1, max=12,
                      size='sm'),
            dbc.InputGroupText('高', className='py-1'),
            # 「高」只对图表/文本卡有意义：表格卡的高度由 options.page_size 决定
            # （一页几行就画多高），填在这里不生效 —— 见 ui.render_card。
            dbc.Input(id='bld-height', type='number', value=340, min=100,
                      max=900, step=20, size='sm'),
        ]), width=12, lg=3),
        dbc.Col(dbc.Button('加卡片', id='bld-add', color='primary', size='sm',
                           n_clicks=0), width='auto'),
    ], className='g-2')


def _new_menu_form():
    groups = sorted({m['group_name'] for m in reg_repo.menu() if m['group_name']})
    return dbc.Row([
        dbc.Col(dbc.Input(id='bld-mkey', placeholder='menu_key，如 mytab',
                          size='sm'), width=12, lg=2),
        dbc.Col(dbc.Input(id='bld-mlabel', placeholder='菜单名，如 我的看板',
                          size='sm'), width=12, lg=2),
        dbc.Col(dbc.Input(id='bld-mroute', placeholder='路由，如 /mytab',
                          size='sm'), width=12, lg=2),
        dbc.Col(dbc.Select(id='bld-mgroup', size='sm',
                           value=groups[0] if groups else '复盘',
                           options=[{'label': g, 'value': g} for g in groups]
                           or [{'label': '复盘', 'value': '复盘'}]),
                width=12, lg=2),
        dbc.Col(dbc.InputGroup([
            dbc.InputGroupText('排序', className='py-1'),
            dbc.Input(id='bld-mseq', type='number', value=200, size='sm'),
        ]), width=12, lg=2),
        dbc.Col(dbc.Button('建菜单', id='bld-mnew', color='success', size='sm',
                           n_clicks=0), width='auto'),
    ], className='g-2')


def panel():
    """整块面板，供 blocks 页当成一个 Tab 塞进去。"""
    return html.Div([
        ui.alert('这一页搭出来的东西马上生效：加完卡片刷新浏览器，'
                 '左边导航点进去就能看到。卡片引用只能从下拉里挑 —— '
                 '不开自由文本，避免把配置变成任意 SQL 入口。', 'light'),
        dbc.Row([
            dbc.Col([
                html.Div('① 选一个已有页面', className='fw-semibold small mb-1'),
                dbc.Select(id='bld-page', size='sm', options=_menu_options(),
                           value='home'),
                html.Div('② 这一页现在的卡片', className='fw-semibold small mt-3 mb-1'),
                html.Div(_cards_table('home'), id='bld-cards'),
                html.Div('③ 加一张卡片', className='fw-semibold small mt-3 mb-1'),
                _add_card_form(),
                dbc.Row([
                    dbc.Col(dbc.InputGroup([
                        dbc.InputGroupText('删除位置', className='py-1'),
                        dbc.Input(id='bld-del-slot', type='number', size='sm'),
                        dbc.Button('删掉这张卡', id='bld-del', color='danger',
                                   outline=True, size='sm', n_clicks=0),
                    ], size='sm'), width=12, lg=5),
                ], className='g-2 mt-2'),
                html.Div(id='bld-msg', className='small mt-2'),
            ], width=12),
        ]),
        html.Hr(),
        html.Div('④ 或者直接建一个新页面（先建菜单，再回到 ① 给它加卡片）',
                 className='fw-semibold small mb-1'),
        _new_menu_form(),
        html.Div(id='bld-mmsg', className='small mt-2'),
    ])


def _next_slot(page_key: str) -> int:
    n = engine.scalar('SELECT max(slot) FROM config.page_layout WHERE page_key = %s',
                      (page_key,))
    return 1 if n is None else int(n) + 1


def register_callbacks(app) -> None:
    @app.callback(Output('bld-ref', 'options'),
                  Output('bld-ref', 'disabled'),
                  Input('bld-kind', 'value'))
    def _refs(kind):
        opts = _ref_options(kind or 'table')
        return opts, not opts

    @app.callback(Output('bld-cards', 'children'),
                  Output('bld-msg', 'children'),
                  Input('bld-page', 'value'),
                  Input('bld-add', 'n_clicks'),
                  Input('bld-del', 'n_clicks'),
                  State('bld-kind', 'value'), State('bld-ref', 'value'),
                  State('bld-title', 'value'), State('bld-width', 'value'),
                  State('bld-height', 'value'), State('bld-del-slot', 'value'))
    def _edit(page_key, _add, _del, kind, ref, title, width, height, del_slot):
        from dash import callback_context
        trig = callback_context.triggered_id
        if trig == 'bld-add' and page_key:
            if KIND_SOURCE.get(kind) in ('chart', 'query') and not ref:
                return _cards_table(page_key), html.Span(
                    f'{kind} 卡片必须选一个引用', className='text-danger')
            slot = _next_slot(page_key)
            opts = {}
            if kind == 'markdown':
                opts['text'] = title or '在积木管理里改这段文字'
            try:
                reg_repo.upsert_row('page_layout', {
                    'page_key': page_key, 'slot': slot,
                    'width': int(width or 6), 'height': int(height or 340),
                    'card_kind': kind, 'card_ref': ref,
                    'title': title, 'options': json.dumps(opts, ensure_ascii=False),
                    'visible': True})
            except Exception as exc:  # noqa: BLE001
                return _cards_table(page_key), html.Span(f'加卡片失败：{exc}',
                                                         className='text-danger')
            return _cards_table(page_key), html.Span(
                f'已加到位置 {slot}。刷新页面就能看到。', className='text-success')

        if trig == 'bld-del' and page_key and del_slot:
            n = engine.execute(
                'DELETE FROM config.page_layout WHERE page_key = %s AND slot = %s',
                (page_key, int(del_slot)))
            return _cards_table(page_key), html.Span(
                f'删掉 {n} 张卡片' if n else '那个位置没有卡片',
                className='text-muted')

        return _cards_table(page_key), ''

    @app.callback(Output('bld-mmsg', 'children'),
                  Output('bld-page', 'options'),
                  Input('bld-mnew', 'n_clicks'),
                  State('bld-mkey', 'value'), State('bld-mlabel', 'value'),
                  State('bld-mroute', 'value'), State('bld-mgroup', 'value'),
                  State('bld-mseq', 'value'),
                  prevent_initial_call=True)
    def _new_menu(_n, key, label, route, group, seq):
        key = (key or '').strip()
        route = (route or '').strip()
        if not (key and label and route):
            return html.Span('menu_key、菜单名、路由都要填',
                             className='text-danger'), no_update
        if not route.startswith('/'):
            return html.Span('路由要以 / 开头', className='text-danger'), no_update
        try:
            reg_repo.upsert_row('menu', {
                'menu_key': key, 'label': label.strip(), 'route': route,
                'icon': 'grid-3x3', 'seq': int(seq or 200),
                'group_name': group or '复盘', 'visible': True})
        except Exception as exc:  # noqa: BLE001
            return html.Span(f'建菜单失败：{exc}',
                             className='text-danger'), no_update
        return (html.Span(f'已建菜单 {label}（{route}）。'
                          f'现在回到 ① 选「{label}」给它加卡片。',
                          className='text-success'),
                _menu_options())
