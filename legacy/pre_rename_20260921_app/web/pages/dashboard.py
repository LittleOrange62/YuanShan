# -*- coding: utf-8 -*-
"""
复盘首页 —— **唯一的复盘页**。

2026-08-29 把原来的「每日复盘」`/review` 整个并进这里了。原因很直接：
两页的卡片高度重叠（涨跌幅分布、四象限、板块、明细），一天要点两个地方看
同一批数字；而首页只有指标块、真正干活的图全在另一页，首页就成了一个必须
再点一次的中转站。现在一页到底：**体检 → 选交易日 → 全套复盘卡片 → 今日任务**。

三样东西是这一页自己的（其余全部由 `config.page_layout` page_key='home' 驱动）：

1. **系统体检**（顶部那排数字 + 提醒）是硬编码的，且**不跟随所选交易日** ——
   它回答"库里到底有没有数据、到哪一天了"，配置或评分出问题时它还得能显示。
2. **交易日下拉 + 冗余横幅**。复盘最常做的动作是"上周五双高的是哪些票"，
   所以整页跟着一个日期走；横幅再把实际生效的日期写一遍，库里缺那天评分时
   会退回最新日，不写出来就看不出来。
3. **每张卡片各自异步加载**。九张卡顺序渲染实测 5.9 秒（板块热度趋势 2.1s、
   情绪时序 1.4s、热度榜 1.0s），换一次日期就白等 6 秒。骨架先返回、
   每张卡一个 `MATCH` 回调，Dash 为每张卡发独立请求，waitress 8 线程并行跑，
   快的先出来，慢的只拖自己那一张。

日期从 `fct.score` 取，不是 `md.stock_daily`：有行情不等于算过评分，
四象限和板块热度都是评分的产物，把没评分的日子列进下拉，选完只会看到空图。
"""
from __future__ import annotations

import datetime as dt

import dash_bootstrap_components as dbc
from dash import MATCH, Input, Output, State, dcc, html

from app.db import engine
from app.db.repo import registry as reg_repo
from app.web import queries, ui

PAGE_KEY = 'home'
# 卡片容器的 id 类型。回调用 MATCH 按 slot 对上，所以 slot 必须唯一 ——
# 它本来就是 config.page_layout 的主键之一。
CARD_ID = 'home-card'


def _overview() -> list:
    q = engine.query_one(
        """
        SELECT (SELECT count(*) FROM md.stock_daily)                    AS daily_rows,
               (SELECT count(*) FROM md.index_daily)                    AS index_rows,
               (SELECT count(*) FROM md.sector_daily)                   AS sector_rows,
               (SELECT count(*) FROM md.stock_minute)                   AS minute_rows,
               (SELECT count(*) FROM ref.security WHERE asset_type='stock')
                                                                        AS stocks,
               (SELECT max(trade_date) FROM md.stock_daily)             AS last_date,
               (SELECT count(DISTINCT trade_date) FROM md.stock_daily)  AS trade_days,
               (SELECT count(*) FROM fct.score)                         AS scores,
               (SELECT count(*) FROM fct.score WHERE is_stale)          AS stale,
               (SELECT count(*) FROM config.watchlist_stock)               AS watch
        """) or {}

    def fmt(n):
        return f'{n:,}' if isinstance(n, int) else (n or '—')

    cards = [
        ('日线行数', fmt(q.get('daily_rows')), f"最新 {q.get('last_date') or '—'}"),
        ('交易日数', fmt(q.get('trade_days')), '来自实际有数据的日期'),
        ('个股数', fmt(q.get('stocks')), 'ref.security asset_type=stock'),
        ('指数 / 板块日线', f"{fmt(q.get('index_rows'))} / {fmt(q.get('sector_rows'))}",
         '已与个股分表'),
        ('分钟线', fmt(q.get('minute_rows')), '待从 vipdoc 重解析'),
        ('评分记录', fmt(q.get('scores')),
         f"其中待重算 {fmt(q.get('stale'))}"),
        ('自选股', fmt(q.get('watch')), '迁自旧系统'),
    ]
    return [dbc.Col(ui.stat(a, b, hint=c), width=6, md=4, lg=3, className='mb-3')
            for a, b, c in cards]


