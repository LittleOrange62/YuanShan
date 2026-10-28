# -*- coding: utf-8 -*-
"""
Dash 应用装配。

导航栏读 config.menu，页面布局读 config.page_layout —— 加一页、挪一张卡片都是
改表，不重启不改代码。

安全提示：本应用**当前没有登录**，因此有两道地址层的闸门 ——
① 默认只绑 127.0.0.1（`app/web/run.py` 读 `config.setting.web_bind_host`）；
② 无论绑在哪，请求都要过 IP 白名单（`app/web/access.py`，loopback 永久放行）。
旧系统 `sql_api_auth_enabled` 默认 False 又监听 0.0.0.0，等于把数据库放在局域网上
任人取用，不重复这个错误。
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, State, dcc, html

from app import config
from app.db import engine
from app.db.repo import registry as reg_repo
from app.web import access, ui
from app.web import help as help_repo
from app.web import pages as pages_mod

BRAND = 'FinanceTool · 股票复盘'

# 侧栏宽度。原来是 Bootstrap 栅格 `lg=2`（占 12 列里的 2 列，1920 屏上约 320px），
# 四个字的菜单名根本用不了那么宽，正文却被挤窄。改成**定宽 150px**（约等于原来
# 的一半）＋ 正文 `flex-grow` 吃掉剩下全部：栅格是按比例分的，屏幕越宽侧栏越浪费，
# 定宽不会。
NAV_WIDTH = '150px'




def _menu_items() -> list[dict]:
    try:
        return reg_repo.menu()
    except Exception:  # noqa: BLE001
        # 库连不上时导航栏不能直接白屏，给出最小可用入口
        return [{'menu_key': 'home', 'label': '复盘首页', 'route': '/',
                 'group_name': '复盘', 'seq': 10}]


def _sidebar(active: str = '/'):
    items = _menu_items()
    groups: dict[str, list[dict]] = {}
    for m in items:
        groups.setdefault(m.get('group_name') or '其他', []).append(m)

    blocks = []
    for gname, ms in groups.items():
        blocks.append(html.Div(gname, className='text-muted mt-2 mb-1 ps-1',
                               style={'fontSize': '0.72rem'}))
        blocks.append(dbc.Nav(
            [dbc.NavLink(m['label'], href=m['route'], active=(m['route'] == active),
                         className='py-1 px-2 rounded small text-nowrap')
             for m in sorted(ms, key=lambda x: x.get('seq') or 100)],
            vertical=True, pills=True))
    return html.Div(blocks, className='pe-1')



def _health_badge():
    h = engine.healthcheck()
    if h.get('ok'):
        return dbc.Badge('DB 正常 · ' + h['version'].replace('PostgreSQL ', 'PG '),
                         color='success', className='ms-2')
    return dbc.Badge('DB 异常：' + str(h.get('error'))[:60], color='danger',
                     className='ms-2')


def create_app() -> Dash:
    app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP,
                                               dbc.icons.BOOTSTRAP],
               suppress_callback_exceptions=True, title=BRAND,
               update_title='加载中…')
    engine.set_app_name('ft_web')
    # IP 白名单闸门。挂在 Flask 上而不是 Dash 回调里：回调只覆盖 `/_dash-update`，
    # 页面本体、静态资源、以后可能加的任何路由都不会经过它。
    access.install(app.server, port=config.WEB_PORT)


    app.layout = dbc.Container([
        dcc.Location(id='url'),
        # 收起状态存在浏览器 localStorage 里：收起来之后翻页、刷新都还是收着的，
        # 不然每点一个菜单又弹出来，等于没收。
        dcc.Store(id='nav-open', data=True, storage_type='local'),
        dbc.Row([
            dbc.Col(dbc.Button(html.I(className='bi bi-list'), id='nav-toggle',
                               color='light', size='sm', n_clicks=0,
                               title='收起 / 展开左侧菜单'),
                    width='auto', className='pe-0'),
            dbc.Col(html.H5(BRAND, className='mb-0 py-2'), width='auto'),
            dbc.Col(html.Div(id='health-slot'), width='auto',
                    className='d-flex align-items-center'),
            dbc.Col(html.Div(id='page-title', className='text-muted small py-2'),
                    className='text-end'),
        ], className='border-bottom align-items-center g-2'),
        dbc.Row([
            # 定宽 + 不参与栅格（width='auto'）；正文那列给 `col` 让它吃掉剩余宽度。
            dbc.Col(html.Div(id='sidebar'), id='sidebar-col', width='auto',
                    className='border-end pt-2',
                    style={'width': NAV_WIDTH}),
            dbc.Col(dcc.Loading(html.Div(id='page-content', className='pt-3'),
                                type='default'),
                    className='col'),
        ], className='g-3 flex-nowrap'),
    ], fluid=True)

    @app.callback(Output('nav-open', 'data'),
                  Input('nav-toggle', 'n_clicks'),
                  State('nav-open', 'data'),
                  prevent_initial_call=True)
    def _toggle_nav(_n, open_):
        return not open_

    @app.callback(Output('sidebar-col', 'style'),
                  Input('nav-open', 'data'))
    def _nav_style(open_):
        """收起＝整列 `display:none`，正文那列是 flex-grow 的，会自动铺满。"""
        if open_ is False:
            return {'display': 'none'}
        return {'width': NAV_WIDTH}


    @app.callback(Output('sidebar', 'children'),
                  Output('page-content', 'children'),
                  Output('page-title', 'children'),
                  Output('health-slot', 'children'),
                  Input('url', 'pathname'), Input('url', 'search'))
    def _route(pathname, search):
        path = pathname or '/'
        render, subtitle = pages_mod.resolve(path)
        try:
            # `search` 是 `?code=600519.SH` 这种查询串。只有声明了 params 的页面
            # 会收到它（见 pages.call），所以带查询串的深链不会影响其它页面。
            body = pages_mod.call(render, search)
        except Exception as exc:  # noqa: BLE001
            body = ui.alert(f'页面渲染失败：{exc}', 'danger')
        # 抬头右上角挂**整页说明**（`config.help_note` 里 scope='page' 那一条）。
        # 放在这一处而不是每个页面自己写：14 个页面里只有 3 个曾经有过顶部说明，
        # 靠"每加一页记着写"是行不通的 —— 挂在路由上，页面级说明就只有一个开关。
        head = [html.Span(subtitle),
                ui.note_badge(help_repo.for_page(path), note_id='note-page',
                              label='本页说明', placement='left')]
        return _sidebar(path), body, head, _health_badge()

    pages_mod.register_all(app)

    return app



app = None


def get_app() -> Dash:
    global app
    if app is None:
        config.ensure_dirs()
        app = create_app()
    return app
