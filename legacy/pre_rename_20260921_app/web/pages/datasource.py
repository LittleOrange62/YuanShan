# -*- coding: utf-8 -*-
"""
数据源监控 + 手动采集 + 数据源管理。

两个标签，回答的是两类问题：

**① 监控 —— "现在能不能用"**
    现在哪个源不能用？   health 灯
    是"坏了"还是"没权限"？ 五种状态分开，灰≠红
    还剩多少配额？        quota 余量条
    什么时候开始坏的？    ops.datasource_probe 的历史
    现在要重新采一遍怎么办？「手动采集」区，一个按钮一个执行器

**② 数据源管理 —— "该先用谁"**
    层级阶梯（P0 通达信本地 → 主力层 → 基座层 → 辅助层）+ 一张可编辑表。
    层级 / 职责 / 费用 / 定位 / 顺序 / 启用 / 配额 / 备注 全部就地改，
    改完直接落 `config.datasource`，不用改代码也不用连库。

**这两件事刻意不混在一列里**：health 是探测出来的**状态**，layer 是人定的
**口径**。混在一起的话，ts_* 一次 40203、em_push2 一次断连，就会把"本该是
主力源"的东西降级成"辅助源"，下次没人知道原本该先用谁。所以 ts_* 现在
health=no_permission，层级照旧是辅助层（它本来就只做交叉验证）。

**灰不是红**：Tushare 那 6 个 40203 接口在这里显示为灰（no_permission）。
标成红会让人一直去查网络，而实际该做的是买权限或换源 —— 这个区分是这一页
存在的主要理由。

**这台机器上没有任何调度进程。** `config.job.cron_expr` 没人读，也没注册 Windows
计划任务。所以「手动采集」不是备用手段，它就是主路径 —— 页面上如实写出来，
免得看到 cron 就以为数据已经自己在更新了。采集一律异步提交（`wait=False`），
提交完立刻返回，下面表格靠 `dcc.Interval` 每 3 秒自己刷新：c10/c12 这种要跑
几分钟的脚本如果同步等，页面就是卡死的。
"""
from __future__ import annotations

import datetime as dt

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, callback_context, dcc, html, no_update

from app.datasource import probe as ds_probe
from app.datasource import registry as ds_reg
from app.datasource import ths_local
from app.db import engine
from app.db.repo import security as sec_repo
from app.orchestrator import runner
from app.web import ui

# 五种健康状态 → 显示颜色。no_permission 与 broken 是"已知缺口"，用灰/黄，
# 不用红：红色留给"本该通但现在不通"的真故障。
TONE = {
    'ok': ('success', '正常'),
    'degraded': ('warning', '不稳'),
    'no_permission': ('secondary', '无权限'),
    'broken': ('dark', '接口有 bug'),
    'down': ('danger', '不可用'),
    'unknown': ('light', '未探测'),
}



def _gap_note() -> str:
    """
    「已知缺口」那块的悬浮说明：**逐条列出缺的是什么**。

    这块原来只有一个数字和一句"无权限或接口自身 bug"。数字不带清单等于没说 ——
    看到的人要么去查网络（白查，这两种状态重试无用），要么直接无视它。
    所以这里当场从库里现算一份清单挂上去，鼠标移到「说明」上就能看全。
    """
    rows = ds_reg.gaps()
    if not rows:
        return ('**当前没有已知缺口**：启用的源要么正常，'
                '要么是可以靠重试解决的故障。')
    head = ('**已知缺口 = 重试无用的那一类**：无权限（付费档位不够）'
            '或接口自身有 bug。红色留给"本该通而现在不通"的真故障。\n\n')
    body = []
    for ds in rows:
        line = (f"- **{ds['name']}**（`{ds['ds_key']}` · "
                f"{ds_reg.LAYER_LABEL.get(ds['layer'], ds['layer'])}）"
                f"：{ds['reason']} → {ds['fix']}")
        if ds.get('note'):
            line += f"\n    - 影响与替代：{ds['note']}"
        if ds.get('probe_note'):
            line += f"\n    - ⚠️ {ds['probe_note']}"
        body.append(line)
    return head + '\n'.join(body) + (
        '\n\n下面「已知缺口清单」那张卡是同一份数据的表格版。')


def _gap_rows() -> list[dict]:
    out = []
    for ds in ds_reg.gaps():
        _, label = TONE.get(ds['health'], TONE['unknown'])
        out.append({
            '类型': label,
            '数据源': ds['name'],
            'key': ds['ds_key'],
            '层级': ds_reg.LAYER_LABEL.get(ds['layer'], ds['layer']),
            '为什么算缺口': ds['reason'],
            '怎么消除': ds['fix'],
            '影响与替代路径': ds.get('note') or '—',
            '上次成功': (ds['last_ok_at'].strftime('%Y-%m-%d %H:%M')
                         if ds.get('last_ok_at') else '从未成功过'),
            '当前探测报错': ds.get('last_err') or '—',
        })
    return out


