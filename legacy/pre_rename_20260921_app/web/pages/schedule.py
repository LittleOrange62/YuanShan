# -*- coding: utf-8 -*-
"""
调度器（运维分组，路由 `/schedule`）。

`/jobs` 讲的是"跑过什么"（`ops.task` 执行历史），这一页讲的是
"什么时候该跑、为什么没跑"（`ops.schedule_state` + `ops.schedule_log`）。
分两页而不是并成一页，是因为要回答的问题完全不同：一边是任务列表与取消，
一边是触发配置与决策流水。

## 页面上最要紧的一块是顶部那条存活状态

调度器最危险的失效形态不是判错一次，是**进程没了而页面一切正常**：cron 那一栏
照旧显示 `10 15,16,17 * * 1-5`，看起来"每天早上都在自动跑"，其实数据早就停了。
`app/orchestrator/runner.py` 开头注释点过这件事，`docs/09 §7.1` 把它列为 P0 的
上线前置条件。所以：

* 心跳超过 `sch_repo.HEARTBEAT_STALE_SEC` 没更新 → 整条横幅变红，字写成
  「调度器已停止，数据不会自动更新」。
* 判活失败的红字不能乱跳 —— 那种假警报出现几次人就开始忽略横幅了，
  比没有横幅更糟。所以阈值取 3 个 tick 以上并留了余量。

## 「为什么没跑」是直接落在表里的，不用点进去查

`note` 列显示 `ops.schedule_state.note`，也就是调度器自己写下的那一条判定原因
（"09-25 不是交易日"、"等前置作业 sync_daily 当日跑成功"、"质量门禁拦截…"）。
这一列存在的全部意义是让"今天没数据"能在三秒内被解释清楚。
"""
from __future__ import annotations

import datetime as dt

import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html

from app.db.repo import schedule as sch_repo
from app.orchestrator import tradeday
from app.web import ui

# 页面上能直接改的开关。只放"改了立刻生效、不需要重启进程"的那些 ——
# 真开关都在 config.setting，因此改动会自动经过 ops.trg_config_history 留痕可回滚。
TOGGLES = (
    ('scheduler_enabled', '调度器总开关'),
    ('scheduler_dry_run', '试运行（只判定不入队）'),
    ('scheduler_web_via_queue', '页面按钮走队列'),
)


def _fmt(v) -> str:
    return '—' if v is None else str(v)[:16]


def _age_text(sec: float | None) -> str:
    if sec is None:
        return '没有过心跳'
    if sec < 60:
        return f'{sec:.0f} 秒前'
    if sec < 3600:
        return f'{sec / 60:.1f} 分钟前'
    return f'{sec / 3600:.1f} 小时前'


def _status_bar(ov: dict) -> html.Div:
    """存活 + 三个开关 + 本 tick 的目标交易日与判据。"""
    live = ov['live']
    sch = live.get('scheduler')
    wk = live.get('worker')
    settings = ov['settings']
    # 页面自己现算一份目标交易日，而不是只回显心跳里的那个：心跳里的可能是
    # 十分钟之前的。两个数不一致时人需要看见的是"现在"这个。
    target, how = tradeday.target_date()

    def dot(name: str, info: dict | None, role: str):
        alive = bool(info and info.get('alive'))
        color = 'success' if alive else 'danger'
        text = (f'{name}：● {"运行中" if alive else "已停止"}'
                f'（{_age_text(info.get("age_sec") if info else None)}）')
        extra = ''
        if info and info.get('meta'):
            meta = info['meta'] or {}
            if role == 'scheduler':
                extra = f"目标 {meta.get('target')}，本 tick 入队 {meta.get('fired')}"
            else:
                extra = f"在跑 {meta.get('running')}/{meta.get('max_parallel')}"
        return html.Span([
            dbc.Badge(text, color=color, className='me-2'),
            html.Small(extra, className='text-muted me-3'),
        ])

    body = [
        dbc.Row([
            dbc.Col(dot('调度器', sch, 'scheduler'), width='auto'),
            dbc.Col(dot('worker', wk, 'worker'), width='auto'),
            dbc.Col(html.Small(
                f"总开关 {'开' if settings['scheduler_enabled'] else '关'}"
                f"　dry-run {'是' if settings['scheduler_dry_run'] else '否'}"
                f"　tick {settings['scheduler_tick_sec']}s"
                f"　并发 {settings['scheduler_max_parallel']}"
                f"　熔断阈值 {settings['scheduler_stop_retry_after']}",
                className='text-muted'), width=True,
                className='d-flex align-items-center'),
        ], className='g-2 align-items-center'),
        html.Div(f"目标交易日 {target or '—'}　{how}"
                 f"　现在 {tradeday.now_bj():%H:%M:%S}",
                 className='small text-muted mt-1'),
    ]
    if not (sch and sch.get('alive')):
        body.append(html.Div(
            '调度器已停止，数据不会自动更新。要么启动进程'
            '（scripts/web_ctl.py start-scheduler），要么把这一页当"手动模式"看。',
            className='text-danger fw-bold mt-1'))
    if settings['scheduler_enabled'] and settings['scheduler_dry_run']:
        body.append(html.Div(
            '试运行中：判定链照常走、原因照常写进「调度决策」，但不会真的入队。'
            '对着实库核对过 1~2 天之后再把上面的 dry-run 关掉。',
            className='text-warning small mt-1'))
    return ui.card('调度器状态', html.Div(body), help_key='schedule.status')


