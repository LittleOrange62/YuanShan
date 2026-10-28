# -*- coding: utf-8 -*-
"""
积木管理 —— 十三张 config.* 注册表的统一维护页。

这一页是"积木城堡"的入口：功能点（插槽）、脚本（积木）、绑定（插法）都是
表里的行，在这里点一下就能改，不用改代码、不用重启。

三个关键设计：

1. **校验先行**。`config` 改动会直接影响跑分结果，所以顶部常驻校验结果：
   权重没归一化、功能点没有启用的绑定、依赖成环、字段口径与实际列不符 ——
   有问题先看见，而不是等跑分出来是错的再回头查。

2. **权重自动归一**。`config.v_effective_binding` 视图对 enabled 的行做归一化，
   所以关掉一个子因子，剩下的权重自动按比例补上，不需要手动把总和凑成 1。
   页面上同时显示 raw_weight 与 eff_weight，让这件事是看得见的。

3. **改动留痕可回滚**。每张 config 表都挂了 ops.trg_config_history 触发器，
   改前改后的整行 JSON 都留着，所以"改坏了退回去"是一次点击的事。
"""
from __future__ import annotations

import json

import dash_bootstrap_components as dbc
from dash import MATCH, Input, Output, State, dcc, html, no_update

from app.db import engine
from app.db.repo import registry as reg_repo
from app.web import ui
from app.web.pages import builder, helpedit

# 展示名 → 表名。顺序按"从插槽到插法"的心智模型排，不按字母序。
TABS = [
    ('功能点 capability', 'capability', '系统里的插槽：一个可被替换实现的能力'),
    ('脚本 script', 'script', '积木本身：一段可执行的实现'),
    ('绑定 binding', 'binding', '插法：哪个功能点用哪些脚本、各占多少权重'),
    ('指标 indicator', 'indicator', '可在页面上写公式的派生指标'),
    ('作业 job', 'job', '什么时候触发、依赖谁'),
    ('数据集 dataset', 'dataset', '数据就绪的判定单位'),
    ('数据源 data_source', 'data_source', '数据从哪来'),
    ('字段口径 field_spec', 'field_spec', '单位与口径的唯一定义处'),
    ('质量规则 quality_rule', 'quality_rule', '落库与产出的校验规则'),
    ('筛选规则 screen_rule', 'screen_rule', '筛选器里的可选条件'),
    ('图表 chart', 'chart', '图表类型与取数命名查询'),
    ('页面布局 page_layout', 'page_layout', '每页摆哪些卡片、多宽'),
    ('菜单 menu', 'menu', '导航栏的内容与顺序'),
    ('通知渠道 notify_channel', 'notify_channel', '推送到哪里'),
    ('通知规则 notify_rule', 'notify_rule', '什么条件下推送'),
    ('杂项配置 setting', 'setting', '不值得单独建表的键值配置'),
    ('界面说明 help_note', 'help_note',
     '每个窗口那个「说明」的正文（原始行）。改内容用上面的「② 说明维护」'),
]

SEVERITY_COLOR = {'error': 'danger', 'warning': 'warning', 'info': 'secondary'}


def _validate_panel():
    try:
        issues = reg_repo.validate()
    except Exception as exc:  # noqa: BLE001
        return ui.alert(f'校验执行失败：{exc}', 'danger')
    if not issues:
        return ui.alert('配置校验全部通过：权重已归一、无空绑定、无依赖成环、'
                        '字段口径与实际列一致。', 'success')
    errors = [i for i in issues if i.get('level') == 'error']
    warns = [i for i in issues if i.get('level') != 'error']
    body = []
    for group, label in ((errors, '必须修'), (warns, '建议看')):
        if not group:
            continue
        body.append(html.Div(f'{label}（{len(group)}）',
                             className='fw-semibold small mt-1'))
        body.append(html.Ul([
            html.Li([dbc.Badge(i.get('level', 'info'),
                               color=SEVERITY_COLOR.get(i.get('level'), 'secondary'),
                               className='me-2'),
                     html.Span(f"{i.get('target', '')} — {i.get('message', '')}")],
                    className='small')
            for i in group[:40]]))
    return dbc.Alert(body, color='danger' if errors else 'warning',
                     className='py-2')


def _weights_panel():
    rows = engine.query(
        """
        SELECT cap_key AS 功能点, count(*) AS 绑定数,
               round(sum(eff_weight), 4) AS 有效权重合计,
               string_agg(script_key || '=' || round(eff_weight, 3)::text, '  '
                          ORDER BY seq) AS 明细
          FROM config.v_effective_binding GROUP BY cap_key ORDER BY cap_key
        """)
    return ui.table(rows, page_size=12)


def _rows_for(table: str) -> list[dict]:
    try:
        return reg_repo.list_rows(table)
    except Exception as exc:  # noqa: BLE001
        return [{'错误': str(exc)}]