def _health_notes() -> list:
    """
    体检提醒。**只报真的能补的缺口** —— 报一个永远补不上的数字，
    等于每天都在喊狼来了，最后连真的告警也没人看。

    三条提醒在 2026-08-27 全部核过一遍：
      · 涨跌停：`limit_up IS NULL` 的 5,581 行**全部** `pre_close` 也是 NULL，
        也就是每只票的上市首日 —— 首日没有前收盘，涨跌停价本来就不存在。
        所以这里按"缺前收盘"分开数，跑 b6_reconcile.py 也不会变。
      · 换手率：缺口曾是 254,617 行 / 351 个代码（339 个北交所 + 12 只新股），
        已由 c9_share.py 从新浪日线补齐（自带每日流通股本）。
      · 复权因子：曾经全表为空，已由 c8_dividend.py 采分红送配后回算。
    """
    notes = []
    row = engine.query_one(
        """
        SELECT count(*) FILTER (WHERE turnover_rate_pct IS NULL) AS no_turnover,
               count(*) FILTER (WHERE limit_up IS NULL
                                 AND pre_close IS NOT NULL)      AS no_limit,
               count(*) FILTER (WHERE limit_up IS NULL
                                 AND pre_close IS NULL)          AS first_day,
               count(*) FILTER (WHERE adj_factor IS NULL)        AS no_adj,
               count(*)                                          AS total
          FROM md.stock_daily
        """) or {}
    total = row.get('total') or 0
    if not total:
        return [ui.alert('md.stock_daily 还没有数据，先跑迁移脚本 b3_daily.py',
                         'warning')]
    if row.get('no_limit'):
        notes.append(ui.alert(
            f"涨跌停未回算 {row['no_limit']:,} 行（有前收盘却没算出来）。"
            '跑 b6_reconcile.py 按板块规则回算。', 'warning'))
    if row.get('first_day'):
        notes.append(ui.alert(
            f"另有 {row['first_day']:,} 行没有涨跌停价，是各股上市首日"
            '（无前收盘，结构性如此，不用管）。', 'light'))
    if row.get('no_turnover'):
        notes.append(ui.alert(
            f"换手率缺失 {row['no_turnover']:,} 行"
            f"（{row['no_turnover'] / total:.1%}）。"
            '跑 scripts/ingest/c9_share.py 从新浪补每日换手率与流通股本。',
            'warning'))
    if row.get('no_adj'):
        share = row['no_adj'] / total
        tone = 'danger' if share > 0.5 else 'light'
        notes.append(ui.alert(
            f"复权因子为空 {row['no_adj']:,} 行（{share:.0%}）——"
            '这些票在 ref.dividend 里没有分红送配记录，未复权等于不影响；'
            '有送转记录的票已回算。跑 c8_dividend.py 可再采一次。', tone))
    return notes


# ---------------------------------------------------------------- 交易日

def date_rows(limit: int = 30) -> list[dict]:
    """有评分的交易日，最近的在前。"""
    return queries.q_score_dates(limit=limit)


def options_of(rows: list[dict]) -> list[dict]:
    """
    日期下拉的选项。标签里带上当天参与评分的只数 —— 数字明显偏小的那天
    （比如只有几十只）说明那天评分没跑全，选之前就能看出来，
    而不是选完对着一张稀疏的散点图猜。
    """
    return [{'label': f"{r['trade_date']}    {r['stocks']:,} 只"
                      + ('  · 最新' if i == 0 else ''),
             'value': str(r['trade_date'])}
            for i, r in enumerate(rows)]


def _pick(value) -> dt.date | None:
    """
    下拉给回来的是字符串，**这里统一转成 date 再往下传**。
    命名查询里有几处拿 trade_date 直接参与日期运算，喂字符串会炸在 SQL 层，
    报错还指不回这一步。取不到就退回最新有数据的交易日。
    """
    if value:
        try:
            return dt.date.fromisoformat(str(value))
        except ValueError:
            pass
    return queries.latest_date()


