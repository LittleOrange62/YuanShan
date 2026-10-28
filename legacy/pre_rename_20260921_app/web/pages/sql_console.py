# -*- coding: utf-8 -*-
"""
SQL 查询台 —— 旧 `/sqlite` 页面的正经替代。

旧系统的教训值得完整记下来：`/api/sql/execute` 只用字符串前缀判断
"是不是 select"，于是 `ATTACH DATABASE '任意路径' AS x` 就能绕过库白名单
和只读限制，而 `sql_api_auth_enabled` 默认 False 且监听 0.0.0.0 ——
等于把整台机器的文件系统开放给局域网。

这里用三层防护，而且**任何一层被绕过都不至于泄漏或改数据**：

1. 连接用 `ft_reader` 角色，它在库层只有 SELECT 权限。这是硬边界，
   不依赖应用层判断是否正确。
2. 用 sqlglot 解析语法树，确认是单条 SELECT/WITH，且不含 DDL/DML 节点。
   是语法树判断，不是字符串匹配。
3. 自动补 LIMIT，角色级 statement_timeout 60 秒，防拖库和跑死。

第 1 层是根本，第 2、3 层是为了给出友好报错和避免误操作。
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import Input, Output, State, html

from app.db import engine
from app.web import ui

MAX_LIMIT = 2000

FORBIDDEN_NODES = (
    'Insert', 'Update', 'Delete', 'Drop', 'Create', 'Alter', 'TruncateTable',
    'Grant', 'Revoke', 'Copy', 'Command', 'Attach', 'Set',
)

EXAMPLES = [
    ('最新交易日的涨幅前 20',
     "SELECT d.ts_code, s.name, d.close, d.pct_chg, d.turnover_rate_pct\n"
     "  FROM md.stock_daily d JOIN ref.security s USING (ts_code)\n"
     " WHERE d.trade_date = (SELECT max(trade_date) FROM md.stock_daily)\n"
     "   AND s.asset_type = 'stock'\n"
     " ORDER BY d.pct_chg DESC LIMIT 20"),
    ('每年日线行数',
     "SELECT extract(year FROM trade_date) AS y, count(*) AS n\n"
     "  FROM md.stock_daily GROUP BY 1 ORDER BY 1"),
    ('按板块看个股数量',
     "SELECT board, count(*) AS n FROM ref.security\n"
     " WHERE asset_type = 'stock' GROUP BY board ORDER BY n DESC"),
    ('有效权重',
     'SELECT * FROM config.v_effective_binding'),
    ('字段口径字典',
     'SELECT * FROM ops.v_field_dict LIMIT 50'),
]


def check_sql(sql: str) -> tuple[bool, str, str]:
    """
    返回 (是否放行, 处理后的 SQL, 提示)。用语法树判断，不用字符串前缀。
    """
    text = (sql or '').strip().rstrip(';').strip()
    if not text:
        return False, '', '先写一条 SELECT。'
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return False, '', '缺少 sqlglot，无法做语法树校验，出于安全拒绝执行。'

    try:
        stmts = sqlglot.parse(text, read='postgres')
    except Exception as exc:  # noqa: BLE001
        return False, '', f'SQL 解析失败：{exc}'

    stmts = [s for s in stmts if s is not None]
    if len(stmts) != 1:
        return False, '', f'只允许一条语句，这里解析出 {len(stmts)} 条。'

    tree = stmts[0]
    if not isinstance(tree, exp.Select | exp.Union) and \
            not (isinstance(tree, exp.With) or tree.find(exp.Select)):
        return False, '', f'只允许 SELECT，这条是 {type(tree).__name__}。'

    for node in tree.walk():
        name = type(node).__name__
        if name in FORBIDDEN_NODES:
            return False, '', f'语法树里出现了 {name} 节点，只读查询台不放行。'

    # 自动补 LIMIT
    limit = tree.args.get('limit') if hasattr(tree, 'args') else None
    if limit is None:
        text = f'SELECT * FROM ({text}) AS _q LIMIT {MAX_LIMIT}'
        note = f'未写 LIMIT，已自动限制 {MAX_LIMIT} 行。'
    else:
        note = ''
    return True, text, note


def render():
    return html.Div([
        ui.alert('这里用 ft_reader 只读角色连接，语法树校验只允许单条 SELECT，'
                 '超时 60 秒。即使校验被绕过，角色权限也写不了数据。', 'light'),
        dbc.Row([
            dbc.Col([
                dbc.Textarea(id='sql-input', rows=8,
                             placeholder='SELECT ...',
                             style={'fontFamily': 'Consolas, monospace',
                                    'fontSize': '.85rem'}),
                html.Div([
                    dbc.Button('执行', id='sql-run', color='primary', size='sm',
                               className='mt-2 me-2'),
                    dbc.Badge(id='sql-note', color='light', text_color='dark',
                              className='mt-2'),
                ]),
            ], width=12, lg=8),
            dbc.Col(ui.card('示例', html.Div([
                dbc.Button(label, id={'kind': 'sql-example', 'i': i},
                           color='link', size='sm',
                           className='d-block text-start p-0')
                for i, (label, _sql) in enumerate(EXAMPLES)
            ]), height=210, help_key='sql.samples'), width=12, lg=4),
        ], className='g-3'),
        html.Div(id='sql-result', className='mt-3'),
    ])


def register_callbacks(app):
    @app.callback(Output('sql-result', 'children'),
                  Output('sql-note', 'children'),
                  Input('sql-run', 'n_clicks'),
                  State('sql-input', 'value'),
                  prevent_initial_call=True)
    def _run(_n, sql):
        ok, final_sql, note = check_sql(sql)
        if not ok:
            return ui.alert(note, 'warning'), ''
        try:
            rows = engine.query(final_sql, role='reader')
        except Exception as exc:  # noqa: BLE001
            return ui.alert(f'执行失败：{exc}', 'danger'), note
        return html.Div([
            html.Div(f'{len(rows)} 行', className='text-muted small mb-1'),
            ui.table(rows, page_size=25),
        ]), note

    @app.callback(Output('sql-input', 'value'),
                  [Input({'kind': 'sql-example', 'i': i}, 'n_clicks')
                   for i in range(len(EXAMPLES))],
                  prevent_initial_call=True)
    def _fill_example(*clicks):
        # 哪个按钮被点了：取 n_clicks 非空且最大的那个
        best, best_n = None, 0
        for i, n in enumerate(clicks):
            if n and n > best_n:
                best, best_n = i, n
        # **返回值必须包成列表。** 这个回调的输入是 pattern-matching id
        # （`{'kind':'sql-example','i':i}`），Dash 3 把它当 wildcard multi-output，
        # 返回裸字符串会抛 InvalidCallbackReturnValue → HTTP 500。
        # 症状很坑：点示例按钮毫无反应，页面上一点错都看不到，只有服务端日志里有。
        if best is None:
            return ['']
        return [EXAMPLES[best][1]]