def _history_panel(limit: int = 30):
    # 列名是 row_key 不是 pk。写错列名时这一整块会 500，而 /blocks 页本身
    # 照样打得开（面板是回调填的、不是 render() 里出的）—— 所以"页面能开"
    # 不等于"页面能用"，自检必须连回调一起打。
    rows = engine.query(
        """
        SELECT id, at, actor, table_name AS 表, op AS 操作,
               coalesce(row_key, '—') AS 主键
          FROM ops.config_history ORDER BY id DESC LIMIT %s
        """, (limit,))
    if not rows:
        return html.Div('还没有配置改动记录。任何 config.* 的增删改都会自动落这里。',
                        className='text-muted small p-2')
    return html.Div([
        ui.table(rows, page_size=10),
        html.Div([
            dbc.InputGroup([
                dbc.InputGroupText('回滚到改动前'),
                dbc.Input(id='blk-revert-id', type='number',
                          placeholder='填上面的 id'),
                dbc.Button('回滚', id='blk-revert-btn', color='warning'),
            ], size='sm', className='mt-2'),
            html.Div(id='blk-revert-msg', className='small mt-1'),
        ]),
    ])


def render():
    tabs = [
        # 搭建器放第一个：这一页最常做的事是"加个页面/加张卡片"，
        # 而不是翻某张注册表的原始行。
        dbc.Tab(label='① 页面搭建', tab_id='builder',
                children=html.Div(builder.panel(), className='pt-2')),
        # 说明维护紧跟其后：加完卡片下一件事就是给它写说明，
        # 而且这一块是**带缺口清单**的，能直接看出还有哪些窗口没写。
        dbc.Tab(label='② 说明维护', tab_id='help',
                children=html.Div(helpedit.panel(), className='pt-2')),
    ] + [
        dbc.Tab(label=label, tab_id=table,
                children=html.Div([
                    html.Div(desc, className='text-muted small py-2'),
                    html.Div(id={'kind': 'blk-table', 'table': table}),
                ]))
        for label, table, desc in TABS
    ]
    return html.Div([
        dbc.Row([
            dbc.Col(html.Div(id='blk-validate'), width=12, lg=8),
            dbc.Col(dbc.Button('重新校验配置', id='blk-validate-btn',
                               color='primary', size='sm', className='mt-1'),
                    width=12, lg=4, className='text-lg-end'),
        ], className='g-2'),
        dbc.Row([
            dbc.Col(ui.card('有效权重（关掉子项会自动重新归一）',
                            html.Div(id='blk-weights'), height=300,
                            help_key='blocks.weights'),
                    width=12, lg=6, className='mb-3'),
            dbc.Col(ui.card('改动历史（可回滚）', html.Div(id='blk-history'),
                            height=300, help_key='blocks.history'),
                    width=12, lg=6, className='mb-3'),
        ], className='g-3'),
        html.Hr(),
        dbc.Tabs(tabs, id='blk-tabs', active_tab='builder'),
        dcc.Store(id='blk-refresh', data=0),
    ])


def register_callbacks(app):
    builder.register_callbacks(app)
    helpedit.register_callbacks(app)


    @app.callback(Output('blk-validate', 'children'),
                  Output('blk-weights', 'children'),
                  Output('blk-history', 'children'),
                  Input('blk-validate-btn', 'n_clicks'),
                  Input('blk-refresh', 'data'))
    def _refresh(_n, _r):
        return _validate_panel(), _weights_panel(), _history_panel()

    @app.callback(Output({'kind': 'blk-table', 'table': MATCH}, 'children'),
                  Input('blk-tabs', 'active_tab'),
                  State({'kind': 'blk-table', 'table': MATCH}, 'id'))
    def _fill(active, comp_id):
        # 同样是 wildcard（MATCH）输出，返回值必须包成列表，否则 Dash 3 抛
        # InvalidCallbackReturnValue → 500，表现成"点了标签页永远空白"。
        if not active or not comp_id or comp_id['table'] != active:
            return [no_update]
        rows = _rows_for(active)
        note = ('这张表被标记为 protected：改它会影响系统行为，改动都有历史可回滚。'
                if active in getattr(reg_repo, 'PROTECTED', set()) else '')
        return [html.Div([
            html.Div(f'{len(rows)} 行' + ('　·　' + note if note else ''),
                     className='text-muted small mb-1'),
            ui.table(rows, page_size=20),
        ])]

    @app.callback(Output('blk-revert-msg', 'children'),
                  Output('blk-refresh', 'data'),
                  Input('blk-revert-btn', 'n_clicks'),
                  State('blk-revert-id', 'value'),
                  State('blk-refresh', 'data'),
                  prevent_initial_call=True)
    def _revert(_n, hid, ver):
        if not hid:
            return '先填要回滚的历史 id。', no_update
        try:
            reg_repo.revert_config(int(hid))
        except Exception as exc:  # noqa: BLE001
            return f'回滚失败：{exc}', no_update
        return f'已回滚历史记录 {hid}。', (ver or 0) + 1


def export_json() -> str:
    """把当前全部配置导出成 JSON，方便备份或搬到另一台机器。"""
    return json.dumps(reg_repo.export_config(), ensure_ascii=False, indent=2,
                      default=str)
