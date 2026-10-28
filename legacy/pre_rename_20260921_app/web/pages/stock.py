# -*- coding: utf-8 -*-
"""
个股详情。

一屏回答"这只票现在什么状态"：K 线与量、评分四项归因、量能温度与阶段、
所属板块、龙虎榜记录。

搜索框接受**代码、简称、拼音首字母**三种输入。只认 `600519.SH` 这种
完整代码是不现实的 —— 复盘时手里通常只有名字。
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, State, callback_context, dcc, html, no_update
from plotly.subplots import make_subplots

from app import quote_link
from app.db import engine
from app.web import queries, ui

DEFAULT_CODE = '600519.SH'
LOOKBACK = 120



def _resolve(text: str | None) -> str | None:
    """代码 / 简称 / 拼音 → ts_code。找不到返回 None。"""
    q = (text or '').strip()
    if not q:
        return None
    rows = engine.query(
        """
        SELECT ts_code FROM ref.security
         WHERE upper(ts_code) = upper(%s)
            OR symbol = %s
            OR name = %s
            OR upper(pinyin_abbr) = upper(%s)
         ORDER BY is_active DESC LIMIT 1
        """, (q, q, q, q))
    if rows:
        return rows[0]['ts_code']
    rows = engine.query(
        """
        SELECT ts_code FROM ref.security
         WHERE name LIKE %s OR symbol LIKE %s
         ORDER BY is_active DESC, ts_code LIMIT 1
        """, (f'%{q}%', f'{q}%'))
    return rows[0]['ts_code'] if rows else None


def _head(ts_code: str):
    row = engine.query(
        """
        SELECT s.ts_code, s.name, s.board, s.is_st, s.list_date,
               -- 行业不在 ref.security 上（那张表只有 board 板块），
               -- 要从 ref.sector_member ⋈ ref.sector_info 里取 sector_type='行业'
               (SELECT i.name FROM ref.sector_member m
                  JOIN ref.sector_info i ON i.sector_code = m.sector_code
                 WHERE m.ts_code = s.ts_code AND m.out_date IS NULL
                   AND i.sector_type = '行业'
                 ORDER BY i.name LIMIT 1)             AS industry,
               d.trade_date, d.close, d.pct_chg, d.turnover_rate_pct,
               d.amount_yuan, d.vol_shares,
               round(q.value, 1) AS x_quality, round(y.value, 1) AS y_env,
               round(o.value, 1) AS oper, o.quadrant, o.rank_in_market,
               round(t.value, 1) AS vol_temp, vs.value AS vol_stage
          FROM ref.security s
          LEFT JOIN md.stock_daily d ON d.ts_code = s.ts_code
               AND d.trade_date = (SELECT max(trade_date) FROM md.stock_daily
                                    WHERE ts_code = s.ts_code)
          LEFT JOIN fct.score q ON q.ts_code = s.ts_code AND q.trade_date = d.trade_date
                               AND q.cap_key = 'stock_quality'
          LEFT JOIN fct.score y ON y.ts_code = s.ts_code AND y.trade_date = d.trade_date
                               AND y.cap_key = 'env_resonance'
          LEFT JOIN fct.score o ON o.ts_code = s.ts_code AND o.trade_date = d.trade_date
                               AND o.cap_key = 'oper_score'
          LEFT JOIN fct.score t ON t.ts_code = s.ts_code AND t.trade_date = d.trade_date
                               AND t.cap_key = 'vol_temp'
          LEFT JOIN fct.score vs ON vs.ts_code = s.ts_code AND vs.trade_date = d.trade_date
                                AND vs.cap_key = 'vol_stage'
         WHERE s.ts_code = %s
        """, (ts_code,))
    if not row:
        return ui.alert(f'{ts_code} 在 ref.security 里找不到', 'warning')
    r = row[0]
    stage = {1: '冷却', 2: '酝酿', 3: '高潮', 4: '背离', 5: '退潮'}.get(
        int(r['vol_stage']) if r['vol_stage'] is not None else 0)
    pct = r['pct_chg']
    tone = 'up' if (pct or 0) >= 0 else 'down'
    # 第一个指标块的名称与代码都挂**东方财富行情页**外链，跟表格里代码/名称列
    # 一样（`ui.linkify_rows` 走的也是 `quote_link.em_url`）：本页给的是评分归因，
    # 想看盘口和资金还是要去行情页，而这里是全站唯一"已经选定了一只票"的地方。
    # `color: inherit` 是必须的 —— 不写的话链接会变成 Bootstrap 的蓝色，
    # 把指标块按涨跌染的色盖掉。`target='_blank'` 另开标签页，不顶掉当前复盘页。
    em = quote_link.em_url(r['ts_code'])

    def _em(text):
        if not em:
            return text
        return html.A(text, href=em, target='_blank', rel='noopener',
                      title='在东方财富看行情',
                      className='text-decoration-none',
                      style={'color': 'inherit'})

    cards = [
        (_em(r['name'] or '-'), _em(r['ts_code']),
         f"{r['board'] or ''} {r['industry'] or ''}".strip() or '—', 'accent'),
        ('最新价', r['close'], f"{r['trade_date'] or '—'}", tone),
        ('涨跌幅', f"{pct}%" if pct is not None else '—',
         f"换手 {r['turnover_rate_pct'] or '—'}%", tone),
        ('操作分', r['oper'] if r['oper'] is not None else '—',
         f"{r['quadrant'] or ''} 排名 {r['rank_in_market'] or '—'}", 'accent'),
        ('X 个股质量', r['x_quality'] if r['x_quality'] is not None else '—',
         'Y 环境共振 ' + (str(r['y_env']) if r['y_env'] is not None else '—'),
         'accent'),
        ('量能温度', r['vol_temp'] if r['vol_temp'] is not None else '—',
         stage or '阶段未算', 'muted' if not stage else 'accent'),
    ]
    # 六个指标块共用一条说明（`card.stock.stat`）：它们讲的是同一件事 ——
    # 这只票的当日快照。逐块各写一条会把同一段口径抄六遍。
    # `note_id` 必须显式给：`ui.stat` 没给 id 时是拿 `hash((label, note))` 兜底的，
    # 而第一块的 label 现在是个 `html.A` 组件（不保证可 hash）。
    return dbc.Row([dbc.Col(ui.stat(a, b, hint=c, tone=t,
                                    help_key='stock.stat' if i == 0 else None,
                                    note_id='note-stock-stat' if i == 0 else None),
                            width=6, lg=2, className='mb-2')
                    for i, (a, b, c, t) in enumerate(cards)], className='g-2')


def _kline(ts_code: str):
    rows = queries.run('q_stock_kline', ts_code=ts_code, lookback=LOOKBACK)
    if not rows:
        return ui.alert('没有日线数据', 'warning')
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.72, 0.28], vertical_spacing=0.03)
    x = [r['trade_date'] for r in rows]
    fig.add_trace(go.Candlestick(
        x=x, open=[r['open'] for r in rows], high=[r['high'] for r in rows],
        low=[r['low'] for r in rows], close=[r['close'] for r in rows],
        increasing_line_color=ui.COLORS['up'],
        decreasing_line_color=ui.COLORS['down'], name='K'), row=1, col=1)
    # 量柱按当天涨跌染色 —— 红量绿量分开才能看出"放量上涨"还是"放量下跌"，
    # 单色量柱在判断量价配合时基本没用。
    colors = [ui.COLORS['up'] if (r['pct_chg'] or 0) >= 0 else ui.COLORS['down']
              for r in rows]
    fig.add_trace(go.Bar(x=x, y=[r['vol_shares'] for r in rows],
                         marker_color=colors, name='量'), row=2, col=1)
    # plot_layout 里已经带了一个 height=None，再传 height= 会撞成
    # "got multiple values for keyword argument 'height'"，所以先剔掉。
    lay = {k: v for k, v in ui.plot_layout().items() if k != 'height'}
    fig.update_layout(**lay, showlegend=False,
                      xaxis_rangeslider_visible=False, height=420)
    return dcc.Graph(figure=fig, config={'displaylogo': False},
                     style={'height': '420px'})


def _parts(ts_code: str):
    """
    评分归因，按功能点分组。

    小标题用 `cap_name`（注册表里的中文名，如「个股质量(X轴)」），不是 `cap_key`
    —— 原来这里直接把 `stock_quality` 这种键名摆在页面上，看不懂。
    """
    rows = queries.run('q_score_parts', ts_code=ts_code)
    if not rows:
        return html.Div('这只票当天没有评分明细（可能还没跑评分）',
                        className='text-muted p-3')
    groups: dict[str, str] = {}
    for r in rows:
        groups.setdefault(r['cap_key'], r.get('cap_name') or r['cap_key'])
    return html.Div([
        html.Div([
            html.Div(name, className='text-muted small mt-2 mb-1'),
            ui.score_panel([r for r in rows if r['cap_key'] == cap]),
        ]) for cap, name in groups.items()])


def _sectors(ts_code: str):
    rows = engine.query(
        """
        SELECT i.sector_type AS 类型, i.name AS 板块, m.sector_code AS 代码,
               sd.pct_chg AS 当日涨幅, sd.member_count AS 成分数
          FROM ref.sector_member m
          JOIN ref.sector_info i ON i.sector_code = m.sector_code
          LEFT JOIN md.sector_daily sd ON sd.sector_code = m.sector_code
               AND sd.trade_date = (SELECT max(trade_date) FROM md.sector_daily)
         WHERE m.ts_code = %s AND m.out_date IS NULL
         ORDER BY i.sector_type, sd.pct_chg DESC NULLS LAST
        """, (ts_code,))
    return ui.table(rows, page_size=8)


def _lhb(ts_code: str):
    rows = engine.query(
        """
        SELECT trade_date AS 日期, reason AS 上榜原因, pct_chg AS 涨跌幅,
               round(net_buy / 1e4, 1) AS 净买万元,
               round(lhb_amount / 1e4, 1) AS 龙虎榜成交万元,
               net_buy_ratio AS 净买占比
          FROM md.lhb_detail WHERE ts_code = %s
         ORDER BY trade_date DESC LIMIT 30
        """, (ts_code,))
    if not rows:
        return html.Div('近期没有龙虎榜记录', className='text-muted p-3')
    return ui.table(rows, page_size=8)


def _score_line(ts_code: str):
    """
    评分历史三条线：操作分、X 个股质量、Y 环境共振。

    为什么要有这张图：卡片上的操作分是**一个时点的数**，看不出"这只票是刚
    转强还是已经在高位横了半个月"。三条线画在一起还能看出分歧 —— X 在涨、
    Y 在跌，说明是个股自己的事，不是环境带的。

    y 轴固定 -100~100 并画一条 0 线：不固定的话每只票的纵轴自适应，两只票
    的图并排看会得出完全错误的强弱印象。
    """
    rows = queries.run('q_score_history', ts_code=ts_code, lookback=LOOKBACK)
    if not rows:
        return html.Div('这只票还没有评分历史（先跑 d2_scores）',
                        className='text-muted p-3')
    x = [r['trade_date'] for r in rows]
    fig = go.Figure()
    for key, label, color in (('oper_score', '操作分', ui.COLORS['accent']),
                              ('stock_quality', 'X 个股质量', ui.COLORS['up']),
                              ('env_resonance', 'Y 环境共振', ui.COLORS['down'])):
        ys = [r[key] for r in rows]
        if all(v is None for v in ys):
            continue
        fig.add_trace(go.Scatter(x=x, y=ys, name=label, mode='lines',
                                 line={'width': 1.6, 'color': color},
                                 connectgaps=False))
    fig.add_hline(y=0, line_width=1, line_dash='dot', line_color='#adb5bd')
    # plot_layout() 里已经有 height 和 legend，重复传会撞成
    # "got multiple values for keyword argument"，所以先剔掉再给自己的值。
    lay = {k: v for k, v in ui.plot_layout().items()
           if k not in ('height', 'legend')}
    fig.update_layout(**lay, height=300, yaxis={'range': [-100, 100]},
                      legend={'orientation': 'h', 'y': 1.12, 'x': 0})
    return dcc.Graph(figure=fig, config={'displaylogo': False},
                     style={'height': '300px'})


# 指标曲线默认画这三条：日均涨跌看趋势方向，日均振幅看波动，明日振幅是外推。
# 三条同在一个量级（个位数百分比），共用一个 y 轴才不会互相压平。
IND_LINES = ('chg10_avg', 'amp10_avg', 'amp_pred')


def _ind_line(ts_code: str):
    """
    指标历史（10 日日均涨跌 / 10 日日均振幅 / 明日振幅预测）。

    竖表分组画线，不在 SQL 里横过来 —— 指标是注册表里的可配项，横过来就等于
    把列名写死在代码里（见 `q_indicator_history` 的说明）。
    """
    rows = queries.run('q_indicator_history', ts_code=ts_code,
                       lookback=LOOKBACK, ind_keys=IND_LINES)
    if not rows:
        return html.Div('这只票还没有因子历史（先跑 d1_indicators）',
                        className='text-muted p-3')
    series: dict[str, list] = {}
    for r in rows:
        series.setdefault(r['ind_key'], []).append((r['trade_date'], r['value']))
    fig = go.Figure()
    palette = {'chg10_avg': ui.COLORS['accent'], 'amp10_avg': ui.COLORS['up'],
               'amp_pred': ui.COLORS['muted']}
    for key in IND_LINES:
        pts = series.get(key)
        if not pts:
            continue
        fig.add_trace(go.Scatter(
            x=[p[0] for p in pts], y=[p[1] for p in pts], mode='lines',
            name=ui.col_label(key),
            line={'width': 1.6, 'color': palette.get(key),
                  'dash': 'dot' if key == 'amp_pred' else 'solid'}))
    fig.add_hline(y=0, line_width=1, line_dash='dot', line_color='#adb5bd')
    lay = {k: v for k, v in ui.plot_layout().items()
           if k not in ('height', 'legend')}
    fig.update_layout(**lay, height=300,
                      legend={'orientation': 'h', 'y': 1.12, 'x': 0})
    return dcc.Graph(figure=fig, config={'displaylogo': False},
                     style={'height': '300px'})


def _body(ts_code: str):
    return html.Div([
        _head(ts_code),
        dbc.Row([
            dbc.Col(ui.card('K 线 + 成交量', _kline(ts_code), height=480,
                            help_key='stock.kline'),
                    width=12, lg=8, className='mb-3'),
            dbc.Col(ui.card('评分归因（分值 × 权重）', _parts(ts_code),
                            height=480, help_key='stock.parts',
                            footer='分项值域 -100~100，条形从中线向两侧长；'
                                   '贡献 = 分值 × 权重。每行点「怎么算的」'
                                   '展开公式、归一化方式与当前参数 —— '
                                   '公式来自脚本，权重与参数来自注册表'),
                    width=12, lg=4, className='mb-3'),
        ], className='g-3'),
        dbc.Row([
            dbc.Col(ui.card('评分走势（近 120 交易日）', _score_line(ts_code),
                            height=380, help_key='stock.score_line',
                            footer='断线＝那天没跑评分，不是 0 分'),
                    width=12, lg=6, className='mb-3'),
            dbc.Col(ui.card('因子走势（近 120 交易日）', _ind_line(ts_code),
                            height=380, help_key='stock.ind_line',
                            footer='10日涨跌/10日振幅是每日均值；明日振幅是'
                                   '20 日趋势外推，只说波动大小、不说方向。'
                                   '曲线只画到已经算出因子的那天为止'),
                    width=12, lg=6, className='mb-3'),
        ], className='g-3'),
        dbc.Row([
            dbc.Col(ui.card('所属板块', _sectors(ts_code), height=300,
                            help_key='stock.sectors'),
                    width=12, lg=6, className='mb-3'),
            dbc.Col(ui.card('龙虎榜记录', _lhb(ts_code), height=300,
                            help_key='stock.lhb'),
                    width=12, lg=6, className='mb-3'),
        ], className='g-3'),
    ])


def label_of(r: dict) -> str:
    """下拉里显示的一行：名称 代码 · 行业/板块。"""
    tail = ' · '.join(x for x in (r.get('board'), r.get('industry')) if x)
    return f"{r.get('name') or '?'}  {r['ts_code']}" + (f'  · {tail}' if tail else '')


def search_rows(text: str | None, limit: int = 30,
                kinds: tuple[str, ...] | None = None) -> list[dict]:
    """
    搜票的原始行（代码 / 6 位数字 / 简称 / 拼音首字母 / 拼音全拼）。

    `kinds` 限制 `asset_type`（如 `('stock', 'etf')`）。自选池要用它把指数和
    板块挡在外面：`999999` 是上证指数，加进自选之后 `md.stock_daily` 里
    join 不到任何行，页面上就是"有这只票但没价没分"。

    前缀命中和**中段命中**都要：只做前缀的话，「茅台」这种把品牌词写在后面的
    搜法搜不到，而人就是这么打的。中段匹配会带进噪声，所以 ORDER BY 里先按
    "命中方式"排（代码前缀 → 名称/拼音前缀 → 中段），再按成交额。

    **按成交额排序而不是按代码**：输入"中"能匹配上百只票，下拉里只放得下
    二三十行，先给的必须是当天真正在交易的大票，否则要找的名字永远在
    第二页 —— 而下拉是没有第二页的。
    """
    q = (text or '').strip()
    if not q:
        return []
    return engine.query(
        """
        SELECT s.ts_code, s.symbol, s.name, s.board, s.pinyin, s.pinyin_abbr,
               s.asset_type,
               (SELECT i.name FROM ref.sector_member m
                  JOIN ref.sector_info i ON i.sector_code = m.sector_code
                 WHERE m.ts_code = s.ts_code AND m.out_date IS NULL
                   AND i.sector_type = '行业'
                 ORDER BY i.name LIMIT 1) AS industry,
               d.amount_yuan
          FROM ref.security s
          LEFT JOIN md.stock_daily d
                 ON d.ts_code = s.ts_code
                AND d.trade_date = (SELECT max(trade_date) FROM md.stock_daily)
         WHERE (%(kinds)s::text[] IS NULL OR s.asset_type = ANY(%(kinds)s))
           AND (upper(s.ts_code)      LIKE upper(%(like)s)
             OR s.symbol              LIKE %(like)s
             OR s.name                LIKE %(like)s
             OR upper(s.pinyin_abbr)  LIKE upper(%(like)s)
             OR s.name                LIKE %(mid)s
             OR s.symbol              LIKE %(mid)s
             OR upper(s.pinyin_abbr)  LIKE upper(%(mid)s)
             OR upper(s.pinyin)       LIKE upper(%(mid)s))
         ORDER BY CASE
                    WHEN upper(s.ts_code) LIKE upper(%(like)s)
                      OR s.symbol LIKE %(like)s THEN 0
                    WHEN s.name LIKE %(like)s
                      OR upper(s.pinyin_abbr) LIKE upper(%(like)s) THEN 1
                    ELSE 2 END,
                  s.is_active DESC, d.amount_yuan DESC NULLS LAST, s.ts_code
         LIMIT %(lim)s
        """, {'like': f'{q}%', 'mid': f'%{q}%', 'lim': limit,
              'kinds': list(kinds) if kinds else None})


def _search_text(r: dict) -> str:
    """一行的可搜文本：代码 + 6 位 + 名称 + 拼音首字母 + 全拼。"""
    return ' '.join(str(x) for x in (r.get('ts_code'), r.get('symbol'),
                                     r.get('name'), r.get('pinyin_abbr'),
                                     r.get('pinyin')) if x)


def search_options(text: str | None, limit: int = 30) -> list[dict]:
    """
    给 `dcc.Dropdown` 用的候选项。

    **每一项都必须带 `search`**：Dropdown 拿到服务端返回的 options 之后，还会
    在浏览器里按 `search`（没写就按 `label`）**再过滤一遍**。label 里没有拼音
    首字母，所以打 `gzmt` 的结果是"服务端明明查到了 1 条，下拉里却显示无结果"
    —— 自选页的加票框就是这么坏的（2026-08-29 查实）。
    """
    return [{'label': label_of(r), 'value': r['ts_code'],
             'search': _search_text(r)} for r in search_rows(text, limit)]



def group_options() -> list[dict]:
    """自选分组下拉。带成员数，空组一眼能看出来。"""
    rows = engine.query(
        """
        SELECT g.group_id, g.name, g.is_focus,
               (SELECT count(*) FROM config.watchlist_stock w
                 WHERE w.group_id = g.group_id) AS n
          FROM config.watchlist_group g
         ORDER BY g.is_focus DESC, g.seq, g.name
        """)
    return [{'label': f"{'★ ' if r['is_focus'] else ''}{r['name']}（{r['n']}）",
             'value': r['group_id']} for r in rows]


def group_stock_options(group_id) -> list[dict]:
    """
    某个自选分组里的股票，按当天 **10 日振幅**从高到低（用户 2026-09-01 指定，
    与自选页的组内成员表 / 自选池明细同一个排序键）。

    label 里同时带振幅和操作分：只写操作分而按振幅排的话，下拉里的顺序看起来
    是乱的 —— 排序键必须是能在标签上看见的那个数。
    """
    if group_id in (None, ''):
        return []
    rows = engine.query(
        """
        SELECT s.ts_code, s.symbol, s.name, s.board, s.pinyin, s.pinyin_abbr,
               round(o.value, 1) AS oper, round(i.value, 2) AS amp10
          FROM config.watchlist_stock w
          JOIN ref.security s ON s.ts_code = w.ts_code
          LEFT JOIN fct.score o
                 ON o.ts_code = w.ts_code AND o.cap_key = 'oper_score'
                AND o.trade_date = (SELECT max(trade_date) FROM fct.score)
          LEFT JOIN fct.indicator_value i
                 ON i.ts_code = w.ts_code AND i.ind_key = 'amp10_avg'
                AND i.trade_date = (SELECT max(trade_date) FROM fct.indicator_value
                                     WHERE ind_key = 'amp10_avg')
         WHERE w.group_id = %s
         ORDER BY i.value DESC NULLS LAST, o.value DESC NULLS LAST, s.ts_code
        """, (group_id,))
    return [{'label': f"{r['name']}  {r['ts_code']}"
                      + (f"  · 振幅 {r['amp10']}" if r['amp10'] is not None else '')
                      + (f"  · 操作分 {r['oper']}" if r['oper'] is not None else ''),
             'value': r['ts_code'],
             'search': _search_text(r)} for r in rows]


def _code_option(code: str) -> list[dict]:
    """
    单个代码 → 下拉里的一项，label 尽量带上名字。

    只塞 `{'label': code, 'value': code}` 的话，框里显示的是 `600519.SH` 这串
    数字，看不出是哪只票 —— 而恢复"上次看的那只"时，人要的正是那个名字。
    """
    rows = search_rows(code, limit=1)
    if rows and rows[0]['ts_code'] == code:
        return [{'label': label_of(rows[0]), 'value': code,
                 'search': _search_text(rows[0])}]
    return [{'label': code, 'value': code}]


def _coverage() -> dict:
    """
    这一页的数据覆盖面。**一次查询**，用来在页顶给出真数字。

    写死"全市场都有"是不够的 —— 评分是 `d2_scores` 跑出来的，哪天没跑
    哪天就没有，而页面上"没分"和"这只票不支持"长得一模一样。

    数票用 `cap_key='oper_score'` 的行数，不用 `count(DISTINCT ts_code)`：
    `fct.score` 主键含 cap_key，一只票一天只有一行 oper_score，结果一样
    （都是 5,547），但 0.003s vs 0.193s —— 这段是首屏同步跑的。
    """
    return engine.query_one(
        """
        SELECT (SELECT max(trade_date) FROM fct.score)            AS d,
               (SELECT count(*) FROM fct.score
                 WHERE cap_key = 'oper_score'
                   AND trade_date = (SELECT max(trade_date) FROM fct.score))
                                                                  AS scored,
               (SELECT count(*) FROM ref.security
                 WHERE asset_type = 'stock' AND is_active)         AS stocks,
               (SELECT count(DISTINCT ts_code) FROM config.watchlist_stock)
                                                                  AS watched
        """) or {}



def _intro():
    """
    页顶说明。折叠用 `html.Details`（浏览器原生），不占回调也不占首屏高度。

    非要写这一段的原因：页上并排放着「搜个股」和「从自选分组里挑」两个下拉，
    看起来像"只有自选的票才有这些图"。实际不是 —— 评分、因子、归因都是
    **全市场**逐票算的，自选那个下拉只是快捷入口。
    """
    c = _coverage()
    line = (f"这一页对**全市场任意一只票**都可用，不限自选。"
            f"{c.get('d') or '—'} 有评分的票 "
            f"**{c.get('scored') or 0} / {c.get('stocks') or 0}** 只在市个股；"
            f"自选池只有 {c.get('watched') or 0} 只，它只是右边那个下拉的快捷入口。")
    return html.Details([
        html.Summary('这一页的数据是怎么来的 · 覆盖到哪（点开）',
                     className='small text-muted', style={'cursor': 'pointer'}),
        dcc.Markdown(
            line + '\n\n'
            '**六张卡各自的来源**\n\n'
            '- 顶部指标条 / 评分归因 / 评分走势：`fct.score` + `fct.score_detail`，'
            '由 `d2_scores.py` 对**全市场每一只票**逐票算，不看自选。\n'
            '- K 线 + 成交量：`md.stock_daily`（通达信日线）。\n'
            '- 因子走势：`fct.indicator_value`，由 `d1_indicators.py` 全市场算。\n'
            '- 所属板块：`ref.sector_member ⋈ md.sector_daily`。\n'
            '- 龙虎榜：`md.lhb_detail`，只有真上过榜才有行。\n\n'
            '**所以"没数"只有三种原因**：那只票当天没交易（停牌/退市）、'
            '那天的 `d1/d2` 还没跑、或者它压根不是股票'
            '（指数和板块也能搜到，但 `md.stock_daily` 里没有它们）。'
            '**不是因为它不在自选里。**\n\n'
            '**搜索框是服务端边打边出**（代码 / 6 位数字 / 简称 / 拼音首字母 / 全拼，'
            '前缀和中段都匹配，按当日成交额排序）。'
            '⚠️ 因此**网站进程停了的话，这个框会表现成"打字没反应"** —— '
            '不是输入框坏了，是没人应答。这种时候先看 `/admin` 或重启网站。\n\n'
            '页面记得**上次看过的那只票**（存浏览器 localStorage），'
            '不带 `?code=` 进来时默认打开它。',
            className='small text-muted p-2 mt-1',
            style={'background': '#f8f9fa', 'borderRadius': '4px'}),
    ], className='mb-2')


def render(params: dict | None = None):

    """
    `params` 是 URL 查询串（`/stock?code=600519.SH`，`ui.detail_link` 造的链接）。

    深链要在**首屏就渲染出目标票**，不是先画一只默认票再靠回调换掉：靠回调的话
    页面会闪一下别的票，而且 `stock-pick` 的 value 输出已经被"按分组选票"
    那个回调占了，再加一个写同一个 Output 的回调 Dash 直接拒绝启动。

    **没带 `?code=` 时不画默认票，而是等浏览器把"上次看过的那只"报上来。**
    上次看的是哪只存在 `dcc.Store(storage_type='local')` 里 ——
    ★没存进 `config.setting`：`config.*` 每张表都挂了 `ops.config_history` 行级触发器
    （见 `04_constraints.sql`），把"我刚看了哪只票"写进去等于每翻一只票就往
    配置变更历史里塞一行，那张表是用来回答"谁把配置改坏了、怎么回退"的，
    被浏览记录灌满就没法用了。
    浏览器本地存也够用：这套东西目前只绑 127.0.0.1、单人用。

    首屏因此**不阻塞**：先返回一句"正在打开…"的骨架，值由 `stock-init`
    这个一次性 `dcc.Interval` 触发后填进来，再由 `_show` 渲染正文。
    """
    code = _resolve((params or {}).get('code'))
    return html.Div([
        _intro(),

        # 上次看过的那只票。storage_type='local' —— 关掉浏览器再开也还在。
        dcc.Store(id='stock-last', storage_type='local'),
        # 一次性触发器：localStorage 的值只有浏览器知道，服务端首屏读不到，
        # 必须等页面挂载后由它把值送回来。`prevent_initial_call` 挡掉了
        # Store 自己的初始回调，所以用 Interval 明确地打这一下。
        dcc.Interval(id='stock-init', interval=200, max_intervals=1),
        dbc.Row([
            dbc.Col([
                html.Div('搜个股（代码 / 名称 / 拼音首字母，边打边出）',
                         className='text-muted small mb-1'),
                dcc.Dropdown(id='stock-pick', value=code,
                             options=_code_option(code) if code else [],
                             placeholder='比如 600519 / 贵州茅台 / gzmt',
                             searchable=True, clearable=False,
                             optionHeight=42),
            ], width=12, lg=5),
            dbc.Col([
                html.Div('或者从自选分组里挑', className='text-muted small mb-1'),
                dcc.Dropdown(id='stock-group', options=group_options(),
                             placeholder='选一个自选分组',
                             searchable=False, clearable=True),
            ], width=12, lg=4),
            dbc.Col(html.Div(id='stock-msg', className='small text-muted'),
                    className='d-flex align-items-end pb-1'),
        ], className='g-2 mb-3'),
        dcc.Loading(html.Div(
            _body(code) if code
            else html.Div('正在打开上次看过的那只票…',
                          className='text-muted p-4'),
            id='stock-body'), type='default'),
    ])


def register_callbacks(app) -> None:
    @app.callback(Output('stock-pick', 'options'),
                  Output('stock-pick', 'value'),
                  Input('stock-pick', 'search_value'),
                  Input('stock-group', 'value'),
                  Input('stock-init', 'n_intervals'),
                  State('stock-last', 'data'),
                  State('stock-pick', 'value'),
                  prevent_initial_call=True)
    def _pick(search, group_id, _n, last, cur):
        """
        下拉的 options 和 value 一起管。

        **必须合成一个回调**：Dash 3 里一个 Output 只允许一个回调写，而
        "打字搜全市场"、"选了分组就换成组内成员"、"首屏恢复上次看的那只"
        三件事都要动这两个属性。

        搜索走服务端 —— 5,500 只票一次性塞进 options 会让首屏多背几百 KB，
        而且拼音首字母是库里的列，浏览器端匹配不到。
        """
        trig = callback_context.triggered_id
        if trig == 'stock-init':
            # 带了 ?code= 的深链，首屏已经画好了，别去动它
            if cur:
                return no_update, no_update
            code = _resolve(last) or DEFAULT_CODE
            return _code_option(code), code
        if trig == 'stock-group':
            opts = group_stock_options(group_id)
            if not opts:
                return no_update, no_update
            return opts, opts[0]['value']
        text = (search or '').strip()
        return (search_options(text) if text else no_update), no_update

    @app.callback(Output('stock-last', 'data'),
                  Input('stock-pick', 'value'),
                  Input('stock-init', 'n_intervals'),
                  prevent_initial_call=True)
    def _remember(value, _n):
        """
        把当前这只票记进 localStorage，下次不带参数进这一页就默认它。

        `stock-init` 那一路是给**深链**留的：`/stock?code=X` 首屏就把值定死了，
        `stock-pick.value` 不会变、这个回调也就不会被它触发 —— 不补这一下，
        从别的页面点「详情」跳过来看的票记不住。
        """
        code = _resolve(value)
        return code or no_update

    @app.callback(Output('stock-body', 'children'),
                  Output('stock-msg', 'children'),
                  Input('stock-pick', 'value'),
                  prevent_initial_call=True)
    def _show(value):
        ts_code = _resolve(value)
        if ts_code is None:
            return ui.alert(f'找不到「{value}」。试试完整代码，或换个关键词。',
                            'warning'), ''
        return _body(ts_code), f'当前：{ts_code}'


