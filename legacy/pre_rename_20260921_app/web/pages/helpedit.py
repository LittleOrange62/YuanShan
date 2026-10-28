# -*- coding: utf-8 -*-
"""
说明维护（`/blocks` 的一个 tab）：把 `config.help_note` 的字段摆出来改，改完当场预览。

为什么要有这一块：说明进库的意义就是**不用改代码也能改说明**。如果改一句话还要
写 SQL，那和写在代码里没差多少。

三个部分：

- **缺口清单**：哪些窗口还没有说明（`help.coverage()`）。点一行就把 target
  填进右边的表单，接着写就行。
- **字段表单**：`purpose / content / 取数三件套 / derive / formula / caveat`
  分开填，不是一个大文本框 —— 分开之后全站说明结构一致，也能查"哪些卡用了
  `fct.score`"。
- **预览**：按真实渲染逻辑（`help.render`）拼一遍，看到的就是页面上弹出来的样子。
  `includes` 引用的公共片段会一起展开，环引用会当场报错而不是悄悄少一段。
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html, no_update

from app.web import help as help_repo
from app.web import ui

# 表单里一行一个字段：(列名, 显示名, 行数, 占位提示)
TEXT_FIELDS = [
    ('title', '标题（页级说明会显示，卡片说明不显示）', 1, '板块热度'),
    ('purpose', '为什么有这个窗口（回答什么问题）', 2,
     '回答"今天谁在领涨、这股热度是在积累还是在退潮"'),
    ('content', '看什么、怎么读', 4, '五张卡的分工…'),
    ('source_query', '取数：命名查询', 1, 'q_sector_heat'),
    ('source_tables', '取数：库表（逗号分隔）', 1,
     'md.sector_daily, fct.sector_heat_daily'),
    ('script', '取数：脚本', 1, 'scripts/compute/d3_sector_trend.py'),
    ('derive', '二次加工：上游数据 → 谁算的', 3,
     '① vipdoc 板块日线 → ② 成员名单 → ③ d2_scores 逐票算分 → ④ d3 聚合'),
    ('formula', '参数与得分口径', 3,
     '热度 = 0.30 涨幅 + 0.30 广度 + 0.25 动量 + 0.15 成交，取值 −100 ~ +100'),
    ('caveat', '坑与已知缺口（自动加 ⚠️）', 2, '行业板块成员名单只覆盖 418/652'),
    ('includes', '引用的公共片段（逗号分隔 note_key）', 1,
     'snip.oper_score, snip.sector_heat'),
]


def _options() -> list[dict]:
    rows = help_repo.notes()
    return [{'label': f"{k}　·　{(r['scope'])}"
                      + (f"→{r['target']}" if r['target'] else ''),
             'value': k}
            for k, r in sorted(rows.items())]


def _gap_table():
    rows = [r for r in help_repo.coverage() if not r['has_note']]
    if not rows:
        return ui.alert('所有展示窗口都有说明了（库里）。', 'success')
    data = [{'类型': r['kind'], '位置': r['where'], 'target': r['target'],
             '标题': r['title'] or ''} for r in rows]
    return html.Div([
        ui.alert(f'还有 {len(rows)} 个窗口没有库里的说明 —— '
                 '代码兜底（ui.CARD_HELP）不算覆盖。', 'warning'),
        ui.table(data, id_='help-gap', page_size=10,
                 link_codes=False),
    ])


def panel():
    return html.Div([
        ui.alert('说明正文存在 `config.help_note`，**按字段拆开**：'
                 'purpose / content / 取数（命名查询·库表·脚本）/ 二次加工 / '
                 '口径 / 坑。渲染时按固定顺序拼成一段 markdown，全站一个样式。'
                 '公共口径（如操作分公式）写成 `snip.*` 片段，用 includes 引用，'
                 '改一处所有说明跟着变。保存即生效，不用重启。', 'light'),
        dbc.Row([
            dbc.Col(ui.card('缺口清单', html.Div(_gap_table(), id='help-gaps'),
                            height=380), width=12, lg=5, className='mb-3'),
            dbc.Col(ui.card('预览（页面上弹出来就是这个样子）',
                            html.Div(id='help-preview',
                                     className='small border rounded p-2'),
                            height=380), width=12, lg=7, className='mb-3'),
        ], className='g-3'),
        ui.card('编辑说明', html.Div([
            dbc.Row([
                dbc.Col(dbc.InputGroup([
                    dbc.InputGroupText('选一条', className='py-1'),
                    dbc.Select(id='help-pick', options=_options(), size='sm'),
                ]), width=12, lg=6),
                dbc.Col(dbc.InputGroup([
                    dbc.InputGroupText('note_key', className='py-1'),
                    dbc.Input(id='help-key', size='sm',
                              placeholder='card.页面.别名 / page./路由 / snip.名字'),
                ]), width=12, lg=6),
            ], className='g-2 mb-2'),
            dbc.Row([
                dbc.Col(dbc.InputGroup([
                    dbc.InputGroupText('scope', className='py-1'),
                    dbc.Select(id='help-scope', size='sm',
                               options=[{'label': v, 'value': v} for v in
                                        ('card', 'page', 'stat', 'snippet')],
                               value='card'),
                ]), width=12, lg=4),
                dbc.Col(dbc.InputGroup([
                    dbc.InputGroupText('target', className='py-1'),
                    dbc.Input(id='help-target', size='sm',
                              placeholder='card_ref / 路由 / 手写卡别名'),
                ]), width=12, lg=8),
            ], className='g-2 mb-2'),
            *[html.Div([
                html.Div(label, className='small text-muted'),
                dbc.Textarea(id=f'help-f-{col}', rows=rows_, size='sm',
                             placeholder=ph, className='mb-2'),
            ]) for col, label, rows_, ph in TEXT_FIELDS],
            dbc.Row([
                dbc.Col(dbc.Button('保存并生效', id='help-save', color='success',
                                   size='sm', n_clicks=0), width='auto'),
                dbc.Col(html.Div(id='help-msg', className='small'),
                        className='d-flex align-items-center'),
            ], className='g-2'),
        ]), footer='脚本里的那份初始内容在 scripts/ops/seed_help_notes.py；'
                   '它默认只补不覆盖，所以这里手改过的说明不会被重跑冲掉'),
    ])


def register_callbacks(app) -> None:
    @app.callback(
        [Output('help-key', 'value'), Output('help-scope', 'value'),
         Output('help-target', 'value')]
        + [Output(f'help-f-{c}', 'value') for c, *_ in TEXT_FIELDS]
        + [Output('help-preview', 'children')],
        Input('help-pick', 'value'),
        prevent_initial_call=True)
    def _load(key):
        if not key:
            return [no_update] * (4 + len(TEXT_FIELDS))
        row = help_repo.notes().get(key) or {}
        vals = []
        for col, *_ in TEXT_FIELDS:
            v = row.get(col)
            vals.append(', '.join(v) if isinstance(v, list) else (v or ''))
        return [key, row.get('scope') or 'card', row.get('target') or '',
                *vals, dcc.Markdown(help_repo.text(key) or '（这条渲染结果是空的）')]

    @app.callback(Output('help-msg', 'children'),
                  Output('help-preview', 'children', allow_duplicate=True),
                  Output('help-gaps', 'children'),
                  Output('help-pick', 'options'),
                  Input('help-save', 'n_clicks'),
                  State('help-key', 'value'), State('help-scope', 'value'),
                  State('help-target', 'value'),
                  *[State(f'help-f-{c}', 'value') for c, *_ in TEXT_FIELDS],
                  prevent_initial_call=True)
    def _save(_n, key, scope, target, *vals):
        values = {'scope': scope, 'target': (target or '').strip() or None}
        for (col, *_), v in zip(TEXT_FIELDS, vals, strict=False):
            values[col] = (v or '').strip() or None
        try:
            op = help_repo.save(key, values)
        except Exception as exc:  # noqa: BLE001
            return (html.Span(f'没存成：{exc}', className='text-danger'),
                    no_update, no_update, no_update)
        return (html.Span('已' + ('新增' if op == 'ins' else '更新')
                          + f'「{key}」，刷新页面就能看到',
                          className='text-success'),
                dcc.Markdown(help_repo.text(key) or '（渲染结果是空的）'),
                _gap_table(), _options())
