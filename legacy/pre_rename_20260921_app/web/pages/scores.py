# -*- coding: utf-8 -*-
"""
全市场个股分析 —— **按四象限选票**的专属页。

原来这一页叫「评分与因子」，是个配置页：四象限散点图 + 量能阶段分布 +
操作分排行 + 权重表。图上能看出"右上角有一堆点"，但**点不动** —— 想知道那一堆
是哪些票、分别多少分，得回到排行榜里自己按象限用眼睛挑。所以这一页现在是
专属页：上半截一个选票框，下半截是全市场逐票的几张表。

**2026-08-30 改名并收编了两张卡**（原来在复盘首页）：

- 「X>30 且 Y>30 的双高个股」—— 散点图看得见那一簇、答不出是哪些票；
- 「个股明细」—— 全市场逐票的当日明细。

搬的理由：复盘首页回答的是"今天大盘和板块怎么样"，逐票筛选是另一件事，
混在一屏要往下翻半天才到；而这两张表跟这一页的选票框问的是同一个问题。
改名同理 ——「评分与因子」听起来像配置页，它其实是全市场逐票筛选的入口。

设计取舍：


- **象限的定义不写注释里，写在数据里再核对过**（2026-08-29 逐条比对当日
  6,547 只票）：`Q1` X≥0 且 Y≥0、`Q2` X≥0 且 Y<0、`Q3` X<0 且 Y≥0、
  `Q4` X<0 且 Y<0。代码里原来的卡片说明把 Q3/Q4 写反了，是这次发现的。
- **默认 Q1**，也就是 X 和 Y 都 ≥0 那一格 —— 自己好、又在风口上，复盘第一眼
  该看的就是它。页面一打开就已经带着结果，不是"点一下才有"：这一页存在的
  意义就是那张表，让人先点一次按钮等于白加一步。
- **X / Y 下限默认 30、排序默认 X+Y**（2026-09-01 按用户要求改，**推翻了原来
  "默认留空不加门槛"的决定**）：原来「X>30 且 Y>30 的双高个股」是下半截独立的
  一张卡（`q_quadrant_strong`），和这张结果表同一天、同一批票、同一个口径，
  区别只是门槛写死、排序写死 —— 同一个问题摆两张表，人还要往下翻半屏才看到
  真正想看的那一簇。现在**合成一张**：默认就是双高（Q1 + X≥30 + Y≥30，
  按 X+Y 排序），想看全貌把两个下限清空即可，那张独立卡已删。
  ⚠️ 口径有一处细微差别：老卡是 `> 30`，这里是 `>= 30`（`q_quadrant_pick` 的
  门槛是闭区间），所以恰好 30.00 分的票现在会多出来几只。
- **四个按钮上带当日只数**。不带的话点进去才知道那个象限是空的 —— 实测（2026-08-31）
  Q2 只有 172 只、Q3 有 2,857 只，这个悬殊本身就是当天的盘面信息。
  ⚠️ 按钮上的只数是**该象限的全部**（不含 X/Y 门槛），所以它必然 ≥ 结果表的
  命中数。结果表上面那行会把当前门槛一起写出来，避免两个数字对不上被当成 bug。
- 排序列走 `queries.QUADRANT_SORTS` 白名单，页面上的下拉就是这个字典 ——
  两处各写一份，迟早出现"下拉里有、查询不认"的选项。
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html

from app.db.repo import registry as reg_repo
from app.web import queries, ui

# 象限 → (短名, 判据, 一句话怎么用)。**顺序就是页面上按钮的顺序**：
# 从"最该看"到"最该躲"，不是按 Q 编号排 —— Q3 的编号在中间，但它是
# "风口热、票暂时弱"，比 Q2 更值得试探。
QUADS: tuple[tuple[str, str, str, str], ...] = (
    ('Q1', '双正 · 好票在风口', 'X≥0 且 Y≥0', '主攻区，先看这里'),
    ('Q3', '风口热 · 票暂时弱', 'X<0 且 Y≥0', '可试探，容易一日游'),
    ('Q2', '好票 · 没风口', 'X≥0 且 Y<0', '等板块，别急'),
    ('Q4', '双负 · 回避', 'X<0 且 Y<0', '两头都不占，躲'),
)

DEFAULT_QUAD = 'Q1'
#: 条数上限。2026-09-01 从 200 提到 400：默认视图就是双高，而板块热度换口径重算
#: 之后当天双高有 **201 只** —— 上限压在 200 会让默认那一屏一进来就顶着
#: 「已到条数上限」，等于默认视图自己是被截断的。
DEFAULT_LIMIT = 400
#: 双高门槛与排序（2026-09-01 用户要求："默认显示 X>30 且 Y>30 的双高个股"）。
#: 写成常量而不是散在 `_controls` / `_result` / 回调三处 —— 三处各填一个 30，
#: 改门槛时漏掉一处的表现是"首屏 30、点一下按钮变成不限"，属于静默不一致。
DEFAULT_MIN_X = 30
DEFAULT_MIN_Y = 30
DEFAULT_SORT = 'xy_total'

# 结果表一页多少行。2026-09-01 从 40 改成 **80**（用户："表格页面再拉长一点，
# 直接能看到的个股多一些"）。`_fit_height` 不封顶，所以 80 行是**全部直接可见**、
# 表格内部不滚 —— 页面本身往下滚一屏多，但滚页面比滚表格好：滚表格会把冻结的
# 表头之外的上下文（选票框、命中数）一起滚掉，翻回去还要再找。
# 08-31 那次从 50 → 40 的理由（page_size 大于可见行数等于把翻页换成滚动）在这里
# 仍然成立，只是这次连高度一起放大，所以 80 行仍然满足"一页 = 一屏能看完的量"。
PAGE_SIZE = 80



def _fit_height(n_rows: int) -> int:
    """抬头 32 + 过滤行 32 + 每行 23 + 末尾 12，实现在 `ui.fit_height`
    （行高常量只存一份，padding 一改两边一起走）。这里不封顶：这张表的行数
    就是 `PAGE_SIZE`，本来就该铺完。"""
    return ui.fit_height(n_rows, cap=10_000)



# 排序下拉的中文名。键必须是 queries.QUADRANT_SORTS 里的键。
SORT_CN = {
    'oper_score': '操作分（0.40·X + 0.60·Y）',
    'xy_total': 'X + Y 合计',
    'x_score': 'X 个股质量',
    'y_score': 'Y 环境共振',
    'pct_chg': '当日涨跌幅',
    'amount_yi': '成交额',
}

PCT_COLS = ('涨跌幅%',)
AMP_COLS = ('换手率%',)


def _counts(day=None) -> dict[str, int]:
    """
    四个按钮上的只数。**必须把基准日传进来**：不传的话 `q_quadrant_counts` 走
    `_d()`，那是日历上最新的已收盘交易日（08-31 15:50 就给 08-31），当天评分还
    没入库 → 四个按钮全写"无数据"，而下面的结果表用的是 08-28、票好好地在。
    页面上表现为"按钮说没数据、表里却有票"，是本页最容易骗人的地方。
    """
    return {r['quadrant']: r['stocks']
            for r in queries.q_quadrant_counts(trade_date=day)}


def _quad_options(counts: dict[str, int]) -> list[dict]:
    out = []
    for q, short, rule, _use in QUADS:
        n = counts.get(q)
        tail = f'{n} 只' if n is not None else '无数据'
        out.append({'label': f'{q} {short}（{rule}） · {tail}', 'value': q})
    return out


def _sort_options() -> list[dict]:
    # 只列 QUADRANT_SORTS 里真有的键：下拉是查询能力的投影，不是另一份清单
    return [{'label': SORT_CN.get(k, k), 'value': k}
            for k in queries.QUADRANT_SORTS]


def _controls(counts: dict[str, int], day):
    return html.Div([
        dbc.RadioItems(id='qd-quad', options=_quad_options(counts),
                       value=DEFAULT_QUAD, inline=False,
                       className='small mb-2'),
        dbc.Row([
            dbc.Col([dbc.Label('基准交易日', size='sm'),
                     dbc.Input(id='qd-date', type='date', size='sm',
                               value=str(day) if day else None,
                               debounce=True)], width=12, lg=2),
            dbc.Col([dbc.Label('X 下限（留空＝不限）', size='sm'),
                     dbc.Input(id='qd-minx', type='number', size='sm',
                               value=DEFAULT_MIN_X,
                               placeholder='如 30', debounce=True)],
                    width=6, lg=2),
            dbc.Col([dbc.Label('Y 下限（留空＝不限）', size='sm'),
                     dbc.Input(id='qd-miny', type='number', size='sm',
                               value=DEFAULT_MIN_Y,
                               placeholder='如 30', debounce=True)],
                    width=6, lg=2),
            dbc.Col([dbc.Label('排序依据', size='sm'),
                     dcc.Dropdown(id='qd-sort', options=_sort_options(),
                                  value=DEFAULT_SORT, clearable=False)],
                    width=12, lg=3),
            dbc.Col([dbc.Label('条数上限', size='sm'),
                     dbc.Input(id='qd-limit', type='number', size='sm',
                               value=DEFAULT_LIMIT, min=10, max=2000,
                               debounce=True)], width=6, lg=2),
            dbc.Col(dbc.Button('选票', id='qd-run', color='primary', size='sm',
                               n_clicks=0, className='mt-4'), width='auto'),
        ], className='g-2'),
        html.Div(_legend(), className='text-muted small mt-2'),
    ])


def _legend():
    """四句话把四个格子的用法说完，不用点开说明也知道在选什么。"""
    return [html.Div([html.Strong(f'{q} '), f'{rule} —— {use}'])
            for q, _short, rule, use in QUADS]


def _result(quadrant=DEFAULT_QUAD, day=None, min_x=DEFAULT_MIN_X,
            min_y=DEFAULT_MIN_Y, sort=DEFAULT_SORT, limit=DEFAULT_LIMIT):
    """
    选票并出表。**同一个函数既给首屏用也给回调用** —— 首屏和点按钮之后看到
    的必须是同一套逻辑，两处各写一遍就会出现"刷新一下结果变了"。

    门槛先归一化成 `None`：清空 `dbc.Input(type='number')` 拿到的是 `''`，
    直接传下去会在 `%(mx)s::numeric` 上炸（空串转 numeric 报
    `invalid input syntax`），而页面上只会看到"渲染失败"。
    """
    min_x = None if min_x in (None, '') else min_x
    min_y = None if min_y in (None, '') else min_y
    rows = queries.q_quadrant_pick(trade_date=day or None, quadrant=quadrant,
                                   min_x=min_x, min_y=min_y,
                                   sort=sort or DEFAULT_SORT,
                                   limit=int(limit or DEFAULT_LIMIT))
    # 门槛写进提示里：按钮上的只数是象限全量，这里是加了门槛之后的命中数，
    # 两个数字必然不等 —— 不把门槛写出来就会被当成"哪个数错了"。
    gate = '＋'.join(filter(None, [
        f'X≥{min_x}' if min_x is not None else '',
        f'Y≥{min_y}' if min_y is not None else '']))
    gate = f'（{gate}）' if gate else '（不加门槛）'
    if not rows:
        return (html.Div('这个象限在这一天没有票（或门槛卡太死）。'
                         '把 X / Y 下限清空，或换一个象限。',
                         className='text-muted p-2'),
                html.Span(f'0 只{gate}', className='text-muted'))
    view = []
    for r in rows:
        row = {ui.col_label(k): v for k, v in r.items() if k != 'detail'}
        row['详情'] = r['detail']
        view.append(row)
    tbl = ui.table(view, page_size=PAGE_SIZE,
                   # **按"这一页真有几行"给高度**，不是按 PAGE_SIZE 给。
                   # 命中数少于一页时（换个象限、把门槛提到 60 就会出现），
                   # 照 80 行的高度画会在表格下面留出几百像素空白，
                   # 看着像"表格坏了/还在加载"。
                   height=_fit_height(min(len(rows), PAGE_SIZE)),
                   color=ui.rules_pct(PCT_COLS) + ui.rules_amp(AMP_COLS))
    hit = f'{quadrant} 命中 {len(rows)} 只{gate}'
    if len(rows) >= int(limit or DEFAULT_LIMIT):
        hit += '（已到条数上限，放大上限看更多）'
    return tbl, html.Span(hit, className='text-muted')


def render():
    day = queries.q_score_dates(limit=1)
    day = day[0]['trade_date'] if day else None
    counts = _counts(day)          # 只数与结果表同一天，见 _counts 的说明
    charts = {c['chart_key']: c for c in reg_repo.charts()}
    table, msg = _result(day=day)
    return html.Div([
        ui.alert('这一页是**全市场逐票**的入口，不限自选：X 是个股质量、'
                 'Y 是环境共振，两条零线把当日全部有分的票分成四格。'
                 '**默认给的就是"双高"**：Q1（X 和 Y 都 ≥0）里再卡 '
                 f'X≥{DEFAULT_MIN_X} 且 Y≥{DEFAULT_MIN_Y}，按 X+Y 从高到低 —— '
                 '想看整个象限的全貌，把两个下限清空。'
                 '换象限、加门槛、改排序都会立刻重算；'
                 '下半截的「个股扫描」是同一天的全市场明细。', 'light'),
        dbc.Row([
            dbc.Col(ui.card('四象限选票', html.Div([
                _controls(counts, day),
                html.Hr(className='my-2'),
                html.Div(msg, id='qd-msg', className='small'),
            ]), help_key='q_quadrant_pick',
                note_id='note-qd-pick'), width=12, className='mb-3'),
            # 标题原来叫「选中的票」，被问过"这是什么意思"——它听着像"我勾选过的票"
            # 或"系统推荐的票"，其实是上面那个选票框按象限 + 门槛 + 排序筛出来的结果。
            # 2026-09-01 标题里点明"默认双高"：原来那张独立的「X>30 且 Y>30 的
            # 双高个股」卡已经合并进来，标题不说的话会以为双高那张表被删掉了。
            dbc.Col(ui.card('选票结果（默认 X≥30 且 Y≥30 的双高个股 · 按 X+Y 排序）',

                            html.Div(table, id='qd-result'),
                            help_key='scores.result'),
                    width=12, className='mb-3'),
        ], className='g-3'),
        html.Hr(),
        html.H6('全市场逐票的散点、明细与排行（卡片来自 config.page_layout）',
                className='text-muted'),
        # 把同一个交易日传给下面的卡片：不传的话每张卡各自取"库里最新的一天"，
        # 上半截选票框标的是 2026-08-28、下面的表却可能是另一天，对不上账。
        # 注意这些卡是**服务端一次性渲染**的，改上面那个「基准交易日」输入框
        # 只影响选票结果，不会重画下面的卡 —— 要整页跟着走得刷新页面。
        ui.render_page(reg_repo.layout('scores'), charts,
                       params={'trade_date': str(day)} if day else None),
    ])



def register_callbacks(app) -> None:
    @app.callback(Output('qd-result', 'children'), Output('qd-msg', 'children'),
                  Input('qd-run', 'n_clicks'), Input('qd-quad', 'value'),
                  Input('qd-sort', 'value'),
                  State('qd-date', 'value'), State('qd-minx', 'value'),
                  State('qd-miny', 'value'), State('qd-limit', 'value'),
                  prevent_initial_call=True)
    def _pick(_n, quadrant, sort, day, min_x, min_y, limit):
        """
        换象限和换排序**直接重算**（Input），门槛和日期改完按回车或点「选票」
        才算（State + debounce）—— 象限是"我要看哪一格"，点一下就该看到；
        门槛是边试边调的数字，每敲一位都跑一次查询是上一版自选股搜索栽过的坑。
        """
        try:
            return _result(quadrant=quadrant or DEFAULT_QUAD, day=day,
                           min_x=min_x, min_y=min_y, sort=sort, limit=limit)
        except ValueError as ex:      # 排序列不在白名单
            return (html.Div(str(ex), className='text-danger p-2'),
                    html.Span('参数不对', className='text-danger'))