def _switch_panel(ov: dict):
    """三个开关。改的是 config.setting，所以下个 tick 就生效、且可回滚。"""
    settings = ov['settings']
    return ui.card('开关', html.Div([
        dbc.Row([
            dbc.Col(dbc.Switch(
                id=f'sch-sw-{key}', label=label,
                value=bool(settings.get(key)), className='mb-2'), width=12, lg=4)
            for key, label in TOGGLES
        ]),
        html.Div([
            html.Span('质量门禁豁免交易日：', className='me-2'),
            dbc.Input(id='sch-override', size='sm', style={'width': '12rem'},
                      placeholder='YYYY-MM-DD，留空=不豁免',
                      value=settings.get('quality_gate_override_date') or ''),
            dbc.Button('保存', id='sch-override-save', size='sm',
                       color='outline-primary', className='ms-2'),
            html.Span(id='sch-override-msg', className='small text-muted ms-2'),
        ]),
        html.Div(
            '「页面按钮走队列」关掉时，页面上的「运行」由网站进程直接起子进程 —— '
            '沿用历史行为，代价是不受 concurrency_key 约束（连点两次会同时跑两份）。'
            '打开后按钮只入队、由 worker 拉起，点下去最多等一个 tick（'
            f'{settings["scheduler_tick_sec"]} 秒）才开始跑。',
            className='small text-muted mt-2'),
    ]), help_key='schedule.switches')


def _job_table(ov: dict):
    rows = ov['jobs']
    if not rows:
        return ui.alert('config.job 是空的，先跑一次 scripts/init_db.py', 'warning')
    head = html.Thead(html.Tr([html.Th(c) for c in (
        '作业', '触发', '定时表达式', '下次触发', '上次入队', '上次成功日',
        '连续失败', '跳过槽', '为什么没跑')]))
    body = []
    for j in rows:
        manual = j['trigger_kind'] == 'manual'
        rows_fail = int(j['consecutive_fail'] or 0)
        limit = int(ov['settings']['scheduler_stop_retry_after'] or 0)
        body.append(html.Tr([
            html.Td([j['name'],
                     html.Div(html.Code(j['job_key']), className='small text-muted')]),
            html.Td(html.Code(j['trigger_kind'],
                              className='small text-muted' if manual else 'small')),
            html.Td(html.Code(j['cron_expr'] or '—', className='small')),
            # 显示算出来的 next_fire_at 而不是 cron 原文：两者不一致本身就是
            # "配置没生效"的信号（比如表达式改过但 state 里还是老值）。
            html.Td('不自动触发' if manual else _fmt(j['next_fire_at']),
                    className='small align-middle'),
            html.Td(_fmt(j['last_fire_at']), className='small align-middle'),
            html.Td(_fmt(j['last_success_date']), className='small align-middle'),
            html.Td(dbc.Badge(str(rows_fail),
                              color='danger' if limit and rows_fail >= max(limit - 1, 1)
                              else 'secondary') if rows_fail else '0',
                    className='small' if rows_fail else None),
            html.Td(str(j['missed_count'] or 0), className='small align-middle'),
            html.Td(html.Div((j['note'] or '—')[:90],
                             title=j['note'] or ''), className='small'),
        ]))
    return dbc.Table([head, html.Tbody(body)], hover=True, responsive=True,
                     size='sm', className='mb-0')


def _log_table(ov: dict):
    rows = ov['log']
    if not rows:
        return html.Div('还没有调度决策记录。调度器进程启动后每个判定会落一条。',
                        className='text-muted p-3')
    tone = {'fire': 'success', 'dry': 'info', 'skip': 'warning',
            'error': 'danger', 'fuse': 'danger'}
    head = html.Thead(html.Tr([html.Th(c) for c in (
        '时间', '作业', '槽', '结论', '原因', '任务')]))
    body = [html.Tr([
        html.Td(str(r['at'])[:19], className='small'),
        html.Td([r['name'] or r['job_key'],
                 html.Div(html.Code(r['job_key']), className='small text-muted')]),
        html.Td(html.Code(r['slot'] or '—', className='small')),
        html.Td(dbc.Badge(r['action'], color=tone.get(r['action'], 'secondary'))),
        html.Td(html.Div((r['reason'] or '')[:120], title=r['reason'] or '')),
        html.Td(html.A(f'#{r["task_id"]}', href='/jobs', className='small')
                if r['task_id'] else '—'),
    ]) for r in rows]
    return dbc.Table([head, html.Tbody(body)], hover=True, responsive=True,
                     size='sm', className='mb-0')


