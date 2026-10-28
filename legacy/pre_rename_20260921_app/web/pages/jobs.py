# -*- coding: utf-8 -*-
"""
任务与调度。

四块：一键追平、作业清单（`config.job`）、手动触发、执行历史（`ops.task`）。

**一键追平**是这一页的主入口：目标交易日按**点下去那一刻**算（先同步日历 →
15:30 收盘线 → 日历里最后一个开市日），然后把这一天以及往前缺的各层数据按顺序
全跑一遍。判据在 `app/orchestrator/tradeday.py`，编排在
`scripts/ops/e1_fill_all.py` —— 页面这边只显示和触发，不复制判据。

**没有执行器的作业照实标出来。** 缺对应脚本或模块的作业（分钟线、质量审计、
摘要推送）按钮是灰的。把它们显示成"可运行"然后点一下变 succeeded 是最坏的
做法 —— 那会让人以为数据已经同步了。

**「调度」现在是有实体了**：`app/orchestrator/run_scheduler.py` 是常驻调度进程，
`run_worker.py` 消费队列，判据在 `schedule.py`。但**开关默认是关的**
（`config.setting.scheduler_enabled=false`），关着的时候 cron 那一栏仍然只是计划 ——
所以这一页的措辞跟着开关走，而不是一刀切成"已自动"：开关没开就照实说"未启用"。
真实的触发配置、下次触发时刻、以及"这个作业今天为什么没跑"在 **/schedule** 那一页，
不在这里 —— 本页只回答"跑过什么"。

触发一律异步（`wait=False`）：采集脚本动辄几分钟，同步等的话页面就是卡死的。
提交后 `dcc.Interval` 每 3 秒刷新执行历史，跑完了自己停。
"""
from __future__ import annotations

import datetime as dt

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, callback_context, dcc, html

from app.db.repo import schedule as sch_repo
from app.db.repo import security as sec_repo
from app.orchestrator import runner, tradeday
from app.web import ui

STATE_TONE = {'succeeded': 'success', 'failed': 'danger', 'running': 'primary',
              'pending': 'secondary', 'cancelled': 'light', 'skipped': 'warning'}

# 一键追平串起来的步骤，和 scripts/ops/e1_fill_all.py 的 STAGES 一一对应。
# 这里只是给人看的文案 —— 顺序的唯一真相在脚本里，改顺序要改那边。
FILL_STEPS = ('日历', '日线/指数/板块', 'ETF份额', '两融', '龙虎榜', '资金流',
              '分红+复权因子', '股本', '基础因子', '评分')


def _fill_state():
    """
    目标交易日 + 当前缺口。**单独拎成一小块**是为了能被轮询刷新：整块面板里
    含着 `job-fill` 按钮，把按钮所在的子树整个换掉会让 Dash 重建这个 Input，
    n_clicks 归零那一下有可能再触发一次回调 —— 那就是"点一次跑两遍"。
    """
    now = dt.datetime.now()
    target, how = tradeday.target_date(now)
    have = sec_repo.latest_trade_date()
    miss = tradeday.missing_days(target) if target else []
    if not target:
        gap = '库里还没有交易日历，先跑一次日线同步'
    elif miss:
        gap = f"缺 {len(miss)} 个交易日：" + '、'.join(
            str(d) for d in miss[-6:])
    elif have == target:
        gap = '日线已经到目标日（辅助源和评分仍会按窗口回补一遍）'
    else:
        gap = f'库里最新 {have}，目标 {target}'
    return [
        html.Div([
            html.Span('目标交易日 '),
            html.Strong(str(target or '—'), className='text-primary'),
            html.Span(f'　{how}　·　收盘线 '
                      f'{tradeday.CLOSE_HHMM[0]:02d}:'
                      f'{tradeday.CLOSE_HHMM[1]:02d}'
                      f'（{now:%H:%M} 早于它就算上一个交易日）',
                      className='text-muted'),
        ], className='mb-1'),
        html.Div(gap, className='small text-muted'),
    ]


def _fill_panel():
    """
    一键追平。**目标交易日按点下去那一刻算**，不是按库里最新那天算。

    面板上显示的是"用库里现有日历 + 现在时刻"算出来的预告；真跑时脚本会先同步
    一次日历再确认，跨年/长假后可能比这里显示的更靠后 —— 照实写出来，
    别让人把预告当最终值。
    """
    return ui.card(
        '一键追平（把数据补到最新已收盘交易日）',
        html.Div([
            html.Div(_fill_state(), id='job-fill-state', className='mb-2'),
            html.Div('按顺序跑：' + ' → '.join(FILL_STEPS),
                     className='small text-muted mb-2'),
            dbc.Row([
                dbc.Col(dbc.Button('一键追平到最新收盘日', id='job-fill',
                                   color='danger', n_clicks=0), width='auto'),
                dbc.Col(dbc.InputGroup([
                    dbc.InputGroupText('回补交易日', className='py-1'),
                    dbc.Input(id='job-fill-back', type='number', value=5,
                              size='sm'),
                ]), width=12, lg=3),
                dbc.Col(dbc.Checkbox(
                    id='job-fill-web', value=False,
                    label='允许网络兜底（新浪，口径与通达信不完全可比）'),
                    width='auto', className='d-flex align-items-center'),
            ], className='g-2'),
            html.Div('日线主源是通达信 vipdoc。vipdoc 还没覆盖目标日、又没勾兜底时，'
                     '这一步会按设计停下来并在执行历史里说明，不会悄悄换源。',
                     className='small text-muted mt-2'),
        ]),
        footer='脚本 scripts/ops/e1_fill_all.py；只想跑某一层还是用下面的作业清单',
        help_key='jobs.fill')


