# -*- coding: utf-8 -*-
"""
执行器实现。

一次执行的生命周期：

    operations.task 插一行 pending
      → 起子进程，state=running
      → 进程退出，按 returncode 落 succeeded / failed
      → 无论成败都写 finished_at 和 message

`operations.task` 上有唯一约束 `ux_task_job_cap_date_attempt (job_key, capability_key,
trade_date, attempt)`，所以同一作业同一天重跑必须递增 attempt ——
这里查当天最大 attempt 再 +1，而不是永远写 0（写 0 第二次就撞主键，
表现成"点了没反应"，最难查的那种）。

**这里只有"执行"，没有"调度"。** `config.job.cron_expr` 目前没有任何进程在读：
没有常驻 scheduler，也没有注册 Windows 计划任务（2026-08-29 用
`Get-ScheduledTask` 核过，一条都没有）。所以每一次执行都是有人在
`/datasource`、`/jobs` 上点，或者在命令行手动敲出来的。页面上必须把这件事
写明白，不然 cron 那一列看起来就像"已经在自动跑了"。

**2026-09-21 更新：上面那段已经不成立了。** 调度器落在
`app/orchestrator/run_scheduler.py`（决定该不该跑、往 `operations.task` 插 pending），
消费队列的是 `run_worker.py`，判据本身在 `schedule.py` 的纯函数里，
设计文档 `docs/09-调度器功能说明.md`。

`run_job()` 保留原样，是"页面上点一下立刻要看到结果"那条手动路径；
它和 worker 共用同一套 `_argv` / `_close_task` 口径，只是不经过队列 ——
所以它**不受 `concurrency_key` 约束**。要不要让手工按钮也走队列，
是 `config.setting.scheduler_web_via_queue` 一个开关的事（默认关，
保持历史行为）。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import psutil

from app.db import engine
from app.db.repo import security as sec_repo

ROOT = Path(__file__).resolve().parent.parent.parent

# 子进程超时的兜底值。config.job.timeout_sec 优先。
DEFAULT_TIMEOUT = 3600

# 白名单：作业能跑的**只有**这几个入口。
#   module — 用 `python -m <module>` 起的模块名。**这里必须是模块名而不是脚本路径**：
#            本项目没有 `scripts/ingest|ops|compute/` 那套目录（那是参考仓库的布局），
#            以前写 `ROOT / 'scripts/...'` 的结果是 `/jobs` 上每个"立即触发"
#            都在启动瞬间 FileNotFoundError —— 33 条全部如此，2026-09-25 逐个核过。
#   date   — 这个入口怎么接交易日：'iso' = --date YYYY-MM-DD，
#            'compact' = --date YYYYMMDD，None = **不传 --date**（脚本自己定）
#   what   — 这一个执行器实际落哪些表，页面上直接显示给人看
#
# `date` 这一列是必须的，不是装饰：c4/c5/c6 用 `strptime(%Y%m%d)` 解析，
# 而 c1/c3/c7/c8/c9/c11 连 `--date` 都没有。以前这里统一塞 ISO 的 `--date`，
# 一旦给它们绑上按钮就会 `unrecognized arguments` 或者 ValueError 直接失败。
# `fill_all` 的 `date` 也故意是 None：它的目标交易日必须由**脚本自己按点击时刻**
# 算（同步日历 → 15:30 收盘线 → 日历里最后一个开市日）。这里要是把
# `sec_repo.latest_trade_date()` 当 `--date` 传下去，目标就永远等于"库里已经有
# 的那天"，追平这件事直接变成空操作。
# 新增一个可跑的作业 = 在这里加一行 + config.job.params 里写 {"runner":"名字"}。
RUNNERS: dict[str, dict] = {
    'fill_all':           {'module': 'ingest.jobs.e1_fill_all', 'date': None,
                           'what': '一键追平：通达信盘后下载→日历→日线/指数/板块→'
                                   '份额/两融/龙虎榜/资金流→分红/股本→因子→评分'},
    # 收盘即时链。15:59 那条常驻宿主起的就是它；登记在这里是为了走队列时也能执行。
    # `date=None` 是刻意的：它自己判时机与目标日（`step_timing`），调度器硬塞一个
    # trade_date 会让它跳过①的守卫，那是两件不同的事，别在这里替它决定。
    'daily_close':        {'module': 'ingest.jobs.daily_close_sync', 'date': None,
                           'what': '判时机→查库→vipdoc/盘后下载→c1/c3/c16 入库→'
                                   '板块趋势档位→回验（含 GUI 那一下，需要可交互桌面）'},
    # 通达信 GUI 盘后下载。**它不落任何一张表**，只把 vipdoc 文件推到目标交易日
    # ——"数据同步之前的前置动作"。需要一个能操作鼠标的交互式桌面（会话自检不过
    # 会直接退出），所以别指望它在无人登录的计划任务里跑。
    'tdx_predownload':    {'module': 'ingest.jobs.p0_tdx_predownload',
                           'date': 'iso',
                           'what': 'vipdoc 文件（日线 .day + 1/5 分钟线 .lc1/.lc5）'
                                   '—— 不写库，是日线入库的前置'},
    'ingest_day':         {'module': 'ingest.jobs.c1_day', 'date': None,
                           'what': 'market_data.stock_daily（通达信 vipdoc 全量重扫）'},
    'ingest_tq_day':      {'module': 'ingest.jobs.c12_tq_day', 'date': 'iso',
                           'what': 'market_data.stock_daily / market_data.index_daily / market_data.sector_daily（TQ 接口）'},
    'ingest_catchup':     {'module': 'ingest.jobs.c10_catchup', 'date': 'iso',
                           'what': '按源优先级补齐当日日线与日历（默认不走网络兜底）'},
    'ingest_minute':      {'module': 'ingest.jobs.c16_minute', 'date': 'iso',
                           'what': 'market_data.stock_minute（通达信 vipdoc 的 .lc1/.lc5，'
                                   '默认只摄取目标交易日的 1/5 分钟线）'},
    # 通达信分类增强：板块全集走文件解析，成分/除权/档案走 TQ（需客户端已登录）。
    'ingest_tdx_sector':   {'module': 'ingest.jobs.c17_tdx_sector', 'date': None,
                            'what': 'reference_data.sector_info（通达信板块全集+分类，读 tdxzs*.cfg 文件）'},
    'ingest_tdx_membership': {'module': 'ingest.jobs.c18_tdx_membership', 'date': None,
                              'what': 'reference_data.sector_member（通达信板块成分，走 TQ，需客户端登录）'},
    'ingest_tdx_dividend': {'module': 'ingest.jobs.c19_tdx_dividend', 'date': None,
                            'what': 'reference_data.dividend（通达信除权除息，走 TQ，需客户端登录）'},
    'ingest_tdx_profile':  {'module': 'ingest.jobs.c20_tdx_profile', 'date': None,
                            'what': 'reference_data.stock_profile（通达信个股档案，走 TQ，需客户端登录）'},
    'ingest_sector':      {'module': 'ingest.jobs.c3_sector', 'date': None,
                           'what': 'market_data.sector_daily + 板块家数（vipdoc）'},
    'ingest_sector_name': {'module': 'ingest.jobs.c7_sector_name', 'date': None,
                           'what': 'reference_data.sector 的中文名'},
    # 同花顺本地板块。**只有它能补上"行业板块一只成分股都没有"这个洞** ——
    # 通达信的板块成分股是私有二进制，解不出来。
    'ingest_ths_block':   {'module': 'ingest.jobs.c13_ths_block', 'date': None,
                           'what': 'reference_data.sector_info + reference_data.sector_member'
                                   '（同花顺本地概念/行业/地域/特色指数）'},
    # 同花顺本地补充参考数据：港股席位 + 用户自定义指标档案（2026-09-22 摸底新增）。
    'ingest_ths_reference': {'module': 'ingest.jobs.c20_ths_reference', 'date': None,
                             'what': 'reference_data.ths_hk_broker + config.ths_user_formula'
                                     '（同花顺本地席位表与 .hxf 指标档案）'},
    # 自选分组镜像。全量覆盖删组建 —— 定时化是用户 2026-09-22 的明确要求，
    # 兜底是 import_watchlist() 里那份"覆盖前强制备份"，报告带备份路径。
    'ingest_ths_watchlist': {'module': 'ingest.jobs.c14_ths_watchlist', 'date': None,
                             'what': 'config.watchlist_group + config.watchlist_stock'
                                     '（同花顺自选全量镜像，覆盖前自动备份 JSON）'},
    'ingest_etf_share':   {'module': 'ingest.jobs.c4_etf_share', 'date': 'compact',
                           'what': 'market_data.etf_share（份额变动 → 净申赎）'},
    'ingest_margin':      {'module': 'ingest.jobs.c5_margin', 'date': 'compact',
                           'what': 'market_data.margin_detail（两融逐股明细）'},
    'ingest_lhb':         {'module': 'ingest.jobs.c6_lhb', 'date': 'compact',
                           'what': 'market_data.dragon_tiger_detail + market_data.dragon_tiger_institution（龙虎榜）'},
    'ingest_fundflow':    {'module': 'ingest.jobs.c11_fundflow', 'date': None,
                           'what': 'market_data.market_fund_flow + market_data.fund_flow_rank'
                                   '（东财资金流，只取最新交易日）'},
    'ingest_dividend':    {'module': 'ingest.jobs.c8_dividend', 'date': None,
                           'what': 'reference_data.dividend + 回算 adjustment_factor 复权因子'},
    'ingest_share':       {'module': 'ingest.jobs.c9_share', 'date': None,
                           'what': 'reference_data.share_capital（股本，用于市值）'},
    # 通达信收盘快照库（插件每天落的 SQLite）。date=None 是有意的：脚本自己
    # 按数据回推交易日（表名是采集日期，盘中采的表会被跳过），传日期反而会
    # 把"哪天有效"这个判断交给调用方拍脑袋。
    'ingest_snapshot':    {'module': 'ingest.jobs.c15_snapshot', 'date': None,
                           'what': 'market_data.stock_snapshot（市值/PE/PB/内外盘/涨跌停价，'
                                   '来自通达信插件 daily_close_data.db）'},

    'compute_indicators': {'module': 'ingest.jobs.d1_indicators', 'date': 'iso',
                           'what': 'analytics.indicator_value（基础因子）'},
    'compute_scores':     {'module': 'ingest.jobs.d2_scores', 'date': 'iso',
                           'what': 'analytics.score / analytics.score_detail（评分）'},
    'compute_sector_trend': {'module': 'ingest.jobs.d3_sector_trend',
                             'date': 'iso',
                             'what': 'analytics.sector_heat_daily + analytics.sector_trend'
                                     '（板块热度的日趋势与 3/5/7/9/11/15/21 日趋势）'},
    # 启动点。**必须排在 compute_sector_trend 之后**：它读热度表，热度没写今天，
    # 这一步只会把昨天的再算一遍。和评分无关（不读 analytics.score），所以和
    # compute_scores 没有先后关系。
    'compute_ignition':   {'module': 'ingest.jobs.d4_ignition',
                           'date': 'iso',
                           'what': 'analytics.ignition（板块趋势启动点：蓄势 / 点火 / '
                                   '延续 / 退潮四档）'},
    # 个股趋势分。**必须排在 compute_scores 之后**：它读 analytics.score 的三个
    # capability_key。漏跑只是让 /scores 那张卡回退成现算（慢 0.7s），不会变成空表。
    'compute_score_trend': {'module': 'ingest.jobs.d5_score_trend',
                            'date': 'iso',
                            'what': 'analytics.score_trend（个股操作分的 10 日趋势：'
                                    '斜率 / 拟合度 / X+Y 最高值 / 迷你走势）'},
    # 全市场情绪读数。**只读 market_data.stock_daily**，不读评分也不读板块，所以只要日线
    # 入库完就能跑，不必排在 compute_scores 后面。它是「明日验证表」的事实来源，
    # 漏跑会让次日的核验直接变成"数据不足"（不是判错，是没法判）。
    'compute_breadth':    {'module': 'ingest.jobs.d6_breadth',
                           'date': 'iso',
                           'what': 'analytics.market_breadth（全市场情绪：涨跌停 / '
                                   '触板炸板 / 连板梯队 / 晋级率 / 中位涨幅）'},
    # 倍量震荡。同样只读 market_data.stock_daily（MA60 在 SQL 里自算），日线一入库就能跑。
    # 一天约 10 秒 —— 是所有计算作业里最慢的一步，回填要按天回显。
    'compute_vol_double': {'module': 'ingest.jobs.d7_vol_double',
                           'date': 'iso',
                           'what': 'analytics.volume_double（倍量震荡：近 40 日倍量次数 / '
                                   '穿越次数 / 贴 60 线程度）'},



    # ---- qd 轨（quantdash 口径双轨，规格见 D:\finance\数据加工工作指南\09）----
    # 三个作业算的是**另一套口径**，不是 analytics.* 的下游：情绪周期读天梯落库的
    # 连板分布（不另推一遍），所以 calc_qd_sentiment 必须排在 calc_qd_ladder 之后。
    # 阈值全部硬依赖 config.setting 的 qd_* 三键，读不到直接抛错、不退回代码默认值。
    'compute_qd_ladder':    {'module': 'ingest.jobs.qd2_ladder', 'date': 'iso',
                             'what': 'qd.board_ladder / board_ladder_member / '
                                     'board_ladder_leader / board_height / '
                                     'stock_strength（双边严格涨停判据 + 连板天梯）'},
    'compute_qd_sentiment': {'module': 'ingest.jobs.qd1_sentiment',
                             'date': 'iso',
                             'what': 'qd.sentiment_cycle（三套 stage 并存：ui/mcp/'
                                     'feishu。依赖 qd2_ladder 已覆盖 T-1 与 T+1）'},
    'compute_qd_sector':    {'module': 'ingest.jobs.qd3_sector', 'date': 'iso',
                             'what': 'qd.sector_rotation / sector_persistence / sector_follow'
                                     '（板块轮动矩阵 + 领涨持续性 + 前三甲轨迹与相似榜，只取 TDX_ 体系）'},

    # 质量审计。`date` 是 None **不是漏了**：它要审的是"最近一个已收盘交易日
    # 往前所有还没审过的天"，缺 2 天就自己跑 2 次。这里要是把
    # `sec_repo.latest_trade_date()` 当 --date 传下去，就永远只审一天，
    # 08-27 那种落在中间的缺口会被永久跳过。
    'quality_audit':      {'module': 'ingest.jobs.e2_quality_audit',
                           'date': None,
                           'what': 'operations.quality_result（config.quality_rule 登记的那批'
                                   '规则，按交易日逐天审计，缺几天补几天）'},
}


# job_key → runner 名字的默认映射。`config.job.params.runner` 可以覆盖它。
# 采集类作业的绑定已经全部写在种子的 `config.job.params` 里（配置驱动），
# 这里只留计算类的历史默认值。
# 没有映射的作业**照实说没有执行器**，不假装能跑 —— 假装能跑的结果是
# 点一下变成 succeeded 而什么都没做，比明写"没实现"危险得多。
DEFAULT_RUNNER: dict[str, str] = {
    'sync_daily':            'ingest_day',
    'sync_minute':           'ingest_minute',
    'calc_base':             'compute_indicators',
    'calc_scores':           'compute_scores',
    'calc_scores_fallback':  'compute_scores',
    'build_review':          'compute_scores',
}

# 只允许这些参数名进命令行，且值必须是标量。防止从表里注入 `--exec` 之类。
# `check / no-gui / force` 是 daily_close 那条链自己的开关：不加进白名单，
# 配在 `config.job.params` 里会被 `_argv()` 静默丢掉（不报错），页面点了就变成
# "以为走了 --no-gui，其实去抢了桌面"。
ALLOWED_ARGS = {'date', 'recent', 'cap', 'ind', 'start', 'end', 'lag-days',
                'universe', 'overwrite', 'back', 'allow-web', 'skip',
                'p0-instant', 'yes', 'check', 'no-gui', 'force'}


def jobs() -> list[dict]:
    rows = engine.query(
        """
        SELECT job_key, name, kind, caps, trigger_kind, cron_expr, ready_on,
               after_job, scope, params, only_trade_day, max_retry,
               timeout_sec, concurrency_key, enabled
          FROM config.job ORDER BY job_key
        """)
    for r in rows:
        r['runner'] = job_runner(r)
    return rows


def job_runner(job: dict) -> str | None:
    """这个作业用哪个执行器。表里配的优先，没配用默认映射，都没有就是 None。"""
    params = job.get('params') or {}
    name = params.get('runner') or DEFAULT_RUNNER.get(job['job_key'])
    return name if name in RUNNERS else None


def runner_what(name: str | None) -> str:
    """这个执行器实际落哪些表。页面上直接显示，免得靠猜。"""
    return (RUNNERS.get(name or '') or {}).get('what', '')


def runner_script(name: str | None) -> str:
    """页面上显示的"这个作业怎么跑"。返回可直接复制执行的 `-m` 形式。"""
    mod = (RUNNERS.get(name or '') or {}).get('module', '')
    return f'python -m {mod}' if mod else ''


def latest_by_job() -> dict[str, dict]:
    """每个作业最后一次执行。给"上次什么时候采过、成没成"用。"""
    rows = engine.query(
        """
        SELECT DISTINCT ON (job_key)
               job_key, task_id, trade_date, state, message, actor,
               started_at, finished_at
          FROM operations.task
         WHERE job_key IS NOT NULL
         ORDER BY job_key, task_id DESC
        """)
    return {r['job_key']: r for r in rows}



def tasks(limit: int = 50, job_key: str | None = None) -> list[dict]:
    return engine.query(
        """
        SELECT t.task_id, t.job_key, j.name, t.capability_key, t.trade_date, t.state,
               t.progress, t.attempt, t.actor, t.created_at, t.started_at,
               t.finished_at, t.message, left(coalesce(t.error, ''), 300) AS error
          FROM operations.task t
          LEFT JOIN config.job j ON j.job_key = t.job_key
         WHERE (%s::text IS NULL OR t.job_key = %s::text)
         ORDER BY t.task_id DESC LIMIT %s
        """, (job_key, job_key, limit))


def running_tasks() -> list[dict]:
    return engine.query(
        """
        SELECT task_id, job_key, trade_date, started_at, progress
          FROM operations.task WHERE state = 'running' ORDER BY task_id
        """)


def _argv(job: dict, runner: str, trade_date: dt.date,
          extra: dict | None = None) -> list[str]:
    spec = RUNNERS[runner]
    # `-m` 而不是脚本路径：这些入口用相对导入（`from app.compute import ...`），
    # 直接跑文件会 ModuleNotFoundError；`-m` 也让 cwd 不再决定成败。
    argv = [sys.executable, '-m', spec['module']]
    params = dict((job.get('params') or {}).get('args') or {})
    params.update(extra or {})

    # 日期按脚本自己的口径给：没有 --date 的脚本一个都不给（给了就是启动即失败）
    mode = spec.get('date')
    if mode == 'iso':
        params.setdefault('date', trade_date.isoformat())
    elif mode == 'compact':
        params.setdefault('date', trade_date.strftime('%Y%m%d'))
    else:
        params.pop('date', None)

    for key, val in params.items():
        if key not in ALLOWED_ARGS:
            continue
        if isinstance(val, (list, tuple)):
            for v in val:
                argv += [f'--{key}', str(v)]
        elif isinstance(val, bool):
            if val:
                argv.append(f'--{key}')
        elif val is not None:
            argv += [f'--{key}', str(val)]

    # 评分类作业把 config.job.caps 逐个传成 --cap，让"这个作业算哪些能力"
    # 保持由表决定。
    if runner == 'compute_scores' and job.get('caps'):
        for cap in job['caps']:
            argv += ['--cap', cap]
    return argv



def _next_attempt(job_key: str, trade_date: dt.date) -> int:
    n = engine.scalar(
        """
        SELECT max(attempt) FROM operations.task
         WHERE job_key = %s AND trade_date = %s
        """, (job_key, trade_date))
    return 0 if n is None else int(n) + 1


def _open_task(job: dict, trade_date: dt.date, actor: str) -> int:
    # result_meta 里记发起进程的 pid：`wait=False` 的任务是由这个进程的后台线程
    # 收尾的，进程一旦被重启/结束，那一行就永远停在 running。记下 pid 之后
    # reap_orphans() 能把它收掉，页面不会一直显示"正在跑"。
    return engine.scalar(
        """
        INSERT INTO operations.task (job_key, capability_key, trade_date, scope, params,
                              state, attempt, max_retry, concurrency_key, actor,
                              result_meta)
        VALUES (%s, NULL, %s, %s, %s, 'pending', %s, %s, %s, %s, %s)
        RETURNING task_id
        """,
        (job['job_key'], trade_date,
         json.dumps(job.get('scope') or {}, ensure_ascii=False),
         json.dumps(job.get('params') or {}, ensure_ascii=False),
         _next_attempt(job['job_key'], trade_date),
         job.get('max_retry') or 2, job.get('concurrency_key'), actor,
         json.dumps({'owner_pid': os.getpid()})),
        role='app')


def reap_orphans() -> int:
    """
    把"发起进程已经不在了"的 pending/running 任务收成 failed，返回收了几条。

    页面每次刷新前调一下。不做这件事的后果实测过（2026-08-29）：命令行里用
    `wait=False` 提交完进程就退了，那条任务永远停在 running，页面显示
    「正在跑 sync_etf_share」并且轮询永不停止。
    """
    rows = engine.query(
        """
        SELECT task_id, (result_meta->>'owner_pid')::int AS pid
          FROM operations.task WHERE state IN ('pending', 'running')
        """)
    n = 0
    for r in rows:
        pid = r['pid']
        if pid and psutil.pid_exists(pid):
            continue
        _close_task(r['task_id'], 'failed',
                    message='发起进程已不在（网站重启或被结束），状态被回收',
                    error='orphan: owner process gone')
        n += 1
    return n



def _close_task(task_id: int, state: str, *, message: str = '',
                error: str | None = None, progress: int = 100) -> None:
    if state == 'succeeded':
        _supersede_previous_success(task_id)
    engine.execute(
        """
        UPDATE operations.task
           SET state = %s, message = left(%s, 2000), error = left(%s, 4000),
               progress = %s, finished_at = now()
         WHERE task_id = %s
        """, (state, message, error, progress, task_id), role='app')


def _supersede_previous_success(task_id: int) -> None:
    """把同 `job_key + trade_date` 上**旧的那条 succeeded** 让位给这一次。

    `operations.task` 上有部分唯一索引 `ux_task_succeeded`（一个作业一个交易日
    只留一条 succeeded），本来是给调度器判"这天已经跑过了"用的。但它有个副作用：
    **补数据之后重跑同一天的作业，收尾的 UPDATE 会直接 UniqueViolation**，
    整条链路抛异常回到页面（2026-09-25 实测：第二次点 `calc_vol_double` 就崩）。

    只在**这一次真的成功了**之后才让位 —— 重跑失败时必须让原来那条成功记录留着，
    否则一次失败的尝试会把好数据的历史抹掉。旧行改 `cancelled` 并留下被谁取代的
    说明，时间线和统计都还在，不是删除。
    """
    mine = engine.query_one(
        'SELECT job_key, trade_date FROM operations.task WHERE task_id = %s',
        (task_id,), role='app')
    if not mine or not mine.get('job_key'):
        return
    engine.execute(
        """
        UPDATE operations.task
           SET state = 'cancelled',
               message = left(coalesce(message, '') ||
                              '｜已被 task #' || %s || ' 取代（同一交易日重跑）', 2000)
         WHERE job_key = %s AND trade_date = %s AND state = 'succeeded' AND task_id <> %s
        """, (task_id, mine['job_key'], mine['trade_date'], task_id), role='app')


def run_job(job_key: str, *, trade_date: dt.date | str | None = None,
            actor: str = 'web', extra: dict | None = None,
            wait: bool = True) -> dict:
    """
    跑一个作业。返回 `{task_id, state, message}`。

    `wait=True`（默认）同步等子进程结束 —— 页面上点一下就要看到结果，
    异步的话还得再做一套轮询。真正长的作业（分钟线同步）用 `wait=False`，
    页面刷新时从 `operations.task` 读进度。
    """
    rows = engine.query('SELECT * FROM config.job WHERE job_key = %s', (job_key,))
    if not rows:
        return {'task_id': None, 'state': 'failed',
                'message': f'config.job 里没有 {job_key}'}
    job = rows[0]
    if not job.get('enabled'):
        return {'task_id': None, 'state': 'skipped',
                'message': f'{job_key} 当前是停用状态，先在积木管理里打开'}

    runner = job_runner(job)
    if runner is None:
        return {'task_id': None, 'state': 'skipped',
                'message': (f'{job_key} 还没有执行器。可用执行器：'
                            + '、'.join(sorted(RUNNERS))
                            + '；在 config.job.params 里写 {"runner":"名字"} 绑定。')}

    d = (dt.date.fromisoformat(trade_date) if isinstance(trade_date, str)
         else trade_date) or sec_repo.latest_trade_date()
    if d is None:
        return {'task_id': None, 'state': 'failed',
                'message': '库里还没有任何交易日，先同步日线'}

    task_id = _open_task(job, d, actor)
    argv = _argv(job, runner, d, extra)
    timeout = job.get('timeout_sec') or DEFAULT_TIMEOUT

    engine.execute(
        "UPDATE operations.task SET state='running', started_at=now(), "
        "message=%s WHERE task_id=%s",
        (' '.join(argv[1:])[:2000], task_id), role='app')

    def _work():
        try:
            # env 的口径见 _child_env()：必须是当前环境的副本，且要强制 utf-8 输出。
            proc = subprocess.run(  # noqa: S603
                argv, cwd=str(ROOT), capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=timeout,
                env=_child_env())
        except subprocess.TimeoutExpired:
            _close_task(task_id, 'failed',
                        message=f'超时（{timeout}s）被中止', error='timeout')
            return
        except Exception as exc:  # noqa: BLE001
            _close_task(task_id, 'failed', message='起子进程失败',
                        error=str(exc))
            return
        tail = (proc.stdout or '')[-1500:] + (proc.stderr or '')[-1500:]
        if proc.returncode == 0:
            _close_task(task_id, 'succeeded', message=_last_line(tail))
        else:
            _close_task(task_id, 'failed',
                        message=f'退出码 {proc.returncode}', error=tail)

    if wait:
        _work()
    else:
        threading.Thread(target=_work, daemon=True).start()

    row = engine.query(
        'SELECT state, message FROM operations.task WHERE task_id = %s', (task_id,))
    return {'task_id': task_id, 'state': row[0]['state'] if row else 'running',
            'message': row[0]['message'] if row else ''}


def _child_env() -> dict:
    """
    子进程环境：**当前环境的副本**加两个变量，不是新造一个。

    `.env` 里的 DSN 和密钥要靠继承才在（显式重建 env 会连不上库，
    报错还长得像"密码错误"）。PYTHONIOENCODING/PYTHONUTF8：子进程默认按 cp936
    输出中文，父进程按 utf-8 读，结果 operations.task.message 里全是 \\ufffd
    （2026-08-29 实测），页面上就是一行乱码，等于没有执行摘要。
    """
    return {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}


def run_claimed_task(task: dict, *, timeout_sec: int,
                     on_poll: Callable[[], bool] | None = None,
                     poll_sec: int = 5, grace_sec: int = 10) -> dict:
    """
    跑一条**已经被 `operations.claim_task` 抢到手**的 `operations.task`。worker 专用。

    和 `run_job` 的区别不是复制了一份，是三件事：

    1. **`Popen` + 读线程**，不是 `subprocess.run`。旧系统
       `task_http_api.py:434` 用 `for line in proc.stdout:` 阻塞读，
       永远走不到 `proc.wait(timeout=...)`，泄漏两次整个任务 API 就死锁。
       这里把 stdout/stderr 合并后交给一个后台线程收，主循环只管轮询。
    2. **轮询里同时做三件检查**：租约还在不在（被别人 requeue 走了就得停，
       否则同一份数据两个进程写）、`cancel_requested` 有没有置、超时到没到。
    3. **取消是真的**：`cancel()` 只置标记，因为 web 那条路径硬杀子进程会留下
       半截事务；而这里的子进程归 worker 所有，可以先 terminate、给 grace、
       再 kill，终态写 `cancelled`。`docs/04` 承诺的"真取消"落在这段。

    `on_poll` 返回 False 表示"这个任务已经不属于你了"（租约丢了）。
    """
    from app.db.repo import ops as ops_repo  # noqa: PLC0415

    task_id = task['task_id']
    job_key = task['job_key']
    rows = engine.query('SELECT * FROM config.job WHERE job_key = %s', (job_key,))
    if not rows:
        ops_repo.finish_task(task_id, 'failed',
                             message=f'config.job 里没有 {job_key}')
        return {'state': 'failed', 'message': f'config.job 里没有 {job_key}'}
    job = rows[0]
    runner = job_runner(job)
    if runner is None:
        msg = f'{job_key} 没有执行器，去 app/orchestrator/runner.py 的 RUNNERS 里登记'
        ops_repo.finish_task(task_id, 'failed', message=msg)
        return {'state': 'failed', 'message': msg}
    # 注意这里**不**因为 `config.job.enabled=false` 就中止：人关作业是为了
    # 停"以后"，队列里这条是关掉之前正当提交的，扔掉反而更难解释。

    argv = _argv(job, runner, task['trade_date'])
    tail: deque[str] = deque(maxlen=400)

    try:
        proc = subprocess.Popen(  # noqa: S603
            argv, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', env=_child_env())
    except Exception as exc:  # noqa: BLE001
        msg = f'起子进程失败：{type(exc).__name__}: {exc}'
        ops_repo.finish_task(task_id, 'failed', message=msg, error=str(exc))
        return {'state': 'failed', 'message': msg}

    def _drain() -> None:
        # 单独线程读干 stdout。PIPE 不读满会把子进程堵死在 write() 上 ——
        # 采集脚本动辄打几万行进度，这是最容易踩的坑。
        try:
            for line in proc.stdout:  # noqa: SIM110
                tail.append(line)
        except Exception:  # noqa: BLE001, S110, TRY301
            pass
        finally:
            with contextlib.suppress(Exception):
                proc.stdout.close()

    threading.Thread(target=_drain, daemon=True).start()

    engine.execute(
        "UPDATE operations.task SET message = %s WHERE task_id = %s AND state = 'running'",
        (' '.join(argv[1:])[:2000], task_id), role='app')

    deadline = time.monotonic() + max(int(timeout_sec or DEFAULT_TIMEOUT), 60)

    def _stop(force_after: float) -> None:
        """terminate → 等 grace → kill。直接 kill 会让脚本没机会收尾。"""
        with contextlib.suppress(Exception):
            proc.terminate()
        end = time.monotonic() + force_after
        while proc.poll() is None and time.monotonic() < end:
            time.sleep(0.5)
        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=30)

    while proc.poll() is None:
        time.sleep(min(poll_sec, 5))
        if on_poll is not None and not on_poll():
            # 租约没了 = 这行任务已经不属于本进程（被 requeue 走、或 worker 正在退出）。
            # 这里**只停子进程，不写任何终态**：那一行此刻可能已经是 pending 并被
            # 别的 worker 抢走，我们写 failed 会把人家正在跑的任务标成失败。
            _stop(grace_sec)
            return {'state': 'abandoned', 'message': 'lost lease'}
        if ops_repo.cancel_requested(task_id):
            _stop(grace_sec)
            ops_repo.finish_task(task_id, 'cancelled',
                                 message='人工取消：已终止子进程')
            return {'state': 'cancelled', 'message': '人工取消'}
        if time.monotonic() > deadline:
            _stop(grace_sec)
            text = ''.join(tail)
            ops_repo.finish_task(task_id, 'failed',
                                 message=f'超时（{timeout_sec}s）被中止',
                                 error='timeout | ' + _last_line(text))
            return {'state': 'failed', 'message': 'timeout'}

    text = ''.join(tail)
    if proc.returncode == 0:
        ops_repo.finish_task(task_id, 'succeeded', message=_last_line(text))
        return {'state': 'succeeded', 'message': _last_line(text)}
    ops_repo.finish_task(task_id, 'failed',
                         message=f'退出码 {proc.returncode}', error=text[-4000:])
    return {'state': 'failed', 'message': f'退出码 {proc.returncode}'}


def _last_line(text: str) -> str:
    """
    取输出的最后一行有内容的文字当摘要 —— 脚本的结论都在末尾。

    跳过 tqdm 进度条：它们也是"最后一行"，但 `100%|███| 1/1 [00:00<00:00]`
    当执行摘要毫无信息量，真正的结论在它上面那一行。
    """
    for line in reversed((text or '').splitlines()):
        s = line.strip()
        if not s:
            continue
        if '█' in s or 'it/s]' in s or '%|' in s:
            continue
        return s[:400]
    return '完成（无输出）'



def cancel(task_id: int) -> bool:
    """
    请求取消。只置标记，不杀进程 —— 子进程正在写库，硬杀会留下半截事务。
    脚本自己在阶段边界检查这个标记比较安全，目前脚本还没接，
    所以这里如实只把 pending 的任务标掉。
    """
    n = engine.execute(
        """
        UPDATE operations.task SET cancel_requested = true,
               state = CASE WHEN state = 'pending' THEN 'cancelled' ELSE state END,
               finished_at = CASE WHEN state = 'pending' THEN now() ELSE finished_at END
         WHERE task_id = %s
        """, (task_id,), role='app')
    return bool(n)