def _gap_card():
    """
    缺口的表格版。**和悬浮说明是同一个 `ds_reg.gaps()`**，不各查一遍 ——
    两处各写一份 SQL，迟早出现"卡上写 7 条、悬浮里列 6 条"。
    """
    rows = _gap_rows()
    if not rows:
        return ui.card('已知缺口清单',
                       ui.alert('当前没有已知缺口 —— 启用的源要么正常，'
                                '要么是可以靠重试解决的故障。', 'success'),
                       height=150, help_key='datasource.gaps')
    return ui.card(f'已知缺口清单（{len(rows)} 条 · 重试无用，要换源或买权限）',
                   ui.table(rows, page_size=10),
                   help_key='datasource.gaps',
                   footer='「上次成功」写"从未成功过"的源一次都没通过；'
                          '「影响与替代路径」读的是 config.datasource.note，'
                          '在 ② 数据源管理 里可以就地改')


def _summary():
    rows = engine.query(
        'SELECT health, count(*) n FROM config.datasource WHERE is_active GROUP BY 1')
    by = {r['health']: r['n'] for r in rows}
    total = sum(by.values())
    cards = [
        ('已登记数据源', total, '全部走 config.datasource，不写死在代码里'),
        ('正常', by.get('ok', 0), ''),
        ('不稳 / 不可用', by.get('degraded', 0) + by.get('down', 0),
         '需要重试或换源'),
        ('已知缺口', by.get('no_permission', 0) + by.get('broken', 0),
         '无权限或接口 bug · 鼠标移到「说明」看逐条'),
    ]
    tones = ['accent', 'up', 'down', 'muted']
    # 四块讲的是同一件事（数据源的健康分布），说明挂在第一块上，不抄四遍。
    # 例外是第四块「已知缺口」：它的说明是**当场从库里算出来的清单**，不是文档，
    # 所以直接传 note（help_key 为空时 ui.help_text 就用它），
    # 不写进 config.help_note —— 写进库里就成了一份会过期的抄本。
    notes = [None, None, None, _gap_note()]
    return [dbc.Col(ui.stat(a, f'{b:,}', hint=c, tone=t, note=notes[i],
                            help_key='datasource.stat' if i == 0 else None),
                    width=6, lg=3, className='mb-3')
            for i, ((a, b, c), t) in enumerate(zip(cards, tones, strict=False))]



def _quota_bar(ds: dict):
    lim, used = ds.get('quota_limit'), ds.get('quota_used')
    if not lim:
        # 没有配额概念的源不画条。画成 0% 会让人以为额度用光了
        return html.Span('—', className='text-muted')
    pct = min(100, int((used or 0) / lim * 100))
    return dbc.Progress(value=pct, label=f'{pct}%', style={'height': '14px'},
                        color='danger' if pct > 90 else
                        ('warning' if pct > 70 else 'success'))


def _rows():
    out = []
    for ds in ds_reg.list_sources():
        color, label = TONE.get(ds['health'], TONE['unknown'])
        out.append({
            '状态': label,
            '_color': color,
            '层级': ds_reg.LAYER_LABEL.get(ds['layer'], ds['layer']),
            '数据源': ds['name'],
            'key': ds['ds_key'],
            '类型': ds['kind'],
            # 限流组：同一个组共用一份 IP 配额（三个东财 key 都在 eastmoney 组）。
            # 摆在清单里是因为"为什么这个源慢"经常是我们自己在等闸门。
            '限流组': ds_reg.limit_group(ds['ds_key']),
            '提供方': ds['provider'] or '—',
            '接口': (ds['endpoint'] or '—')[:44],
            '密钥来源': ds['auth_ref'] or '无需鉴权',
            '延迟ms': ds['latency_ms'],
            '行数': ds['row_count'],
            '最近成功': str(ds['last_ok_at'] or '—')[:19],
            '最近探测': str(ds['last_check'] or '—')[:19],
            '最近错误': (ds['last_err'] or '')[:80],
        })
    return out


def _table():
    rows = _rows()
    if not rows:
        return ui.alert('config.datasource 是空的，先跑一次 init_db.py 播种。', 'warning')
    head = html.Thead(html.Tr([html.Th(c) for c in (
        '状态', '层级', '数据源', 'key', '类型', '限流组', '提供方', '接口',
        '密钥来源', '延迟ms', '行数', '最近成功', '最近错误')]))
    body = []
    for r in rows:
        body.append(html.Tr([
            html.Td(dbc.Badge(r['状态'], color=r['_color'])),
            html.Td(r['层级'], className='small text-nowrap'),
            html.Td(r['数据源']), html.Td(html.Code(r['key'])),
            html.Td(r['类型']),
            html.Td(r['限流组'], className='small text-nowrap'),
            html.Td(r['提供方']),

            html.Td(html.Code(r['接口']), className='small'),
            html.Td(r['密钥来源'], className='small text-muted'),
            html.Td('' if r['延迟ms'] is None else r['延迟ms']),
            html.Td('' if r['行数'] is None else f"{r['行数']:,}"),
            html.Td(r['最近成功'], className='small'),
            html.Td(r['最近错误'], className='small text-danger'),
        ]))
    return dbc.Table([head, html.Tbody(body)], bordered=False, hover=True,
                     responsive=True, size='sm', className='mb-0')



