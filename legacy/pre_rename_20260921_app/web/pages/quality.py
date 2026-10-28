# -*- coding: utf-8 -*-
"""
数据质量与口径契约 —— 现在是 `/data`「数据管理」的第二个标签，不再是独立一页。

这一块是第四大块的门面：字段单位在哪儿定义的、校验规则跑没跑、结果如何。
旧系统所有的事故都是"静默的数据错误"而不是"缺功能"，所以它存在的意义
是让口径问题**可见**。

2026-08-30 并进 `/data`：两页问的是同一件事的两半 —— "数据到齐了吗"（批次、
行数、覆盖度）和"到的数据对吗"（校验、口径）。分两页的结果是每天要点两个
地方、而且两边都得先想一下"这个问题该去哪一页看"。这里只保留 `panel()`，
页面壳子由 `data.py` 提供。

2026-08-30 第二处改动 —— **审计从"声明"变成"真的会跑"**：在这之前
`ops.quality_result` 里只有 2 行、`trade_date` 全是 NULL（迁移对账顺手写的），
页面上那句「最近一次 2026-08-26 17:54」是**迁移那天的时间戳**，不是"08-26 的
数据体检过了"。24 条规则登记在册却没有任何执行器，看板一片绿而实际什么都没查。
现在这一页顶部先回答三个问题：现在几点（北京时间）、最近一个已收盘交易日是哪天、
审计落后几天；落后就**打开页面自动补**（异步，一天一次地补），也可以手动点。
"""
from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import Input, Output, callback_context, dcc, html

from app.db import engine
from app.orchestrator import runner
from app.quality import audit
from app.web import queries, ui

#: 这一页触发的审计作业。执行器绑定在 `config.job.params.runner` 里。
JOB_KEY = 'quality_audit'


def _rules():
    return engine.query(
        """
        SELECT rule_key AS 规则, name AS 名称, target AS 作用对象,
               severity AS 级别, enabled AS 启用
          FROM config.quality_rule ORDER BY severity, rule_key
        """)


def _summary():
    q = engine.query_one(
        """
        SELECT count(*)                                      AS 总记录,
               count(*) FILTER (WHERE NOT passed)             AS 未通过,
               count(*) FILTER (WHERE severity = 'error' AND NOT passed) AS 阻断级,
               max(run_at)                                   AS 最近一次,
               max(trade_date)                               AS 审计到
          FROM ops.quality_result
        """) or {}
    cards = [
        ('校验记录', f"{q.get('总记录') or 0:,}", ''),
        ('未通过', f"{q.get('未通过') or 0:,}",
         'error 级 %s' % (q.get('阻断级') or 0)),
        # 「最近一次」只报时间会骗人（迁移那天的时间戳看着也很"新"），
        # 所以后面必须跟着"审计到哪个交易日"。
        ('最近一次', str(q.get('最近一次') or '—')[:19],
         '审计到 %s' % (q.get('审计到') or '还没有按交易日审计过')),
        ('口径字段数', engine.scalar('SELECT count(*) FROM config.field_spec'),
         '单位与口径的唯一定义处'),
    ]
    keys = ['quality.stat.校验记录', 'quality.stat.未通过',
            'quality.stat.最近一次', 'quality.stat.口径字段数']
    return [dbc.Col(ui.stat(a, b, hint=c, help_key=k,
                            tone='down' if a == '未通过' and (
                                q.get('未通过') or 0) else 'accent'),
                    width=6, lg=3, className='mb-3')
            for (a, b, c), k in zip(cards, keys, strict=False)]


def _running() -> list[dict]:
    return [t for t in runner.running_tasks() if t['job_key'] == JOB_KEY]


def _gap_panel():
    """
    审计缺口。**只读**：算这个不写库、不联网，页面渲染多快都无所谓。

    判据与「一键追平」共用 `app/orchestrator/tradeday.py`（收盘线 15:30 →
    日历里 `is_open` 且 ≤ 上限的最大那天），不抄第二份：抄两份的结果是
    这一页写 08-28、脚本按 08-27 跑，两边都"看起来对"。
    """
    g = audit.gap()
    now = g['now'].strftime('%Y-%m-%d %H:%M:%S')
    target = g['target']
    n = len(g['runnable'])
    tone = 'down' if n else 'up'
    stats = [
        ('北京时间', now[11:], now[:10]),
        ('最近已收盘交易日', str(target or '—'), g['why']),
        ('已审计到', str(g['audited_max'] or '从未'),
         '按交易日的审计记录' ),
        ('落后', f'{n} 天' if n else '已追平',
         ('待审：' + '、'.join(str(d) for d in g['runnable'])) if n
         else '和最近已收盘交易日一致'),
    ]
    body = [dbc.Row([
        dbc.Col(ui.stat(a, b, hint=c, tone=tone if a == '落后' else 'accent'),
                width=6, lg=3, className='mb-2')
        for a, b, c in stats
    ], className='g-2')]
    if g['no_data']:
        days = '、'.join(str(d) for d in g['no_data'])
        body.append(ui.alert(
            f'这 {len(g["no_data"])} 天在日历上是交易日、但库里还没有日线，'
            f'**先去 /jobs 一键追平**，审计跳过它们'
            f'（"数据还没采"和"质量不合格"是两件事）：{days}',
            'warning'))
    return html.Div(body)