def _summary():
    js = runner.jobs()
    runnable = [j for j in js if j['runner']]
    ts = runner.tasks(limit=200)
    today = sec_repo.latest_trade_date()
    ok = sum(1 for t in ts if t['state'] == 'succeeded'
             and t['trade_date'] == today)
    bad = sum(1 for t in ts if t['state'] == 'failed')
    cards = [
        ('已登记作业', len(js), '全部来自 config.job'),
        ('可运行', len(runnable), f'另有 {len(js) - len(runnable)} 个缺执行器'),
        (f'{today} 成功', ok, '按最新交易日统计'),
        ('历史失败', bad, '最近 200 条里的失败数'),
    ]
    tones = ['accent', 'up', 'up', 'down']
    # 说明的 key 写死在这里，不从标签推：第三块的标签带着日期（`2026-08-28 成功`），
    # 按标签取 key 每天都会指到一条不存在的说明。
    keys = ['jobs.stat.已登记作业', 'jobs.stat.可运行',
            'jobs.stat.成功', 'jobs.stat.历史失败']
    return [dbc.Col(ui.stat(a, b, hint=c, tone=t, help_key=k), width=6, lg=3,
                    className='mb-3')
            for (a, b, c), t, k in zip(cards, tones, keys, strict=False)]


def _trigger_cell(j: dict, sched_on: bool):
    """
    触发方式怎么显示。措辞跟着 `scheduler_enabled` 走。

    开关关着的时候把 cron 显示成"已自动"是撒谎；开着的时候还写"未接调度"也是撒谎。
    两种谎都会让人按错的假设去理解页面上的数据新鲜度。
    """
    auto = html.Span('调度器已接管', className='text-success') if sched_on \
        else html.Span('计划(调度器未启用)', className='text-warning')
    if j['cron_expr']:
        return html.Div([html.Code(j['cron_expr'], className='text-muted'), auto],
                        className='small')
    trig = j['after_job'] or '、'.join(j['ready_on'] or [])
    if trig:
        return html.Div([html.Code(trig), auto], className='small')
    return html.Div('手动', className='small')



def _job_table(sched_on: bool = False):
    rows = runner.jobs()
    if not rows:
        return ui.alert('config.job 是空的，先跑一次 scripts/init_db.py', 'warning')
    head = html.Thead(html.Tr([html.Th(c) for c in (
        '', '作业', 'key', '类型', '触发', '定时/依赖', '落到哪些表', '执行器', '操作')]))
    body = []
    for j in rows:
        can = bool(j['runner']) and j['enabled']
        body.append(html.Tr([
            html.Td(dbc.Badge('启用' if j['enabled'] else '停用',
                              color='success' if j['enabled'] else 'secondary')),
            html.Td(j['name']),
            html.Td(html.Code(j['job_key'])),
            html.Td(j['kind']),
            html.Td(j['trigger_kind']),
            html.Td(_trigger_cell(j, sched_on)),
            html.Td(runner.runner_what(j['runner']) or '—', className='small'),
            html.Td(html.Code(runner.runner_script(j['runner'])) if j['runner']
                    else html.Span('无执行器', className='text-warning small'),
                    className='small'),
            html.Td(dbc.Button('运行', id={'type': 'job-run', 'key': j['job_key']},
                               size='sm', color='primary' if can else 'secondary',
                               disabled=not can, n_clicks=0)),
        ]))

    return dbc.Table([head, html.Tbody(body)], hover=True, responsive=True,
                     size='sm', className='mb-0')


def _task_table(limit: int = 40):
    rows = runner.tasks(limit=limit)
    if not rows:
        return html.Div('还没有任何执行记录。上面点一个「运行」试试。',
                        className='text-muted p-3')
    head = html.Thead(html.Tr([html.Th(c) for c in (
        'id', '状态', '作业', '交易日', '第几次', '发起人', '开始', '结束', '说明')]))
    body = []
    for t in rows:
        body.append(html.Tr([
            html.Td(t['task_id']),
            html.Td(dbc.Badge(t['state'],
                              color=STATE_TONE.get(t['state'], 'secondary'))),
            html.Td(t['name'] or t['job_key'] or '—'),
            html.Td(str(t['trade_date'])),
            html.Td(t['attempt']),
            html.Td(t['actor'], className='small text-muted'),
            html.Td(str(t['started_at'] or '')[:19], className='small'),
            html.Td(str(t['finished_at'] or '')[:19], className='small'),
            html.Td(html.Div((t['message'] or '')[:120],
                             title=t['error'] or t['message'] or ''),
                    className='small'),
        ]))
    return dbc.Table([head, html.Tbody(body)], hover=True, responsive=True,
                     size='sm', className='mb-0')