def _quotas():
    items = [ds for ds in ds_reg.list_sources() if ds.get('quota_limit')]
    if not items:
        return html.Div('当前没有登记了配额上限的数据源。给 config.datasource.quota_limit '
                        '填上数值后，这里会出现余量条。',
                        className='text-muted small p-2')
    return html.Div([
        html.Div([html.Div(f"{ds['name']}（{ds.get('quota_reset') or '无周期'}）",
                           className='small mb-1'),
                  _quota_bar(ds)], className='mb-2')
        for ds in items])


def _history(limit: int = 40):
    return engine.query(
        """
        SELECT checked_at AS 时间, ds_key AS 数据源, health AS 状态,
               latency_ms AS 延迟ms, row_count AS 行数, message AS 说明
          FROM ops.datasource_probe
         ORDER BY checked_at DESC LIMIT %s
        """, (limit,))


# --------------------------------------------------------------- 手动采集
STATE_TONE = {'succeeded': 'success', 'failed': 'danger', 'running': 'primary',
              'pending': 'secondary', 'cancelled': 'light', 'skipped': 'warning'}


def _collect_jobs() -> list[dict]:
    """采集类作业（config.job.kind='ingest'）。加一个采集口 = 往表里插一行。"""
    return [j for j in runner.jobs() if j['kind'] == 'ingest']


def _collect_table():
    jobs = _collect_jobs()
    if not jobs:
        return ui.alert('config.job 里没有 kind=ingest 的作业，先跑 scripts/init_db.py',
                        'warning')
    last = runner.latest_by_job()
    head = html.Thead(html.Tr([html.Th(c) for c in (
        '上次结果', '采集作业', '落到哪些表', '脚本', '上次交易日', '上次完成',
        '上次说明', '操作')]))
    body = []
    for j in jobs:
        can = bool(j['runner']) and j['enabled']
        t = last.get(j['job_key'])
        if t:
            badge = dbc.Badge(t['state'],
                              color=STATE_TONE.get(t['state'], 'secondary'))
        else:
            badge = html.Span('从没跑过', className='text-muted small')
        body.append(html.Tr([
            html.Td(badge),
            html.Td([j['name'],
                     html.Div(html.Code(j['job_key']), className='small')]),
            html.Td(runner.runner_what(j['runner']) or '—', className='small'),
            html.Td(html.Code(runner.runner_script(j['runner']) or '无执行器'),
                    className='small'),
            html.Td(str(t['trade_date']) if t else '—', className='small'),
            html.Td(str(t['finished_at'] or '')[:19] if t else '—',
                    className='small'),
            html.Td(html.Div((t['message'] or '')[:90] if t else '',
                             title=(t or {}).get('message') or ''),
                    className='small'),
            html.Td(dbc.Button('立即采集' if can else '无执行器',
                               id={'type': 'ds-collect', 'key': j['job_key']},
                               size='sm',
                               color='primary' if can else 'secondary',
                               disabled=not can, n_clicks=0)),
        ]))
    return dbc.Table([head, html.Tbody(body)], hover=True, responsive=True,
                     size='sm', className='mb-0')


def _submit(job_key: str, day: str | None):
    """提交一个作业，返回给页面显示的那一行提示。单行按钮和一键采集共用。"""
    try:
        # wait=False：子进程在后台线程里跑，页面不等它。
        res = runner.run_job(job_key, trade_date=day, actor='web', wait=False)
    except Exception as exc:  # noqa: BLE001
        return html.Span(f'提交失败：{exc}', className='text-danger')
    if res['task_id'] is None:
        return html.Span(f"{job_key} 没有提交：{res['message']}",
                         className='text-warning')
    return html.Span(f"已提交 {job_key}（task {res['task_id']}），"
                     f"命令：{res['message'] or ''}", className='text-primary')


#: 一键采集背后就是「一键追平」那条链（config.job 的 fill_all）。

#: **不另写一套顺序**：谁先谁后、哪步是骨干、失败怎么算，全在
#: scripts/ops/e1_fill_all.py 的 STAGES 里。页面上再排一遍顺序，
#: 迟早和脚本对不上，而且对不上的表现是"数据是旧的"，最难发现。
COLLECT_ALL_JOB = 'fill_all'