def _controls():
    running = _running()
    return dbc.Row([
        dbc.Col(dbc.Button('立即补齐审计', id='q-audit', color='primary',
                           size='sm', n_clicks=0, disabled=bool(running),
                           title='缺几天就逐天各跑一次，异步执行，不阻塞页面'),
                width='auto'),
        dbc.Col(html.Div(id='q-msg', className='small'),
                className='d-flex align-items-center'),
    ], className='g-2 mb-2')


def panel():
    """质量这一块的卡片。由 `data.py` 的「② 数据质量」标签调。"""
    return html.Div([
        # 1.5 秒后第一次 tick：那一下负责"打开页面就自动补齐"（见 register_callbacks）。
        # 不在 panel() 里直接触发 —— 渲染函数有副作用的话，任何一次刷新都会往库里写。
        dcc.Interval(id='q-poll', interval=1500, disabled=False),
        dbc.Row(_summary(), className='g-2'),
        ui.card('审计缺口（打开这一页就会自动补齐）',
                html.Div([html.Div(_gap_panel(), id='q-gap'), _controls()]),
                help_key='quality.gap',
                footer='判据：当前北京时间 → 收盘线 15:30 → 日历里最后一个开市日；'
                       '缺几天就逐天各跑一次 e2_quality_audit.py'),
        ui.alert('字段单位以 config.field_spec 为唯一来源：库里的列注释、文档里的'
                 '字段字典、落库前的校验，三处都由它生成，所以不会互相漂移。',
                 'light'),
        dbc.Row([
            dbc.Col(ui.card('最近校验结果',
                            html.Div(ui.table(queries.q_quality_recent(40)),
                                     id='q-recent'),
                            help_key='quality.recent'),
                    width=12, lg=6, className='mb-3'),
            dbc.Col(ui.card('校验规则登记表（config.quality_rule）',
                            ui.table(_rules()),
                            help_key='quality.rules'),
                    width=12, lg=6, className='mb-3'),
        ], className='g-3'),
        ui.card('字段口径字典（ops.v_field_dict）',
                ui.table(queries.q_field_dict(), page_size=20),
                help_key='quality.fields',
                footer='改这里的单位，库里的列注释与文档会一起变'),
    ])


def register_callbacks(app) -> None:
    """
    一个回调管三件事：打开页面自动补、手动点补、跑完刷新。

    **自动触发只认第一次 tick**（`n_intervals == 1`）。挂在后续 tick 上会变成
    "只要页面开着就反复提交"，而且第一次跑完 `no_data` 那些天仍然算缺口 ——
    死循环。之后的 tick 只负责刷新与在没任务时停掉轮询。
    """
    @app.callback(Output('q-gap', 'children'),
                  Output('q-recent', 'children'),
                  Output('q-msg', 'children'),
                  Output('q-audit', 'disabled'),
                  Output('q-poll', 'disabled'),
                  Input('q-audit', 'n_clicks'),
                  Input('q-poll', 'n_intervals'),
                  prevent_initial_call=True)
    def _audit(_clicks, ticks):
        trig = callback_context.triggered_id
        runner.reap_orphans()
        msg = None
        running = _running()
        want = trig == 'q-audit' or (trig == 'q-poll' and ticks == 1)
        if want and not running:
            g = audit.gap()
            if not g['runnable']:
                msg = html.Span(
                    f"已经审计到最近一个已收盘交易日（{g['target']}），没有要补的。",
                    className='text-success')
            else:
                res = runner.run_job(JOB_KEY, actor='web', wait=False)
                if res['task_id'] is None:
                    msg = html.Span(f"没能提交：{res['message']}",
                                    className='text-warning')
                else:
                    how = '打开页面自动' if trig == 'q-poll' else ''
                    days = '、'.join(str(d) for d in g['runnable'])
                    msg = html.Span(
                        f"{how}补齐 {len(g['runnable'])} 天（{days}）已提交，"
                        f"task {res['task_id']}，跑完这一页自己刷新。",
                        className='text-primary')
                running = _running()
        if msg is None:
            if running:
                msg = html.Span(
                    f"正在审计（task {running[0]['task_id']}，"
                    f"{str(running[0]['started_at'])[:19]} 起）…",
                    className='text-primary')
            else:
                last = engine.query_one(
                    'SELECT max(run_at) AS t, max(trade_date) AS d '
                    '  FROM ops.quality_result') or {}
                msg = html.Span(
                    f"最近一次审计 {str(last.get('t') or '—')[:19]}，"
                    f"审计到 {last.get('d') or '—'}。",
                    className='text-muted')
        # 没有在跑就把轮询停掉，别让页面空转
        return (_gap_panel(), ui.table(queries.q_quality_recent(40)),
                msg, bool(running), not running)
