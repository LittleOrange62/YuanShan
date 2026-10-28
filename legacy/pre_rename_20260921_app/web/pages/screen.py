# -*- coding: utf-8 -*-
"""
筛选器 —— 选股条件的**专属页**，两个标签。

- **① 条件组合**：`config.screen_rule` 那套可配条件的组合筛选（原来的整页内容）。
- **② 形态扫描**（2026-09-01 加）：两个写死判据的形态扫描 ——「前 N 到前 M 个
  交易日涨停若干次」和「断板后走箱体、当日跌破均线」。**它们不走条件模板**：
  连板要数连续段、箱体要找"最后一个涨停日"，都塞不进 WHERE 片段
  （历史上那条 `consecutive` 模板就是因为写不对被删掉的），判据在
  `app/compute/pattern.py`。

原来 `/screen` 是配置页：三张只读卡（条件类型、我的方案、当日涨幅前列），

能看见"库里登记了 5 种条件"，但点不动 —— 没有任何地方能真的跑一次筛选。
「声明了就要能用」，所以这一页现在是专属页：上半截配条件、点运行出结果，
下半截保留原来那三张配置卡（注册表内容本身也是要看的东西）。

设计取舍：

- **条件槽固定 3 个**。旧系统的筛选就是"日期价格比较 + N 日涨停 + 振幅"这
  几条的组合，3 个槽够用；做成"点加号无限加"要维护动态槽的增删状态，
  而多出来的复杂度换不到一条真实用得上的筛法。
- **参数控件按 `param_schema` 生成，不写死**。加一种条件类型＝往
  `config.screen_rule` 插一行，这一页不用改：`literal + enum` 出下拉、
  `format:date` 出日期框、`integer/number` 出数字框。这是"积木"这件事在
  筛选器上的落点 —— 写死七个输入框的话，注册表就成了摆设。
- **动态控件的 id 带 `name`**（`{'type':'sc-p','i':槽号,'name':参数名}`），
  运行时用 `ALL` 通配拿回来、再按 `ctx.states_list` 里的 id 还原成
  `{参数名: 值}`。不这么做就只能给三个槽各写死一组 State，
  等于把 schema 又抄了一遍到代码里。
- **候选集默认自选池**（旧系统 `/watchlist_filter` 的语义）；切"全市场"时
  实测 1.3s 左右，仍在点一下就出结果的范围内，所以不做后台任务。
- **方案存的是 条件+逻辑+候选集**，不是只存条件（同一组条件 and / or
  是两个完全不同的方案）。载入方案时先写 Store 再改下拉，参数控件重建时
  从 Store 里取值回填。
- 报错照实显示：`ScreenError` 的文案本身就是给人看的（"参数 op 只能是
  ['>','<'] 之一"），包装成"筛选失败"等于把唯一有用的信息丢掉。
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, callback_context, dcc, html, no_update

from app.compute import pattern, screener
from app.db import engine
from app.db.repo import registry as reg_repo
from app.web import queries, ui

SLOTS = (1, 2, 3)

# 参数名 → 中文标签。schema 里没有中文名（它是给引擎看的），
# 但页面上"value"这种标签等于没标签。缺的直接显示原名，不兜底成空。
PARAM_CN = {'on_date': '日期', 'field': '字段', 'op': '比较', 'value': '数值',
            'days': '天数', 'pct': '涨幅%', 'min_days': '至少天数'}

FIELD_CN = {'open': '开盘', 'close': '收盘', 'high': '最高', 'low': '最低'}

# 结果表里要染色的列：涨跌有正负，其余按数值大小不着色。
PCT_COLS = ('涨跌幅',)


def _rules() -> list[dict]:
    return screener.rules()


def _rule_options(rules: list[dict]) -> list[dict]:
    return [{'label': r['name'], 'value': r['rule_key']} for r in rules]


def _latest_date():
    return engine.scalar('SELECT max(trade_date) FROM md.stock_daily')


def _param_input(i: int, name: str, spec: dict, value=None):
    """一个参数一个控件。控件类型完全由 schema 决定。"""
    ident = {'type': 'sc-p', 'i': i, 'name': name}
    default = spec.get('default')
    val = default if value in (None, '') else value
    if spec.get('literal'):
        opts = [{'label': FIELD_CN.get(e, e), 'value': e}
                for e in (spec.get('enum') or [])]
        ctrl = dbc.Select(id=ident, options=opts, value=val, size='sm')
    elif spec.get('format') == 'date':
        ctrl = dbc.Input(id=ident, type='date',
                         value=str(val) if val else None, size='sm')
    elif spec.get('type') in ('integer', 'number'):
        ctrl = dbc.Input(id=ident, type='number', value=val, size='sm',
                         step=1 if spec.get('type') == 'integer' else 0.1)
    else:
        ctrl = dbc.Input(id=ident, value=val, size='sm')
    return dbc.Col(dbc.InputGroup([
        dbc.InputGroupText(PARAM_CN.get(name, name), className='py-1'),
        ctrl,
    ]), width=12, lg=3)


def _params_area(i: int, rule_key, given: dict | None = None):
    """某个槽的参数区。规则没选就给一句提示，不留一片空白。"""
    if not rule_key:
        return html.Div('选一个条件类型', className='text-muted small')
    rule = screener.rules_map().get(rule_key)
    if rule is None:
        return html.Div(f'库里没有这种条件：{rule_key}', className='text-danger small')
    props = screener.schema_props(rule)
    given = given or {}
    row = dbc.Row([_param_input(i, n, s or {}, given.get(n))
                   for n, s in props.items()], className='g-2')
    return html.Div([row, html.Div(rule.get('note') or '',
                                   className='text-muted small mt-1')])


def _slot(i: int, rules: list[dict]):
    return html.Div([
        dbc.Row([
            dbc.Col(dbc.Checkbox(id={'type': 'sc-on', 'i': i},
                                 label=f'条件{i}', value=(i == 1)),
                    width='auto', className='d-flex align-items-center'),
            dbc.Col(dcc.Dropdown(id={'type': 'sc-rule', 'i': i},
                                 options=_rule_options(rules),
                                 value=None, clearable=True,
                                 placeholder='条件类型'), width=12, lg=3),
            dbc.Col(html.Div(_params_area(i, None),
                             id={'type': 'sc-params', 'i': i}), width=12, lg=8),
        ], className='g-2'),
        html.Hr(className='my-2'),
    ])


def _controls(day):
    return dbc.Row([
        dbc.Col(dbc.InputGroup([
            dbc.InputGroupText('候选集', className='py-1'),
            dbc.Select(id='sc-universe', size='sm', value='watchlist',
                       options=[{'label': '自选池', 'value': 'watchlist'},
                                {'label': '全市场', 'value': 'market'}]),
        ]), width=12, lg=3),
        dbc.Col(dbc.InputGroup([
            dbc.InputGroupText('条件之间', className='py-1'),
            dbc.Select(id='sc-logic', size='sm', value='and',
                       options=[{'label': '全部满足 (and)', 'value': 'and'},
                                {'label': '任一满足 (or)', 'value': 'or'}]),
        ]), width=12, lg=3),
        dbc.Col(dbc.InputGroup([
            dbc.InputGroupText('基准交易日', className='py-1'),
            dbc.Input(id='sc-date', type='date', size='sm',
                      value=str(day) if day else None),
        ]), width=12, lg=3),
        dbc.Col(dbc.InputGroup([
            dbc.InputGroupText('最多', className='py-1'),
            dbc.Input(id='sc-limit', type='number', value=500, size='sm'),
            dbc.InputGroupText('只', className='py-1'),
        ]), width=12, lg=2),
        dbc.Col(dbc.Button('运行筛选', id='sc-run', color='primary', size='sm',
                           n_clicks=0), width='auto'),
    ], className='g-2')


def _preset_bar():
    return dbc.Row([
        dbc.Col(dcc.Dropdown(id='sc-preset', options=_preset_options(),
                             placeholder='选一个已存方案'), width=12, lg=4),
        dbc.Col(dbc.Button('载入', id='sc-load', color='secondary',
                           outline=True, size='sm', n_clicks=0), width='auto'),
        dbc.Col(dbc.Button('删除', id='sc-del', color='danger',
                           outline=True, size='sm', n_clicks=0), width='auto'),
        dbc.Col(dbc.Input(id='sc-pname', size='sm',
                          placeholder='方案名（同名覆盖）'), width=12, lg=3),
        dbc.Col(dbc.Button('存为方案', id='sc-save', color='success',
                           size='sm', n_clicks=0), width='auto'),
    ], className='g-2')


def _preset_options() -> list[dict]:
    return [{'label': p['name'], 'value': p['preset_id']}
            for p in screener.presets()]


def _cond_panel():
    """① 条件组合：原来整页的内容，一行没改，只是挪进标签里。"""
    rules = _rules()
    day = _latest_date()
    charts = {c['chart_key']: c for c in reg_repo.charts()}
    return html.Div([
        ui.alert('勾上条件、填好参数、点「运行筛选」。条件类型来自 '
                 'config.screen_rule（在「积木管理」里可加可改），参数控件按它的 '
                 'param_schema 自动生成。一条都不勾＝只看候选集本身。', 'light'),
        dcc.Store(id='sc-preload'),
        dbc.Row([
            dbc.Col(ui.card('筛选条件', html.Div([
                _controls(day),
                html.Hr(className='my-2'),
                *[_slot(i, rules) for i in SLOTS],
                _preset_bar(),
                html.Div(id='sc-msg', className='small mt-2'),
            ]), help_key='screen.cond'), width=12, className='mb-3'),
            dbc.Col(ui.card('筛选结果', html.Div(
                '还没跑。上面点「运行筛选」。', id='sc-result',
                className='text-muted p-2'), help_key='screen.result'),
                width=12, className='mb-3'),
        ], className='g-3'),
        html.Hr(),
        html.H6('注册表与快速榜（卡片来自 config.page_layout）',
                className='text-muted'),
        ui.render_page(reg_repo.layout('screen'), charts),
    ])


# ---------------------------------------------------------------- ② 形态扫描

#: 用户 2026-09-01 给的**后续观察要点**，逐字放上去。
#: 这两句不是筛选条件（反包有没有打板、次日是不是一字板，要盯盘才知道），
#: 是筛出候选之后拿这两条去人工确认。写在页面上而不是只写在文档里，
#: 是因为这张表本身看不出"筛出来之后该干什么"。
FOLLOW_UP = ('后一天对 10 日线一次反包，这次反包必须打板。'
             '然后观察后面两天的箱体突破，必须是一字板或加速秒板。')

POOL_OPTS = [{'label': '全市场', 'value': 'market'},
             {'label': '自选池', 'value': 'watchlist'}]


def _num(id_: str, label: str, value, *, step=1, unit: str = '', lg: int = 2):
    grp = [dbc.InputGroupText(label, className='py-1'),
           dbc.Input(id=id_, type='number', value=value, size='sm', step=step)]
    if unit:
        grp.append(dbc.InputGroupText(unit, className='py-1'))
    return dbc.Col(dbc.InputGroup(grp), width=12, lg=lg)


def _sel(id_: str, label: str, value, options, *, lg: int = 2):
    return dbc.Col(dbc.InputGroup([
        dbc.InputGroupText(label, className='py-1'),
        dbc.Select(id=id_, size='sm', value=value, options=options),
    ]), width=12, lg=lg)


def _date(id_: str, day, *, label: str = '基准交易日', lg: int = 3):
    return dbc.Col(dbc.InputGroup([
        dbc.InputGroupText(label, className='py-1'),
        dbc.Input(id=id_, type='date', size='sm',
                  value=str(day) if day else None),
    ]), width=12, lg=lg)


def _streak_card(day):
    return ui.card(
        '① 连板窗口：前 N 到前 M 个交易日里涨停若干次',
        html.Div([
            dbc.Row([
                _date('pt-a-date', day),
                _sel('pt-a-pool', '票池', 'market', POOL_OPTS),
                _num('pt-a-n', '前', 3, unit='起', lg=2),
                _num('pt-a-m', '到前', 7, unit='止', lg=2),
                _sel('pt-a-op', '次数', '>=',
                     [{'label': '至少 (≥)', 'value': '>='},
                      {'label': '正好 (=)', 'value': '='},
                      {'label': '至多 (≤)', 'value': '<='}], lg=2),
                _num('pt-a-times', '', 3, unit='次', lg=2),
                _num('pt-a-limit', '最多', 500, unit='只', lg=2),
                dbc.Col(dbc.Button('扫描', id='pt-a-run', color='primary',
                                   size='sm', n_clicks=0), width='auto'),
            ], className='g-2'),
            html.Div('窗口两端都含：填 3 和 7 ＝ 倒数第 3 到第 7 个交易日，'
                     '前 0 ＝ 含基准日本身。'
                     '「涨停次数」是**窗口内涨停的天数**，'
                     '「连板高度」是最长那一段的板数 —— 两个数都给，别混着看。',
                     className='text-muted small mt-1'),
            html.Div(id='pt-a-msg', className='small mt-2'),
            html.Div('还没扫。点上面的「扫描」。', id='pt-a-result',
                     className='text-muted p-2'),
        ]), help_key='q_pattern_streak')


def _box_card(day):
    return ui.card(
        '② 断板后走箱体，且当日跌破均线',
        html.Div([
            dbc.Row([
                _date('pt-b-date', day),
                _sel('pt-b-pool', '票池', 'market', POOL_OPTS),
                _num('pt-b-look', '回看', 20, unit='日', lg=2),
                _num('pt-b-days', '箱体≥', 3, unit='日', lg=2),
                _num('pt-b-pct', '振幅≤', 15, step=0.5, unit='%', lg=2),
                _sel('pt-b-ma', '均线', 'ma5',
                     [{'label': '5 日线', 'value': 'ma5'},
                      {'label': '10 日线', 'value': 'ma10'},
                      {'label': '20 日线', 'value': 'ma20'}], lg=2),
                _sel('pt-b-price', '跌破用', 'close',
                     [{'label': '收盘价', 'value': 'close'},
                      {'label': '盘中最低', 'value': 'low'}], lg=2),
                _num('pt-b-limit', '最多', 500, unit='只', lg=2),
                dbc.Col(dbc.Button('扫描', id='pt-b-run', color='primary',
                                   size='sm', n_clicks=0), width='auto'),
            ], className='g-2'),
            html.Div('断板日＝回看窗口内**最后一个涨停日的次日**；箱体＝断板日到'
                     '基准日之间 (最高−最低)/最低 ≤ 振幅阈值、天数够、且期间'
                     '**不再涨停**（再涨停就是二波，不是箱体）。'
                     '「跌破用收盘」是收在线下，「盘中最低」是盘中破过就算。',
                     className='text-muted small mt-1'),
            html.Div(id='pt-b-msg', className='small mt-2'),
            html.Div('还没扫。点上面的「扫描」。', id='pt-b-result',
                     className='text-muted p-2'),
        ]), help_key='q_pattern_box')


def _pattern_panel():
    day = _latest_date()
    return html.Div([
        ui.alert('**这两张是形态扫描，不走 config.screen_rule 那套条件模板** —— '
                 '连板要数连续段、箱体要找"最后一个涨停日"，塞不进 WHERE 片段。'
                 '涨停判据用库里的真实涨停价（`md.stock_daily.limit_up`），'
                 '不是"涨幅>9.8%"，所以创业板 20%、北交所 30%、主板 ST 5% 都不会判错。',
                 'light'),
        ui.alert('**筛出来之后要盯的**：' + FOLLOW_UP +
                 '（这两句是人工确认的动作，不是这里的筛选条件）', 'warning'),
        dbc.Row([
            dbc.Col(_streak_card(day), width=12, className='mb-3'),
            dbc.Col(_box_card(day), width=12, className='mb-3'),
        ], className='g-3'),
    ])


def render():
    # 两个标签都在首屏渲染完（`dbc.Tabs` 的 children 是静态的）：这一页没有
    # Interval，静态渲染的代价只是多跑一次 _rules()/_latest_date()。
    return dbc.Tabs([
        dbc.Tab(label='① 条件组合', tab_id='cond', children=_cond_panel()),
        dbc.Tab(label='② 形态扫描', tab_id='pattern', children=_pattern_panel()),
    ], id='screen-tabs', active_tab='cond', className='mb-2')



# ---------------------------------------------------------------- 取值

def _collect(states_list) -> dict[int, dict]:
    """
    把 `ALL` 通配回来的动态参数控件还原成 `{槽号: {参数名: 值}}`。

    `states_list` 里每一项都带着自己的 id，这是唯一能知道"这个值是哪个槽的
    哪个参数"的地方 —— 通配 State 的返回值只是一串裸值，顺序还随 DOM 变。
    这里把整个 `states_list` 平铺着扫、按 id 的 `type` 认领，而不是按下标取
    第几组：State 的顺序以后加一个就全错位，而错位的表现是"筛出来的结果不对"，
    不是报错。
    """
    out: dict[int, dict] = {}
    stack = list(states_list or [])
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
            continue
        ident = (item or {}).get('id') or {}
        if not isinstance(ident, dict) or ident.get('type') != 'sc-p':
            continue
        out.setdefault(int(ident['i']), {})[ident['name']] = item.get('value')
    return out



def _conds(on_flags, rule_keys, per_slot: dict[int, dict]) -> list[dict]:
    """勾了且选了类型的槽才算一条条件。"""
    conds = []
    for idx, (on, key) in enumerate(zip(on_flags, rule_keys, strict=False), 1):
        if not on or not key:
            continue
        conds.append({'rule_key': key, 'params': per_slot.get(idx, {})})
    return conds


#: 形态扫描两张表里按正负染色的列。`箱体振幅` 永远是正数，染色没有意义，
#: 所以不放进来 —— 全绿的一列只会让人以为"这一列有含义"。
PT_PCT_COLS = ('涨跌幅', '破线幅度')


def _pattern_table(rows: list[dict]):
    """扫描结果统一渲染。行数不定，所以不写死高度 —— 表格高度＝内容高度。"""
    for r in rows:
        r['详情'] = ui.detail_link(r['代码'])
    return ui.table(rows, page_size=20, color=ui.rules_pct(PT_PCT_COLS))


def _empty(msg: str):
    return html.Div(msg, className='text-muted p-2')


def _register_pattern(app) -> None:
    """两张形态扫描卡各一个回调。它们的 Output 不重叠，不用挤在一起。"""

    @app.callback(Output('pt-a-result', 'children'),
                  Output('pt-a-msg', 'children'),
                  Input('pt-a-run', 'n_clicks'),
                  State('pt-a-date', 'value'), State('pt-a-pool', 'value'),
                  State('pt-a-n', 'value'), State('pt-a-m', 'value'),
                  State('pt-a-op', 'value'), State('pt-a-times', 'value'),
                  State('pt-a-limit', 'value'),
                  prevent_initial_call=True)
    def _run_streak(_n, day, pool, n_back, m_back, op, times, limit):
        try:
            rows = queries.q_pattern_streak(
                trade_date=day or None, n_back=n_back if n_back is not None else 3,
                m_back=m_back if m_back is not None else 7,
                times=times if times is not None else 3, op=op or '>=',
                universe=pool or 'market', limit=limit or 500)
        except pattern.PatternError as exc:
            return no_update, html.Span(str(exc), className='text-danger')
        except Exception as exc:                                  # noqa: BLE001
            return no_update, html.Span(f'出错了：{exc}',
                                        className='text-danger')
        lo, hi = pattern.window_bounds(
            pattern.resolve_day(day or None),
            n_back if n_back is not None else 3,
            m_back if m_back is not None else 7)

        # 窗口的真实日期一定要显示：填"前 3 到前 7"看不出扫的是哪几天，
        # 而基准日一变（比如库里今天的数据还没进来）整个窗口就往前挪一格。
        note = f'窗口 {lo} ~ {hi}（{op or ">="} {times or 3} 次）'
        if not rows:
            return (_empty('这个窗口里没有符合次数条件的票。放宽次数、把窗口拉长，'
                           '或者把票池切到「全市场」。'),
                    html.Span(note + '，0 只命中', className='text-muted'))
        return (_pattern_table(rows),
                html.Span(f'{note}，命中 {len(rows)} 只', className='text-success'))

    @app.callback(Output('pt-b-result', 'children'),
                  Output('pt-b-msg', 'children'),
                  Input('pt-b-run', 'n_clicks'),
                  State('pt-b-date', 'value'), State('pt-b-pool', 'value'),
                  State('pt-b-look', 'value'), State('pt-b-days', 'value'),
                  State('pt-b-pct', 'value'), State('pt-b-ma', 'value'),
                  State('pt-b-price', 'value'), State('pt-b-limit', 'value'),
                  prevent_initial_call=True)
    def _run_box(_n, day, pool, look, box_days, box_pct, ma, price, limit):
        try:
            rows = queries.q_pattern_box(
                trade_date=day or None, lookback=look or 20,
                box_days=box_days or 3,
                box_pct=box_pct if box_pct is not None else 15.0,
                ma=ma or 'ma5', price=price or 'close',
                universe=pool or 'market', limit=limit or 500)
        except pattern.PatternError as exc:
            return no_update, html.Span(str(exc), className='text-danger')
        except Exception as exc:                                  # noqa: BLE001
            return no_update, html.Span(f'出错了：{exc}',
                                        className='text-danger')
        what = ('收盘' if (price or 'close') == 'close' else '盘中最低')
        note = f'{what}跌破 {ma or "ma5"} · 箱体≥{box_days or 3} 日 · 振幅≤{box_pct}%'
        if not rows:
            return (_empty('没有符合的票。把振幅阈值放大、箱体天数放小，'
                           '或者换成 10 日线试试。'),
                    html.Span(note + '，0 只命中', className='text-muted'))
        return (_pattern_table(rows),
                html.Span(f'{note}，命中 {len(rows)} 只', className='text-success'))


def register_callbacks(app) -> None:

    _register_pattern(app)

    @app.callback(Output({'type': 'sc-params', 'i': ALL}, 'children'),

                  Input({'type': 'sc-rule', 'i': ALL}, 'value'),
                  Input('sc-preload', 'data'))
    def _build_params(rule_keys, preload):
        """
        规则一变就重建这个槽的参数区。**一个回调管三个槽**（ALL 而不是 MATCH）：
        载入方案时要同时改三个槽，MATCH 拆成三个回调后 `sc-preload` 这个
        全局 Input 会各自触发一次，回填顺序不可控。
        """
        given = (preload or {}).get('params') or {}
        return [_params_area(i, key, given.get(str(i)) or given.get(i))
                for i, key in zip(SLOTS, rule_keys, strict=False)]

    @app.callback(Output('sc-result', 'children'), Output('sc-msg', 'children'),
                  Output('sc-preset', 'options'), Output('sc-preload', 'data'),
                  Output({'type': 'sc-rule', 'i': ALL}, 'value'),
                  Output({'type': 'sc-on', 'i': ALL}, 'value'),
                  Output('sc-logic', 'value'), Output('sc-universe', 'value'),
                  Input('sc-run', 'n_clicks'), Input('sc-save', 'n_clicks'),
                  Input('sc-load', 'n_clicks'), Input('sc-del', 'n_clicks'),
                  State({'type': 'sc-on', 'i': ALL}, 'value'),
                  State({'type': 'sc-rule', 'i': ALL}, 'value'),
                  State({'type': 'sc-p', 'i': ALL, 'name': ALL}, 'value'),
                  State('sc-logic', 'value'), State('sc-universe', 'value'),
                  State('sc-date', 'value'), State('sc-limit', 'value'),
                  State('sc-pname', 'value'), State('sc-preset', 'value'),
                  prevent_initial_call=True)
    def _act(_r, _s, _l, _d, on_flags, rule_keys, _pvals, logic, universe,
             day, limit, pname, preset_id):
        """
        运行 / 存方案 / 载入 / 删除，四个按钮一个回调 —— 它们都要写
        `sc-msg` 和方案下拉，Dash 不允许两个回调写同一个 Output。
        """
        trig = callback_context.triggered_id
        keep = (no_update,) * 4          # 规则值、勾选、逻辑、候选集 不动
        per_slot = _collect(callback_context.states_list)

        try:
            if trig == 'sc-run':
                conds = _conds(on_flags, rule_keys, per_slot)
                rows = screener.run(conds, logic=logic or 'and',
                                    universe=universe or 'watchlist',
                                    trade_date=day or None,
                                    limit=int(limit or 500))
                if not rows:
                    return (html.Div('没有符合条件的标的。放宽参数，或把候选集'
                                     '切到「全市场」。', className='text-muted p-2'),
                            html.Span(f'{len(conds)} 条条件，0 只命中',
                                      className='text-muted'),
                            no_update, no_update, *keep)
                # 筛出来的下一步一定是"看这只票到底怎么样"，所以每行带站内深链
                for r in rows:
                    r['详情'] = ui.detail_link(r['代码'])
                tbl = ui.table(rows, page_size=50,
                               color=ui.rules_pct(PCT_COLS))
                return (tbl, html.Span(f'{len(conds)} 条条件，命中 {len(rows)} 只'
                                       f'（按操作分降序）', className='text-success'),
                        no_update, no_update, *keep)

            if trig == 'sc-save':
                conds = _conds(on_flags, rule_keys, per_slot)
                screener.save_preset(pname or '', conds,
                                     logic=logic or 'and',
                                     universe=universe or 'watchlist')
                return (no_update,
                        html.Span(f'已存方案「{(pname or "").strip()}」',
                                  className='text-success'),
                        _preset_options(), no_update, *keep)

            if trig == 'sc-del':
                if preset_id in (None, ''):
                    return (no_update, html.Span('先选一个方案',
                                                 className='text-danger'),
                            no_update, no_update, *keep)
                screener.delete_preset(preset_id)
                return (no_update, html.Span('已删除', className='text-muted'),
                        _preset_options(), no_update, *keep)

            if trig == 'sc-load':
                if preset_id in (None, ''):
                    return (no_update, html.Span('先选一个方案',
                                                 className='text-danger'),
                            no_update, no_update, *keep)
                p = next((x for x in screener.presets()
                          if x['preset_id'] == preset_id), None)
                if p is None:
                    return (no_update, html.Span('这个方案已经不在了',
                                                 className='text-danger'),
                            _preset_options(), no_update, *keep)
                body = p['conditions'] or {}
                conds = body.get('conditions') or []
                keys = [c.get('rule_key') for c in conds][:len(SLOTS)]
                keys += [None] * (len(SLOTS) - len(keys))
                flags = [k is not None for k in keys]
                preload = {'params': {str(i + 1): (conds[i].get('params') or {})
                                      for i in range(len(conds[:len(SLOTS)]))}}
                return (no_update,
                        html.Span(f'已载入「{p["name"]}」，点「运行筛选」出结果',
                                  className='text-success'),
                        no_update, preload, keys, flags,
                        body.get('logic') or 'and',
                        body.get('universe') or 'watchlist')
        except screener.ScreenError as exc:
            return (no_update, html.Span(str(exc), className='text-danger'),
                    no_update, no_update, *keep)
        except Exception as exc:                                  # noqa: BLE001
            return (no_update, html.Span(f'出错了：{exc}',
                                         className='text-danger'),
                    no_update, no_update, *keep)

        return (no_update, no_update, no_update, no_update, *keep)