#: 「刷新 P0 本地」背后就是通达信盘后下载前置动作。它内部已经封装了
#: 关达信 → 重开 → 盘后同步下载 → 自动重试 2 次的完整流程，这里只提交，
#: 不另写一套杀进程/重开逻辑 —— 那样会跟 p0_tdx_predownload 各收各的，
#: 两个都要操作同一个通达信，早晚互相踩。
P0_REFRESH_JOB = 'tdx_predownload'

# P0 维度徽章配色。分三档：到位(success) / 落后(warning) / 缺失(danger)。
_P0_OK, _P0_LAG, _P0_MISS = 'success', 'warning', 'danger'


def _p0_rows() -> list[dict]:
    """
    P0 本地维度 6 项覆盖状态。每项 = 维度名 + 覆盖 + 状态色 + 说明。

    判据和 /datasource 探测、e1 验收是同一批数据（md.* 各表、同花顺本地 ini），
    但页面**当场算**而不是读 `config.datasource.health` —— health 是探测的
    "源通不通"，这里是"该维度覆盖到哪天/多少"，两件事分开看。

    任一项底层源坏了都不让整个区块崩：单个 try 只吞它自己的异常，标 danger。
    """
    target = sec_repo.latest_trade_date()
    out = []

    def add(label, value, tone, note):
        out.append({'维度': label, '覆盖': value, '_tone': tone, '说明': note})

    def scalar(q):
        try:
            return engine.scalar(q)
        except Exception:            # noqa: BLE001
            return None

    # 1/2. 日线、指数 —— 对照目标交易日看有没有跟上。
    sd, idx = scalar('SELECT max(trade_date) FROM md.stock_daily'), \
        scalar('SELECT max(trade_date) FROM md.index_daily')
    if sd and target and sd == target:
        add('通达信 vipdoc·个股日线', str(sd), _P0_OK, '已覆盖到目标交易日')
    elif sd:
        add('通达信 vipdoc·个股日线', str(sd), _P0_LAG,
            f'目标 {target}，还差 {(target - sd).days} 天')
    else:
        add('通达信 vipdoc·个股日线', '无', _P0_MISS, '第一次跑或日线没入库')
    if idx and target and idx == target:
        add('通达信 vipdoc·指数日线', str(idx), _P0_OK, '已覆盖到目标交易日')
    elif idx:
        add('通达信 vipdoc·指数日线', str(idx), _P0_LAG,
            f'目标 {target}，还差 {(target - idx).days} 天')
    else:
        add('通达信 vipdoc·指数日线', '无', _P0_MISS, '指数日线没入库')

    # 3~5. 同花顺本地三个源：板块(have ConfigVer) / 行业 / 自选(没有每日更新的预期)。
    def ths(fn, zerolabel):
        try:
            r = fn()
        except Exception:            # noqa: BLE001
            r = {}
        n = len(r.get('sectors') or r.get('groups') or [])
        members = sum(len(s.get('members') or s.get('member') or [])
                      for s in (r.get('sectors') or r.get('groups') or []))
        if not n:
            return zerolabel, '无', r or None
        return (f'{n} 个 / {members} 条', f'{n} 个板块 {members} 条成员', r)

    for label, fn, zl in (
            ('同花顺本地·板块', ths_local.read_blocks, 'BlockUpdate 里没解出板块'),
            ('同花顺本地·行业', ths_local.read_industry, 'industry.ini 缺失或解不出'),
            ('同花顺本地·自选', ths_local.read_watchlist_groups,
             '没找到带 stockblock.ini 的用户目录')):
        _, _v, r = ths(fn, zl)
        tone = _P0_MISS if r is None else _P0_OK
        add(label, _v, tone, zl if r is None else ('自选分组' if label.endswith('自选')
                                                   else '明文 ini 正常'))

    # 6. 收盘快照库 —— 表名是采集日期，直接看库里最新落到哪天。
    snap = scalar('SELECT max(trade_date) FROM md.stock_snapshot')
    if snap and target and snap == target:
        add('通达信收盘快照库', str(snap), _P0_OK, '市值/估值已覆盖目标日')
    elif snap:
        add('通达信收盘快照库', str(snap), _P0_LAG,
            f'目标 {target}，插件最后落 {snap}')
    else:
        add('通达信收盘快照库', '无', _P0_MISS, '插件没落新表（通达信没开机？）')
    return out


def _p0_table():
    rows = _p0_rows()
    head = html.Thead(html.Tr([html.Th(c) for c in ('P0 本地维度', '覆盖', '说明')]))
    body = []
    for r in rows:
        body.append(html.Tr([
            html.Td(html.Strong(r['维度'])),
            html.Td(dbc.Badge(r['覆盖'], color=r['_tone']), className='text-nowrap'),
            html.Td(r['说明'], className='small text-muted'),
        ]))
    return dbc.Table([head, html.Tbody(body)], bordered=False, hover=True,
                     responsive=True, size='sm', className='mb-0')


