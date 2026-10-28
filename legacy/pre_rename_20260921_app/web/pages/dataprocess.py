# -*- coding: utf-8 -*-
"""
运维 · 「数据加工」面板 —— 挂在 /data 的第一个标签。

回答的问题是"拿到数据之后怎么变成分数"：

- **一键数据加工** = 先拉数据再全部跑分。背后是 `fill_all` 那条整链
  （`scripts/ops/e1_fill_all.py`），它把日线/辅助源/因子/评分/后台分析一路按
  STAGES 跑完。勾上「P0 就地跑分」时，日线+快照一入库就**先**跑 d1 字段分值，
  不等两融/龙虎榜那些网络源 —— 评分在 P0 数据阶段就有字段了。
- **按计算维度拆的 7 个功能点** = 每个只跑一个 `compute_*` 执行器
  （d1 字段分值 / d2 评分 / d3 板块热度 / d4 启动点 / d5 趋势分 / d6 情绪 /
  d7 倍量震荡）。想单独补某一段、不想重跑整条链时用。

**顺序/依赖不在这里排**：一键那段的先先后后由 `e1_fill_all.py` 的 STAGES 负责，
页面只负责"提交 + 回显状态"，不自造第二套顺序 —— 否则迟早跟脚本对不上。
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, callback_context, dcc, html

from app.db.repo import security as sec_repo
from app.orchestrator import runner
from app.web import ui

#: 一键数据加工 = 整条追平链（含全部跑分）。
PROCESS_ALL = 'fill_all'

#: 7 个功能点按钮：job_key / 页面文案。job_key 是 config.job 里登记好的
#: （calc_base→compute_indicators … calc_vol_double→compute_vol_double），
#: 由它跑对应的 compute_* 执行器 —— 见 app/orchestrator/runner.py 的 RUNNERS。
PROCESS_POINTS = [
    ('calc_base',           'P0 字段分值 (d1)'),
    ('calc_scores',         '评分 (d2)'),
    ('calc_sector_trend',   '板块热度 (d3)'),
    ('calc_ignition',       '启动点 (d4)'),
    ('calc_score_trend',    '趋势分 (d5)'),
    ('calc_breadth',        '情绪 (d6)'),
    ('calc_vol_double',     '倍量震荡 (d7)'),
]


def _trigger(job_key: str, day: str | None, extra: dict | None = None):
    """提交一个作业，返回给页面看的那行字。异步（wait=False），点完立刻返回。"""
    try:
        res = runner.run_job(job_key, trade_date=day, actor='web',
                             extra=extra, wait=False)
    except Exception as exc:                     # noqa: BLE001
        return html.Span(f'触发失败：{exc}', className='text-danger')
    tone = 'text-warning' if res['task_id'] is None else 'text-primary'
    return html.Span(f"{job_key} → task {res['task_id']}："
                     f"{res['message'] or res['state']}", className=tone)


def panel():
    """数据加工面板（/data 的第一个标签）。"""
    d = sec_repo.latest_trade_date()
    return html.Div([
        dcc.Interval(id='dp-poll', interval=3000, disabled=True),
        ui.alert('**一键数据加工** = 先拉数据再全部跑分（走 `fill_all` 整条链）。'
                 '勾上「P0 就地跑分」时，日线+快照入库后就**先**跑 d1 字段分值，'
                 '不等两融/龙虎榜这些网络源 —— 评分在 P0 数据阶段就有字段了。'
                 '7 个功能点按钮是给想单独补某一段用的。', 'light'),
        dbc.Row([
            dbc.Col(dbc.Button('一键数据加工（获取数据 + 全部跑分）',
                               id='dp-all', color='success', size='md',
                               n_clicks=0), width='auto'),
            dbc.Col(dbc.Checkbox(id='dp-p0instant', value=True,
                                 label='P0 就地跑分（--p0-instant）'),
                    width='auto', className='d-flex align-items-center'),
            dbc.Col(dbc.InputGroup([
                dbc.InputGroupText('交易日', className='py-1'),
                dbc.Input(id='dp-date', value=str(d) if d else '', size='sm',
                          placeholder='YYYY-MM-DD，留空=最新交易日'),
            ]), width=12, lg=3),
            dbc.Col(html.Div(id='dp-msg', className='small'),
                    className='d-flex align-items-center'),
        ], className='g-2 mb-2'),
        ui.alert('「一键数据加工」的目标日**由 fill_all 自己算**（同步日历 → 15:30 '
                 '收盘线），上面那个交易日输入框只对单行功能点按钮生效。', 'light'),
        ui.card('按计算维度拆的功能点（每步只跑一个 compute_* 执行器）',
                dbc.Row([
                    dbc.Col(dbc.Button(label, id={'type': 'dp-point',
                                                  'key': key},
                                       color='info', size='sm', n_clicks=0,
                                       className='mb-2'),
                            width='auto')
                    for key, label in PROCESS_POINTS]),
                help_key='dataprocess.points',
                footer='执行器白名单在 app/orchestrator/runner.py 的 RUNNERS；'
                       '作业绑定见 config.job.params.runner'),
    ])


def register_callbacks(app) -> None:
    # 按钮与轮询共用一个回调：Dash 不允许两个回调写同一个 Output，
    # 合成一个之后"点完立刻刷一次"和"每 3 秒刷一次"走的是同一段代码，不会走岔。
    @app.callback(Output('dp-msg', 'children'),
                  Output('dp-poll', 'disabled'),
                  Input('dp-all', 'n_clicks'),
                  Input({'type': 'dp-point', 'key': ALL}, 'n_clicks'),
                  Input('dp-poll', 'n_intervals'),
                  Input('dp-p0instant', 'value'),
                  State('dp-date', 'value'),
                  prevent_initial_call=True)
    def _run(_all_clicks, _point_clicks, _ticks, p0instant, date_text):
        trig = callback_context.triggered_id
        msg = None
        if trig == 'dp-all':
            # 不传 trade_date：fill_all 自己算目标日（同步日历 → 15:30 收盘线）。
            # p0-instant 让 d1 在日线+快照入库后就地跑，不等网络源。
            extra = {'p0-instant': bool(p0instant)}
            if runner.running_tasks():
                msg = html.Span('还有采集在跑，等它结束再点。',
                                className='text-warning')
            else:
                msg = _trigger(PROCESS_ALL, None, extra)
        elif isinstance(trig, dict) and trig.get('type') == 'dp-point':
            day = (date_text or '').strip() or None
            msg = _trigger(trig.get('key'), day)

        runner.reap_orphans()
        running = runner.running_tasks()
        if msg is None:
            msg = html.Span(
                ('正在跑：' + '、'.join(str(t['job_key']) for t in running))
                if running else '当前没有正在执行的加工。',
                className='text-primary' if running else 'text-muted')
        return msg, not running