def _banner(value):
    """
    整页数据实际是哪一天的。**这一行是刻意冗余的**：日期已经在下拉里了，
    但下拉说的是"我选了什么"，横幅说的是"卡片里的数字是哪天的"，
    库里缺那天的评分时两者会不一致（会退回最新日），这时候必须看得出来。

    顶部的系统体检不受它影响，那一段永远是库里的当前状态。
    """
    d = _pick(value)
    if d is None:
        return ui.alert('`fct.score` 里还没有任何评分，先跑 '
                        '`python scripts\\compute\\d2_scores.py --recent 1`',
                        'warning')
    n = next((r['stocks'] for r in date_rows() if r['trade_date'] == d), None)
    return dbc.Alert(
        [html.Span('下面全部卡片（指标块、四象限散点、双高名单…）的数据日期：'),
         html.Strong(str(d)),
         html.Span(f'　参与评分 {n:,} 只' if n else '　这一天没有评分记录',
                   className='text-muted small'),
         html.Span('　（顶部系统体检不随日期变，它说的是库里的当前状态）',
                   className='text-muted small')],
        color='light', className='py-2 mb-3 border')


def _skeleton(layout_rows: list[dict]):
    """
    骨架：每张卡片一个空容器 + 各自的 `dcc.Loading`。内容由回调填。

    高度先按配置占好位，避免卡片陆续到达时页面上下乱跳。
    """
    cols = []
    for r in layout_rows:
        if not r.get('visible', True):
            continue
        cols.append(dbc.Col(
            dcc.Loading(html.Div(id={'type': CARD_ID, 'slot': r['slot']},
                                 style={'minHeight': f"{r.get('height') or 320}px"}),
                        type='default'),
            width=12, lg=min(r.get('width') or 6, ui.GRID), className='mb-3'))
    return dbc.Row(cols, className='g-3')


def render():
    layout_rows = reg_repo.layout(PAGE_KEY)
    opts = options_of(date_rows())
    return html.Div([
        html.H6('系统体检', className='text-muted'),
        dbc.Row(_overview(), className='g-2'),
        html.Div(_health_notes()),
        html.Hr(),
        dbc.Row([
            dbc.Col([
                html.Div('交易日（可回看历史，下面整页跟着变）',
                         className='text-muted small mb-1'),
                dcc.Dropdown(id='home-date', options=opts,
                             value=opts[0]['value'] if opts else None,
                             clearable=False, searchable=True, optionHeight=38),
            ], width=12, lg=4),
        ], className='g-2 mb-3'),
        html.Div(id='home-banner'),
        _skeleton(layout_rows) if layout_rows else ui.alert(
            f'page_key = {PAGE_KEY} 还没有配卡片。到「积木管理 → page_layout」加。',
            'secondary'),
    ])


def register_callbacks(app) -> None:
    @app.callback(Output('home-banner', 'children'),
                  Input('home-date', 'value'))
    def _fill_banner(value):
        return _banner(value)

    @app.callback(Output({'type': CARD_ID, 'slot': MATCH}, 'children'),
                  Input('home-date', 'value'),
                  State({'type': CARD_ID, 'slot': MATCH}, 'id'))
    def _fill_card(value, cid):
        """
        一张卡片一次回调。**慢查询只拖它自己那张卡**，这是这一页不阻塞的关键。

        布局行在回调里重新取：卡片配置随时可能在「积木管理」里被改，
        闭包捕获会让改完必须重启才生效。
        """
        slot = (cid or {}).get('slot')
        row = next((r for r in reg_repo.layout(PAGE_KEY)
                    if r['slot'] == slot), None)
        if row is None:
            return ui.alert(f'slot {slot} 已经不在 config.page_layout 里了，刷新本页',
                            'secondary')
        charts = {c['chart_key']: c for c in reg_repo.charts()}
        return ui.render_card(row, charts, params={'trade_date': _pick(value)})