def _p0_panel():
    """
    P0 本地方块。放在监控与采集的最顶上：P0 是"一定先保证齐全"的那一层，
    一眼先看到它还缺什么，而不是先看网络源要花几次配额。

    「刷新 P0 本地」= 提交 tdx_predownload（关/开一次通达信触发盘后下载，
    脚本带自动重试 2 次）。点了走异步，页面不等；fct.p0-poll 每 3 秒刷状态。
    """
    return html.Div([
        dcc.Interval(id='ds-p0-poll', interval=3000, disabled=True),
        ui.alert('**P0 本地维度**：数据在本地、不出网、完全可控，最先保证齐全，'
                 '加工出的分值最可信。跑不出来 = 通达信没同步或本地盘损坏 → '
                 '点「刷新 P0 本地」自动关/开一次通达信触发盘后下载（自动重试 2 次），'
                 '仍失败请补充本地离线数据。', 'light'),
        dbc.Row([
            dbc.Col(dbc.Button(
                '刷新 P0 本地（关/开一次通达信触发盘后下载，自动重试 2 次）',
                id='ds-p0-refresh', color='warning', size='sm', n_clicks=0),
                width='auto'),
            dbc.Col(html.Div(id='ds-p0-msg', className='small'),
                    className='d-flex align-items-center'),
        ], className='g-2 mb-2'),
        ui.card('P0 本地维度（6 项覆盖状态）',
                html.Div(_p0_table(), id='ds-p0-table'), height=340,
                help_key='datasource.p0',
                footer='刷新动作统一走 scripts/ops/p0_tdx_predownload.py（会操作通达信）'),
    ])


def _collect_panel():
    d = sec_repo.latest_trade_date()
    runner.reap_orphans()   # 上次网站被重启时留下的 running 僵尸行先收掉
    chain = next((j for j in runner.jobs()
                  if j['job_key'] == COLLECT_ALL_JOB), None)
    chain_ok = bool(chain and chain['enabled'] and runner.job_runner(chain))

    return html.Div([
        dcc.Interval(id='ds-poll', interval=3000, disabled=True),
        ui.alert('这台机器上**没有调度进程**：config.job 里的 cron 只是计划，没人读它，'
                 '也没注册 Windows 计划任务。所以要更新数据，就在这里点「立即采集」，'
                 '或者直接点「一键采集」把整条链按顺序跑完。'
                 '点了立刻返回，不卡页面；表格每 3 秒自己刷新，跑完状态会变成 '
                 'succeeded/failed。', 'light'),
        dbc.Row([
            dbc.Col(dbc.Button('一键采集（整条链按顺序跑）', id='ds-collect-all',
                               color='success', size='sm', n_clicks=0,
                               disabled=not chain_ok,
                               title='等价于命令行 python scripts\\ops\\e1_fill_all.py'),
                    width='auto'),
            dbc.Col(dbc.InputGroup([
                dbc.InputGroupText('交易日', className='py-1'),
                dbc.Input(id='ds-date', value=str(d) if d else '', size='sm',
                          placeholder='YYYY-MM-DD，留空=最新交易日'),
            ]), width=12, lg=3),
            dbc.Col(html.Div(id='ds-collect-msg', className='small'),
                    className='d-flex align-items-center'),
        ], className='g-2 mb-2'),
        ui.alert('「一键采集」的目标交易日**由脚本自己算**（同步日历 → 15:30 收盘线 → '
                 '日历里最后一个已收盘交易日），上面那个交易日输入框只对单行的'
                 '「立即采集」生效。顺序、依赖、哪步失败算追平失败，全在 '
                 'scripts/ops/e1_fill_all.py 的 STAGES 里。', 'light'),
        ui.card('网络维度采集（要靠天吃饭 · config.job 里 kind=ingest 的作业）',
                dcc.Loading(html.Div(_collect_table(), id='ds-collect-table'),
                            type='default'),
                height=460, help_key='datasource.collect',
                footer='脚本白名单在 app/orchestrator/runner.py 的 RUNNERS；'
                       '绑定写在 config.job.params.runner。命令行等价物：'
                       'python 上面那一列的脚本路径。这一块靠网络/外部源，'
                       '经常抖，缺了看上面的 P0 已经到哪。'),
    ])




# ----------------------------------------------------------- ② 数据源管理
# 表格里的中文列名 → config.datasource 的列名。写库的白名单在
# `ds_reg.EDITABLE`，这里只负责"页面上叫什么"到"库里叫什么"的翻译。
MGR_COLS = {
    '层级': 'layer', '职责': 'ds_role', '费用': 'cost', '定位': 'tagline',
    '顺序': 'seq', '启用': 'is_active', '配额上限': 'quota_limit',
    '配额周期': 'quota_reset', '备注': 'note',
}
LAYER_VALUES = [ds_reg.LAYER_LABEL[k] for k in ds_reg.LAYERS]
LAYER_BY_LABEL = {v: k for k, v in ds_reg.LAYER_LABEL.items()}
YES_NO = ['是', '否']