def render():
    d = sec_repo.latest_trade_date()
    runner.reap_orphans()   # 网站上次被重启时留下的 running 僵尸行先收掉
    on = bool(sch_repo.settings().get('scheduler_enabled'))
    return html.Div([
        dcc.Interval(id='job-poll', interval=3000, disabled=True),
        dbc.Row(_summary(), className='g-2'),
        _fill_panel(),
        ui.alert('触发是异步的：点一下立刻返回，子进程在后台跑，下面的执行历史每 '
                 '3 秒自己刷新，跑完自动停。「无执行器」的作业按钮是灰的 —— '
                 '缺对应脚本，不是坏了。'
                 + ('调度器总开关**已打开**，非 manual 的作业会按 cron / 就绪 / '
                    '依赖自动入队；触发配置与「今天为什么没跑」看 '
                    '/schedule。' if on else
                    '调度器总开关**当前是关的**，所以「触发」列里的 cron 只是计划：'
                    '本机虽有调度进程，它不会入队，真要更新数据得在这里或 '
                    '/datasource 点。开关在 /schedule。'),
                 'light'),
        dbc.Row([
            dbc.Col(dbc.InputGroup([
                dbc.InputGroupText('交易日', className='py-1'),
                dbc.Input(id='job-date', value=str(d) if d else '', size='sm',
                          placeholder='YYYY-MM-DD，留空=最新交易日'),
            ]), width=12, lg=3),
            dbc.Col(html.Div(id='job-msg', className='small'),
                    className='d-flex align-items-center'),
        ], className='g-2 mb-2'),
        ui.card('作业清单（config.job）',
                dcc.Loading(html.Div(_job_table(on), id='job-table'),
                            type='default'),
                height=520, help_key='jobs.list',
                footer='执行器白名单在 app/orchestrator/runner.py 的 RUNNERS 里；'
                       'config.job.params 写 {"runner":"名字"} 即可绑定'),
        ui.card('执行历史（ops.task）',
                dcc.Loading(html.Div(_task_table(), id='task-table'),
                            type='default'),
                height=460, help_key='jobs.history'),
    ])


def register_callbacks(app) -> None:
    # 按钮与轮询共用一个回调：Dash 不允许两个回调写同一个 Output，
    # 合成一个之后"点完立刻刷一次"和"每 3 秒刷一次"也就是同一段代码。
    @app.callback(Output('job-table', 'children'),
                  Output('task-table', 'children'),
                  Output('job-msg', 'children'),
                  Output('job-poll', 'disabled'),
                  Output('job-fill-state', 'children'),
                  Input({'type': 'job-run', 'key': ALL}, 'n_clicks'),
                  Input('job-poll', 'n_intervals'),
                  Input('job-fill', 'n_clicks'),
                  State('job-date', 'value'),
                  State('job-fill-back', 'value'),
                  State('job-fill-web', 'value'),
                  prevent_initial_call=True)
    def _run(_clicks, _ticks, _fill, date_text, back, allow_web):
        trig = callback_context.triggered_id
        msg = None
        if trig == 'job-fill':
            # **不传 trade_date**：目标日必须由脚本按点击时刻自己算。这里要是把
            # 页面上那个"交易日"输入框（默认＝库里最新那天）传下去，目标就永远
            # 等于已经有的那天，追平变成空操作。
            extra = {'back': int(back or 5)}
            if allow_web:
                extra['allow-web'] = True
            msg = _trigger('fill_all', None, extra)
        elif isinstance(trig, dict) and trig.get('type') == 'job-run':
            msg = _trigger(trig.get('key'), (date_text or '').strip() or None)
        # 先把"发起进程已经不在"的僵尸行收掉，再看还有什么真的在跑
        runner.reap_orphans()
        running = runner.running_tasks()
        if msg is None:
            msg = html.Span(
                ('正在跑：' + '、'.join(str(t['job_key']) for t in running))
                if running else '当前没有正在执行的任务。',
                className='text-primary' if running else 'text-muted')
        return (_job_table(bool(sch_repo.settings().get('scheduler_enabled'))),
                _task_table(), msg, not running, _fill_state())


def _trigger(job_key: str, day: str | None, extra: dict | None = None):
    """提交一个作业，返回给页面看的那行字。异步（`wait=False`），点完立刻返回。"""
    try:
        res = runner.run_job(job_key, trade_date=day, actor='web',
                             extra=extra, wait=False)
    except Exception as exc:  # noqa: BLE001
        return html.Span(f'触发失败：{exc}', className='text-danger')
    tone = 'text-warning' if res['task_id'] is None else 'text-primary'
    return html.Span(f"{job_key} → task {res['task_id']}："
                     f"{res['message'] or res['state']}", className=tone)

