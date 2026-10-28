# -*- coding: utf-8 -*-
"""
系统管理：用户与角色、访问白名单、系统设置、当前安全状态。

这一页最重要的作用是**把安全状态说清楚**。`config.app_user` 空的时候系统是
无鉴权的，这不是 bug 而是单机自用的默认；真正的闸门在地址这一层：

    监听地址（config.setting.web_bind_host）  决定"谁能连到这个端口"
    IP 白名单（config.setting.web_allowlist）  决定"连上之后放不放进来"

两者独立，页面必须把组合关系讲明白 —— 只绑 127.0.0.1 时白名单里的局域网地址
根本连不上（不是白名单没生效），而绑了 0.0.0.0 时白名单是唯一的防线。
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, callback_context, dcc, html

from app.db import engine
from app.db.repo import registry as reg_repo
from app.web import access, auth, ui


def _security_panel():
    n = 0
    err = None
    try:
        n = auth.user_count()
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
    host = access.bind_host_running() or access.bind_host()
    want = access.bind_host()
    allow = access.allowlist(fresh=True)
    lan = host == '0.0.0.0'
    pend = ('' if want == host else
            f'　（已保存监听地址 {want}，重启后生效：'
            'python scripts\\web_ctl.py restart）')

    if err:
        rows = [ui.alert(f'读 config.app_user 失败：{err}', 'danger')]
    elif not lan:
        rows = [ui.alert(
            f'当前监听 {host} —— **只有本机能连**，局域网连不到这个端口，'
            f'白名单（{"、".join(allow) or "空"}）此时不起作用也不需要起作用。'
            f'单机自用保持这样最安全。{pend}', 'success')]
    elif allow:
        extra = ('；另外还没有任何账号，白名单内的机器打开就能用（无需登录）'
                 if n == 0 else f'；已有 {n} 个账号，登录门已生效')
        rows = [ui.alert(
            f'当前监听 0.0.0.0（局域网可达），放行名单：{"、".join(allow)}。'
            f'其余地址一律 403，本机（127.0.0.1）永久放行{extra}。{pend}',
            'warning')]
    else:
        rows = [ui.alert(
            '当前监听 0.0.0.0（局域网可达）但白名单是空的 —— 除本机外谁都进不来。'
            f'这不算危险，但如果你以为别的机器能用，那就是这里的问题。{pend}',
            'warning')]
    return html.Div(rows)




def _user_table():
    try:
        rows = auth.users()
    except Exception as exc:  # noqa: BLE001
        return ui.alert(f'读用户表失败：{exc}', 'danger')
    if not rows:
        return html.Div('还没有任何账号。下面建第一个（建议 role=admin）。',
                        className='text-muted p-3')
    head = html.Thead(html.Tr([html.Th(c) for c in (
        '状态', '用户名', '角色', '显示名', '创建时间', '最近登录', '操作')]))
    body = []
    for u in rows:
        body.append(html.Tr([
            html.Td(dbc.Badge('启用' if u['enabled'] else '停用',
                              color='success' if u['enabled'] else 'secondary')),
            html.Td(html.Code(u['username'])),
            html.Td(dbc.Badge(u['role'],
                              color='danger' if u['role'] == 'admin' else 'info')),
            html.Td(u['display_name'] or '—'),
            html.Td(str(u['created_at'])[:19], className='small text-muted'),
            html.Td(str(u['last_login_at'] or '从未')[:19],
                    className='small text-muted'),
            html.Td(dbc.ButtonGroup([
                dbc.Button('停用' if u['enabled'] else '启用',
                           id={'type': 'usr-toggle', 'name': u['username']},
                           size='sm', color='secondary', n_clicks=0),
                dbc.Button('删除',
                           id={'type': 'usr-del', 'name': u['username']},
                           size='sm', color='danger', outline=True, n_clicks=0),
            ], size='sm')),
        ]))
    return dbc.Table([head, html.Tbody(body)], hover=True, responsive=True,
                     size='sm', className='mb-0')


def _new_user_form():
    return dbc.Row([
        dbc.Col(dbc.Input(id='usr-name', placeholder='用户名', size='sm'),
                width=12, lg=3),
        dbc.Col(dbc.Input(id='usr-pass', placeholder='口令（至少 8 位）',
                          type='password', size='sm'), width=12, lg=3),
        dbc.Col(dbc.Select(id='usr-role', size='sm', value='admin',
                           options=[{'label': 'admin 管理员', 'value': 'admin'},
                                    {'label': 'user 普通用户', 'value': 'user'}]),
                width=12, lg=2),
        dbc.Col(dbc.Input(id='usr-display', placeholder='显示名（可空）',
                          size='sm'), width=12, lg=2),
        dbc.Col(dbc.Button('创建 / 重设口令', id='usr-save', color='primary',
                           size='sm', n_clicks=0), width='auto'),
    ], className='g-2')


def _client_ip() -> str | None:
    """当前请求的来源地址。渲染函数也在请求上下文里，所以这里能直接拿。"""
    try:
        from flask import request
        return request.remote_addr
    except Exception:  # noqa: BLE001
        return None


def _access_form():
    items = access.allowlist(fresh=True)
    ip = _client_ip()
    me = (html.Span(['你现在的来源地址：', html.Code(ip or '未知'),
                     '（本机 loopback 永久放行，不用写进白名单）'],
                    className='small text-muted')
          if ip else html.Span())
    return html.Div([
        dbc.Row([
            dbc.Col([
                html.Div('白名单（一行一条，支持单个 IP 或 CIDR 网段）',
                         className='small text-muted mb-1'),
                dbc.Textarea(id='acl-list', value='\n'.join(items), rows=5,
                             style={'fontFamily': 'monospace',
                                    'fontSize': '0.85rem'},
                             placeholder='192.168.176.67\n192.168.176.0/24'),
            ], width=12, lg=7),
            dbc.Col([
                html.Div('监听地址（改这一项要重启才生效）',
                         className='small text-muted mb-1'),
                dbc.Select(id='acl-bind', value=access.bind_host(), size='sm',
                           options=[
                               {'label': '127.0.0.1　仅本机（默认）',
                                'value': '127.0.0.1'},
                               {'label': '0.0.0.0　　本机 + 局域网（仍查白名单）',
                                'value': '0.0.0.0'}]),
                dbc.Button('保存', id='acl-save', color='primary', size='sm',
                           n_clicks=0, className='mt-2'),
            ], width=12, lg=5),
        ], className='g-3'),
        html.Div(me, className='mt-2'),
        html.Div(id='acl-msg', className='small mt-1'),
    ])


def _menu_roles():

    rows = [{'菜单': m['label'], 'key': m['menu_key'], '路由': m['route'],
             '分组': m['group_name'], '需要角色': m.get('need_role') or '所有人'}
            for m in reg_repo.menu()]
    return ui.table(rows, page_size=15)


def _settings():
    return ui.table(engine.query(
        'SELECT key AS 键, value::text AS 值, label AS 说明, category AS 分类 '
        'FROM config.setting ORDER BY category, key'), page_size=10)


def render():
    return html.Div([
        html.Div(_security_panel(), id='acl-state'),
        ui.card('访问白名单（谁能打开这个网页）', _access_form(), height=300,
                help_key='admin.access',
                footer='白名单立刻生效；监听地址要重启：'
                       'python scripts\\web_ctl.py restart'),
        ui.card('账号（config.app_user）', html.Div([

            dcc.Loading(html.Div(_user_table(), id='usr-table'), type='default'),
            html.Hr(),
            _new_user_form(),
            html.Div(id='usr-msg', className='small mt-2'),
        ]), height=520, help_key='admin.users',
            footer='口令用 argon2 存哈希，库里没有明文；本页不回显任何口令'),
        dbc.Row([
            dbc.Col(ui.card('菜单权限（config.menu.need_role）', _menu_roles(),
                            height=360, help_key='admin.menu_roles',
                            footer='改这一列到「积木管理 → menu」'),
                    width=12, lg=6, className='mb-3'),
            dbc.Col(ui.card('系统设置（config.setting）', _settings(), height=360,
                            help_key='admin.settings'),
                    width=12, lg=6, className='mb-3'),
        ], className='g-3'),
    ])


def register_callbacks(app) -> None:
    @app.callback(Output('acl-state', 'children'),
                  Output('acl-list', 'value'),
                  Output('acl-msg', 'children'),
                  Input('acl-save', 'n_clicks'),
                  State('acl-list', 'value'), State('acl-bind', 'value'),
                  prevent_initial_call=True)
    def _save_access(_n, text, bind):
        try:
            items = access.normalize(text or '')
        except ValueError as exc:
            # 一条写错就整批不保存：半批生效比不生效更难查
            return (_security_panel(), text,
                    html.Span(f'没有保存 —— {exc}', className='text-danger'))
        try:
            saved = access.save_allowlist(items)
            host = access.save_bind_host(bind or access.DEFAULT_BIND)
        except Exception as exc:  # noqa: BLE001
            return (_security_panel(), text,
                    html.Span(f'保存失败：{exc}', className='text-danger'))
        running = access.bind_host_running()
        tip = ('' if running is None or host == running
               else '　监听地址改了，要 python scripts\\web_ctl.py restart 才生效。')
        return (_security_panel(), '\n'.join(saved),
                html.Span(f'已保存 {len(saved)} 条，白名单已即时生效。{tip}',
                          className='text-success'))

    @app.callback(Output('usr-table', 'children'),

                  Output('usr-msg', 'children'),
                  Input('usr-save', 'n_clicks'),
                  Input({'type': 'usr-toggle', 'name': ALL}, 'n_clicks'),
                  Input({'type': 'usr-del', 'name': ALL}, 'n_clicks'),
                  State('usr-name', 'value'), State('usr-pass', 'value'),
                  State('usr-role', 'value'), State('usr-display', 'value'),
                  prevent_initial_call=True)
    def _act(_save, _toggles, _dels, name, password, role, display):
        trig = callback_context.triggered_id
        try:
            if trig == 'usr-save':
                if not (name or '').strip():
                    return _user_table(), html.Span('用户名不能为空',
                                                    className='text-danger')
                auth.create_user(name.strip(), password or '', role=role or 'user',
                                 display_name=display or None)
                msg = html.Span(f'已保存 {name.strip()}（{role}）。'
                                '登录门现在生效，下次打开需要登录。',
                                className='text-success')
            elif isinstance(trig, dict) and trig.get('type') == 'usr-toggle':
                target = trig['name']
                cur = {u['username']: u['enabled'] for u in auth.users()}
                auth.set_enabled(target, not cur.get(target, True))
                msg = html.Span(f'{target} 已切换启用状态', className='text-muted')
            elif isinstance(trig, dict) and trig.get('type') == 'usr-del':
                auth.delete_user(trig['name'])
                msg = html.Span(f"已删除 {trig['name']}", className='text-muted')
            else:
                msg = ''
        except Exception as exc:  # noqa: BLE001
            return _user_table(), html.Span(f'操作失败：{exc}',
                                            className='text-danger')
        return _user_table(), msg