# 层级阶梯的一句话说明。摆在表格上面是因为这一层信息**不该靠读表推**：
# "先查本地、再走主力、对不上才拿基座和辅助去校"是取数的默认顺序。
LAYER_WHY = {
    'p0': '先查这里。本地文件读得到就不出网 —— 最快、无配额、不受接口变更影响',
    'main': '日常生产真正在跑的。本地缺日期时按这一层的顺序取',
    'base': '历史底座与回测口径的参照。主力层对不上时用它当第三方裁判',
    'assist': '交叉验证与随手查。缺了不影响跑批，别把它当主力用',
}


def _layer_ladder():
    """层级阶梯：每层一行，列出这层有哪些源、各自担什么活。"""
    by: dict[str, list[dict]] = {k: [] for k in ds_reg.LAYERS}
    for ds in ds_reg.list_sources(active_only=False):
        by.setdefault(ds['layer'], []).append(ds)
    color = {'p0': 'primary', 'main': 'success', 'base': 'info',
             'assist': 'secondary'}
    out = []
    for i, key in enumerate(ds_reg.LAYERS):
        items = by.get(key) or []
        chips = []
        for ds in items:
            tip = ' · '.join(x for x in (ds['ds_role'], ds['cost'],
                                         ds['tagline']) if x)
            chips.append(html.Span(
                [html.Strong(ds['name']),
                 html.Span(f'（{tip}）' if tip else '', className='text-muted')],
                title=f"{ds['ds_key']}：{tip}",
                className='small me-3 text-nowrap',
                style={'opacity': '1' if ds['is_active'] else '.45'}))
        out.append(html.Div([
            html.Div([dbc.Badge(f'{i}', color=color[key], className='me-2'),
                      html.Strong(ds_reg.LAYER_LABEL[key]),
                      html.Span(f'　{LAYER_WHY[key]}',
                                className='small text-muted')],
                     className='mb-1'),
            html.Div(chips or html.Span('（这一层现在是空的）',
                                        className='small text-muted'),
                     className='ps-4 mb-2'),
        ]))
    return html.Div(out)


def _mgr_rows() -> list[dict]:
    """
    管理表的行。**停用的源也要列出来**（`active_only=False`）——
    这张表的用途之一就是把某个源停掉/放回来，只显示启用的就再也开不回去了。
    """
    out = []
    for ds in ds_reg.list_sources(active_only=False):
        out.append({
            '层级': ds_reg.LAYER_LABEL.get(ds['layer'], ds['layer']),
            '数据源': ds['name'],
            'key': ds['ds_key'],
            '职责': ds['ds_role'] or '',
            '费用': ds['cost'] or '',
            '定位': ds['tagline'] or '',
            '顺序': ds['seq'],
            '启用': '是' if ds['is_active'] else '否',
            '类型': ds['kind'],
            '提供方': ds['provider'] or '',
            '配额上限': ds['quota_limit'],
            '配额周期': ds['quota_reset'] or '',
            '密钥来源': ds['auth_ref'] or '无需鉴权',
            '备注': ds['note'] or '',
        })
    return out


def _mgr_table():
    return ui.table(_mgr_rows(), id_='ds-mgr', page_size=30,
                    link_codes=False,
                    editable_cols=tuple(MGR_COLS),
                    dropdowns={'层级': LAYER_VALUES, '启用': YES_NO})


def _manage_panel():
    return html.Div([
        ui.alert('这一页的**层级**是口径不是状态：某个源现在没权限或连不上，'
                 '它该担的角色不变。所以「无权限」的 Tushare 仍旧躺在辅助层，'
                 '而不是被降级 —— 否则一次网络抖动之后，没人知道原本该先用谁。',
                 'light'),
        ui.card('层级阶梯（取数的默认顺序）', _layer_ladder(), height=330,
                help_key='datasource.ladder',
                footer='顺序 0→3 就是取数顺序；灰掉的是已停用的源'),
        html.Div(id='ds-mgr-msg', className='small mb-1'),
        ui.card('数据源管理（双击淡黄色格子就地改，改完立刻落库）',
                _mgr_table(), height=580, help_key='datasource.manage',
                footer='可改：层级 / 职责 / 费用 / 定位 / 顺序 / 启用 / 配额 / 备注。'
                       'health 不给改（那是探测的结论），endpoint 与 probe_spec '
                       '也不给改（改了要连带改探测代码，属于改代码）。'
                       '密钥来源只显示环境变量名'),
    ])