def panel(ov: dict):
    return html.Div([
        dcc.Interval(id='sch-poll', interval=10000),
        dbc.Row([
            dbc.Col(ui.stat('已登记作业', len(ov['jobs']),
                            hint='全部来自 config.job'), width=6, lg=3),
            dbc.Col(ui.stat('自动触发',
                            sum(1 for j in ov['jobs'] if j['trigger_kind'] != 'manual'),
                            hint='trigger_kind ≠ manual，这些才归调度器管',
                            tone='up'), width=6, lg=3),
            dbc.Col(ui.stat('启用中',
                            sum(1 for j in ov['jobs'] if j['enabled']),
                            hint='enabled=false 的不判', tone='accent'), width=6, lg=3),
            dbc.Col(ui.stat('已跳过槽',
                            sum(int(j['missed_count'] or 0) for j in ov['jobs']),
                            hint='因调度器停机错过、超出补跑窗口的触发次数',
                            tone='down'), width=6, lg=3),
        ], className='g-2'),
        _status_bar(ov),
        _switch_panel(ov),
        ui.card('作业调度表（config.job + ops.schedule_state）',
                html.Div(_job_table(ov), id='sch-job-table'),
                height=520, help_key='schedule.jobs',
                footer='「下次触发」是调度器算出来的时刻，不是 cron 表达式原文。'
                       '「为什么没跑」来自 ops.schedule_state.note。'),
        ui.card('调度决策（ops.schedule_log）',
                html.Div(_log_table(ov), id='sch-log-table'),
                height=420, help_key='schedule.log',
                footer='只记 fire / dry / skip / error / fuse。wait 每 15 秒一条，'
                       '不记 —— 记了一天几万行，把有用的淹掉。'),
        dbc.Button('核对全部判定（不写库）', id='sch-explain-all',
                   color='outline-primary', size='sm', n_clicks=0,
                   className='mb-2'),
        html.Div('现在这一刻，把判定链对每个作业会得出的结论原样算一遍给你看。'
                 '和调度器进程走的是同一份代码，所以这个结果就是它真会做的事。',
                 className='small text-muted mb-3'),
        html.Div(id='sch-explain-out'),
    ])


def render():
    return panel(sch_repo.overview())


def register_callbacks(app) -> None:
    @app.callback(
        Output('sch-job-table', 'children'),
        Output('sch-log-table', 'children'),
        Input('sch-poll', 'n_intervals'))
    def _refresh(_ticks):
        ov = sch_repo.overview()
        return _job_table(ov), _log_table(ov)

    for key, _label in TOGGLES:
        _bind_toggle(app, key)

    @app.callback(Output('sch-override-msg', 'children'),
                  Input('sch-override-save', 'n_clicks'),
                  State('sch-override', 'value'),
                  prevent_initial_call=True)
    def _save_override(_clicks, value):
        """
        质量门禁的豁免日。空 = 不豁免。

        这里必须把空串存成 JSON **null** 而不是 `""`：`run_scheduler._quality_blocked`
        判的是 `if override and ...`，存成 `""` 时它是假值所以恰好也对，
        但页面上回填时会显示成一个可编辑的空框、看不出"存了个东西"。
        存 null 才是"没有豁免日"这个事实的忠实表示。
        """
        text = (value or '').strip()
        if text:
            try:
                dt.date.fromisoformat(text)
            except ValueError:
                return html.Span('要 YYYY-MM-DD 这种格式', className='text-danger')
        sch_repo.set_setting('quality_gate_override_date', text or None)
        return html.Span(f'已保存：{text or "不豁免"}', className='text-success')

    @app.callback(Output('sch-explain-out', 'children'),
                  Input('sch-explain-all', 'n_clicks'),
                  prevent_initial_call=True)
    def _explain(_clicks):
        return explain_all()


def _bind_toggle(app, key: str) -> None:
    """
    开关逐个绑回调，写成工厂而不是在循环里直接 def。

    循环体内 def 出来的回调函数名相同、每轮重新绑定同一个 Output，Dash 会报
    "Trying to connect an output to multiple callbacks"；工厂函数每次调用产生
    独立的作用域，`key` 也就各是各的。
    """
    @app.callback(Output(f'sch-sw-{key}', 'children'),
                  Input(f'sch-sw-{key}', 'value'),
                  prevent_initial_call=True)
    def _save(value, _key=key):
        sch_repo.set_setting(_key, bool(value))
        return None      # 不写 children，让开关自己显示新值


def explain_all() -> html.Div:
    """
    把调度器对所有作业的判定现场跑一遍并显示，**一个字节都不写库**。

    这一颗按钮是"敢不敢把总开关打开"的依据：`tick(explain=True)` 里 `decide()`
    的全部输入都从库里真读，只有落库的三处（save_state / log / enqueue）跳过。
    dry-run 跑几天之外，还能在这里针对"现在这一刻"直接看结论。
    """
    from app.orchestrator import run_scheduler  # noqa: PLC0415

    try:
        lines = run_scheduler.capture_explain()
    except Exception as exc:  # noqa: BLE001
        return ui.alert(f'判定核对失败：{type(exc).__name__}: {exc}', 'danger')
    if not lines:
        return ui.alert('没有任何非 manual 的作业可判。'
                        '要么 config.job 全是 manual，要么还没登记。', 'warning')
    return ui.card('判定核对（不写库）', ui.table(lines), help_key='schedule.explain')