def _monitor_panel():
    return html.Div([
        dcc.Store(id='ds-probe-result'),
        dbc.Row(_summary(), className='g-2'),
        ui.alert('「无权限」与「接口有 bug」显示为灰色而不是红色 —— 它们不是故障，'
                 '重试一万次也没用，要做的是换源或买权限。红色只留给本该通而现在'
                 '不通的真故障。', 'light'),
        _gap_card(),

        dbc.Row([
            dbc.Col(dbc.Button('立即探测全部', id='ds-probe-btn', color='primary',
                               size='sm'), width='auto'),
            dbc.Col(dbc.Checkbox(id='ds-probe-sticky', label='连已知缺口一起重探',
                                 value=False), width='auto',
                    className='d-flex align-items-center'),
            dbc.Col(html.Div(id='ds-probe-msg', className='small text-muted'),
                    className='d-flex align-items-center'),
        ], className='g-2 mb-3'),
        ui.card('数据源清单（config.datasource，按层级 → 顺序排）',
                dcc.Loading(html.Div(_table(), id='ds-table'), type='default'),
                height=520, help_key='datasource.list',
                footer='密钥列只显示环境变量名。库里不存任何密钥本身；'
                       '层级与职责在 ② 数据源管理 里改'),
        _p0_panel(),
        _collect_panel(),
        dbc.Row([
            dbc.Col(ui.card('配额余量', _quotas(), height=250,
                            help_key='datasource.quota'), width=12, lg=4,
                    className='mb-3'),
            dbc.Col(ui.card('探测历史（ops.datasource_probe）',
                            ui.table(_history()),
                            help_key='datasource.history'),
                    width=12, lg=8, className='mb-3'),
        ], className='g-3'),
    ])


def render():
    # dbc.Tabs 的 children 是静态渲染的（两个面板一次全建出来），所以
    # 两边的组件 id 都存在、回调都挂得上，不用管当前在哪个标签。
    return dbc.Tabs([
        dbc.Tab(label='① 监控与采集', tab_id='monitor',
                children=_monitor_panel()),
        dbc.Tab(label='② 数据源管理', tab_id='manage',
                children=_manage_panel()),
    ], id='ds-tabs', active_tab='monitor', className='mb-2')



def _diff_cell(prev, cur):
    """
    找出就地编辑改了哪一格。DataTable 不给"哪一格变了"，只在 data 变化时把旧值
    塞进 `data_previous`。行数不一样说明这是整表重画而不是一次编辑，当没差异 ——
    否则会把重画误判成编辑去写库。（同 watchlist.py 里的同名函数。）
    """
    if not prev or not cur or len(prev) != len(cur):
        return None
    for i, (a, b) in enumerate(zip(prev, cur, strict=True)):
        for k, v in b.items():
            if a.get(k) != v:
                return i, k, v, a.get(k)
    return None


def register_callbacks(app) -> None:
    @app.callback(Output('ds-table', 'children'),
                  Output('ds-probe-msg', 'children'),
                  Input('ds-probe-btn', 'n_clicks'),
                  State('ds-probe-sticky', 'value'),
                  prevent_initial_call=True)
    def _run_probe(_n, include_sticky):
        try:
            res = ds_probe.probe_all(skip_sticky=not include_sticky)
        except Exception as exc:  # noqa: BLE001
            return _table(), f'探测失败：{exc}'
        ok = sum(1 for r in res if r['health'] == 'ok')
        bad = [r['ds_key'] for r in res if r['health'] in ('down', 'degraded')]
        msg = f'探测完成：{len(res)} 个源，正常 {ok} 个'
        if bad:
            msg += '；需要关注 ' + '、'.join(bad[:6])
        return _table(), msg

    # ---------------------------------------------------- ② 数据源管理：写回
    # Input 用 `data_previous`、State 用 `data`、Output 才是 `data` ——
    # 把 `data` 同时挂成 Input 和 Output 会被 Dash 判成 Circular Dependencies。
    @app.callback(Output('ds-mgr', 'data'),
                  Output('ds-mgr-msg', 'children'),
                  Input('ds-mgr', 'data_previous'),
                  State('ds-mgr', 'data'),
                  prevent_initial_call=True)
    def _mgr_edit(prev, cur):
        d = _diff_cell(prev, cur)
        if d is None:
            return no_update, no_update
        i, col, new, old = d
        ds_key = (cur[i] or {}).get('key')
        column = MGR_COLS.get(col)
        if not ds_key or column is None:
            # 改到了不可写的列（理论上 DataTable 已经拦住了），把表拉回库里的样子
            return _mgr_rows(), html.Span(f'{col} 不能在这里改（已还原）',
                                          className='text-danger')
        value = LAYER_BY_LABEL.get(new, new) if column == 'layer' else new
        try:
            ds_reg.update_field(ds_key, column, value)
        except Exception as exc:  # noqa: BLE001
            # 写不进去就把表格拉回库里的真实值，否则页面上留着一个库里
            # 并不存在的值，下次刷新才发现。
            return _mgr_rows(), html.Span(f'没改成：{exc}（已还原）',
                                          className='text-danger')
        # 改成了就**不重建表格** —— 重建会把正在编辑的格子连光标一起换掉，
        # 连着改两行就没法做了。层级/顺序改了排序会变，等下次进页面再重排。
        return no_update, html.Span(
            f'{ds_key} 的{col}：{old or "空"} → {new or "空"}',
            className='text-success')

    # 按钮和轮询共用一个回调，而不是两个回调写同一个 Output ——
    # Dash 不允许重复 Output（除非 allow_duplicate），而且合成一个之后
    # "点了立刻刷新一次"和"每 3 秒刷新一次"走的是同一段代码，不会走岔。
    @app.callback(Output('ds-collect-table', 'children'),
                  Output('ds-collect-msg', 'children'),
                  Output('ds-poll', 'disabled'),
                  Input({'type': 'ds-collect', 'key': ALL}, 'n_clicks'),
                  Input('ds-collect-all', 'n_clicks'),
                  Input('ds-poll', 'n_intervals'),
                  State('ds-date', 'value'),
                  prevent_initial_call=True)
    def _collect(_clicks, _all_clicks, _ticks, date_text):
        trig = callback_context.triggered_id
        msg = None
        job_key = day = None
        if isinstance(trig, dict) and trig.get('type') == 'ds-collect':
            job_key = trig.get('key')
            day = (date_text or '').strip() or None
        elif trig == 'ds-collect-all':
            # 一键采集：目标日交给 e1_fill_all 自己算（它要先同步日历再套
            # 15:30 收盘线），所以这里**不传** day —— 传了等于用页面上的值
            # 盖掉脚本里那套判据，14:00 点一下就会去追一个还没收盘的日子。
            job_key = COLLECT_ALL_JOB
        if job_key:
            # 同一时刻只让一条采集在跑：这些脚本抢同一个通达信客户端和同一批表，
            # 并行只会互相踩（而且失败信息会串成一团看不出谁坏了）。
            busy = runner.running_tasks()
            if busy:
                msg = html.Span(
                    '还有采集在跑，等它结束再点：' + '、'.join(
                        t['job_key'] for t in busy), className='text-warning')
            else:
                msg = _submit(job_key, day)

        # 先把僵尸行收掉，再看还有什么真的在跑
        runner.reap_orphans()
        running = runner.running_tasks()
        if msg is None:
            if running:
                msg = html.Span(
                    '正在跑：' + '、'.join(
                        f"{t['job_key']}（{str(t['started_at'])[:19]} 起）"
                        for t in running), className='text-primary')
            else:
                msg = html.Span('当前没有正在执行的采集。', className='text-muted')
        # 没有在跑的任务就把轮询关掉，别让页面空转
        return _collect_table(), msg, not running

    # P0 refresh 与轮询共用一个回调，写法对齐上面的 _collect：点一下立刻刷一次，
    # 每 3 秒自己刷一次，跑完自动停。
    @app.callback(Output('ds-p0-table', 'children'),
                  Output('ds-p0-msg', 'children'),
                  Output('ds-p0-poll', 'disabled'),
                  Input('ds-p0-refresh', 'n_clicks'),
                  Input('ds-p0-poll', 'n_intervals'),
                  prevent_initial_call=True)
    def _p0_refresh(_clicks, _ticks):
        if callback_context.triggered_id == 'ds-p0-refresh':
            # 同一时刻只让一条采集在跑：Refresh 会关/开通达信，跟别的采集并行
            # 会把同一个通达信客户端抢乱。
            busy = runner.running_tasks()
            if busy:
                msg = html.Span(
                    '还有采集在跑，等它结束再点：' + '、'.join(
                        t['job_key'] for t in busy), className='text-warning')
            else:
                # 不传 trade_date：走 run_job 默认值（最新交易日）。
                try:
                    res = runner.run_job(P0_REFRESH_JOB, trade_date=None,
                                         actor='web', wait=False)
                except Exception as exc:            # noqa: BLE001
                    msg = html.Span(f'提交失败：{exc}', className='text-danger')
                else:
                    msg = (html.Span(
                        f"{P0_REFRESH_JOB} 没有提交：{res['message']}",
                        className='text-warning') if res['task_id'] is None
                        else html.Span(
                            f"已提交刷新 P0 本地（task {res['task_id']}）："
                            f"会关/开一次通达信触发盘后下载",
                            className='text-primary'))
        runner.reap_orphans()
        running = runner.running_tasks()
        # 注意：能跑 tdx_predownload 的只有它自己，这里只关心"还有没有采集在跑"，
        # 好把轮询关掉（页面别空转）。表格每 3 秒自己重算 6 项覆盖。
        if msg is None:
            msg = html.Span(
                ('正在跑：' + '、'.join(str(t['job_key']) for t in running))
                if running else '当前没有采集在跑。', className='text-primary'
                if running else 'text-muted')
        return _p0_table(), msg, not running

