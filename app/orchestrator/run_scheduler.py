# -*- coding: utf-8 -*-
"""
`ft-scheduler` —— 调度器主进程。

**只做三件事**：看时间、记状态、往 `operations.task` 插 pending 行。
不起子进程、不解析 vipdoc、不算因子 —— 那是 `runner` 和 `worker` 的事。
边界卡死是为了让"为什么没跑"这个问题只有一个地方能回答。

## 与 runner 的分工

```
本进程    每 tick 读 config.job → schedule.decide() → operations.task(pending)
worker    operations.claim_task() → runner 起子进程 → 写终态
runner    怎么跑：RUNNERS 白名单、argv、超时、收尸
```

## 停机后怎么补

不需要额外的 catch-up 机制：**每个 tick 只消费一个槽**，`next_fire_at` 一格一格
往前推。停机三天后重启，它会在三秒内把错过的槽逐个补完；比补跑窗口
（`scheduler_catchup_days`）还老的槽直接跳过并计入 `missed_count`。

## 单实例

整台机器只允许一个调度器。靠**会话级** advisory lock（`engine.session_advisory_lock`）
独占一条裸连接实现。不能用 `engine.advisory_lock()`：那个是事务级的，
本进程每个查询都是独立事务，锁会在两次 tick 之间松开，第二个实例正好挤进来
把同一个槽触发两遍。

拿不到锁时**以退出码 3 结束**，不静默退出：静默退出的表现是"调度器活着但什么都不做"，
正是 docs/09 §7.1 列为最危险的那一类。

## 上线顺序（务必按这个来）

```
python -m app.orchestrator.run_scheduler --once --explain   # 只算不写，看决策
# scheduler_enabled=true + scheduler_dry_run=true 跑 1~2 个交易日，看 /schedule
# 上每个作业的判定对不对，然后：
# scheduler_dry_run=false                                        ← 真入队
```

`--once` 跑一个 tick 就退出，给 cron 之外的手动验证用；不带就是常驻循环。
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import os
import signal
import sys
import time

from app import config
from app.db import engine
from app.db.repo import ops as ops_repo
from app.db.repo import schedule as sch_repo
from app.orchestrator import schedule as sched
from app.orchestrator import tradeday

LOCK_KEY = 'ft:scheduler'

_stop = False


def _log(msg: str) -> None:
    """带北京时间前缀打到 stdout。日志的重定向由 scripts/web_ctl.py 负责，
    和 web/worker 一个口径（本项目不设 logging 模块，见 docs/09 §11）。"""
    print(f'[{tradeday.now_bj():%Y-%m-%d %H:%M:%S}] {msg}', flush=True)


def _install_signals() -> None:
    def _handler(signum, _frame):
        global _stop
        _stop = True
        _log(f'收到信号 {signum}，本 tick 结束后退出')

    for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        s = getattr(signal, name, None)
        if s is not None:
            # 非主线程装不上信号（ValueError）；某些 Windows 上没有 SIGBREAK
            with contextlib.suppress(ValueError, OSError):
                signal.signal(s, _handler)


def _quality_blocked(target: dt.date | None, settings: dict) -> tuple[bool, str]:
    """
    质量门禁。返回 `(拦不拦, 为什么)`。

    `has_blocking_error` 这个函数早就在 `repo/operations.py:375`，但一直零调用 ——
    它的 docstring 写着"块4 的核心权力：有阻断级错误就不写 data_ready，下游
    加工不触发"，那是承诺不是现状。这里就是把它接上的地方。

    override 那条必须有：一条误报的 blocking 规则能让整条流水线停摆，
    如果没有页面上一键豁免的口子，人只能去改代码或删规则 —— 那比没有门禁更糟。
    """
    if target is None:
        return False, '无目标交易日，不判质量'
    override = settings.get('quality_gate_override_date')
    if override and str(override) == target.isoformat():
        return False, f'{target} 被 quality_gate_override_date 豁免'
    try:
        blocked = bool(ops_repo.has_blocking_error(target))
    except Exception as exc:  # noqa: BLE001
        # 质量表读不出来时**放行**。宁可多跑一次加工，也不要因为审计本身坏了
        # 就把整条数据链停在那儿，还显示成"调度器正常"。
        return False, f'质量门禁查询失败，按放行处理：{exc}'
    return blocked, ('有阻断级规则未通过' if blocked else '质量检查通过')


def build_env(settings: dict, now: dt.datetime) -> tuple[sched.Env, str]:
    target, how = tradeday.target_date(now)
    ready = sch_repo.ready_datasets(target) if target else frozenset()
    done = sch_repo.succeeded_jobs(target) if target else frozenset()
    blocked, why_q = _quality_blocked(target, settings)
    env = sched.Env(
        now=now,
        scheduler_on=bool(settings.get('scheduler_enabled')),
        dry_run=bool(settings.get('scheduler_dry_run')),
        target_date=target,
        ready_datasets=ready,
        deps_succeeded=done,
        self_succeeded=done,
        quality_blocked=blocked,
        trade_day_of=sch_repo.is_trade_day,
        catchup_days=int(settings.get('scheduler_catchup_days') or 5),
    )
    return env, f'{how}；{why_q}'


def tick(*, explain: bool = False, quiet: bool = False) -> dict:
    """
    跑一个 tick。返回统计，`--once --explain` 时打给人看。

    `explain=True` 什么都不写：不入队、不写 schedule_state、不写日志。
    这是为了能在一个正在跑的库上安全验证判据。
    """
    settings = sch_repo.settings()
    now = tradeday.now_bj()
    env, note = build_env(settings, now)
    jobs = sched.job_sort(sch_repo.schedulable_jobs())
    stat = {'jobs': len(jobs), 'fire': 0, 'dry': 0, 'skip': 0, 'wait': 0,
            'target': str(env.target_date), 'note': note,
            'enabled': bool(settings['scheduler_enabled']),
            'dry_run': bool(settings['scheduler_dry_run'])}

    for job in jobs:
        try:
            st = sch_repo.state_of(job['job_key'])
            dec = sched.decide(job, st, env)
        except Exception as exc:  # noqa: BLE001
            # 单个作业的异常不能带走整个 tick：那会让后面所有作业这个 tick 都不判，
            # 表现成"作业偶发不跑"，是最难复现的一类问题。
            _log(f'!! {job["job_key"]} 判定异常：{type(exc).__name__}: {exc}')
            stat['skip'] += 1
            if not explain:
                # 连日志都写不进去时也不能往上抛：那会打断整个 tick，
                # 后面所有作业这一轮都不判。
                with contextlib.suppress(Exception):
                    sch_repo.log(job['job_key'], action='error',
                                 reason=f'{type(exc).__name__}: {exc}')
            continue

        stat[dec.action] = stat.get(dec.action, 0) + 1
        cron_expr = job.get('cron_expr')

        if explain:
            stat.setdefault('rows', []).append({
                '作业': f"{job['job_key']}  {job.get('name') or ''}".strip(),
                '结论': dec.action,
                '槽': dec.slot or '—',
                '原因': dec.reason or '（无新信息）',
                '下次触发': f'{dec.next_fire_at:%m-%d %H:%M}' if dec.next_fire_at
                            else '（不变）',
            })
            continue

        if dec.action == 'fire':
            if env.dry_run:
                # dry-run 不消费槽：真放开的那天还要能重判一遍。
                if not sch_repo.already_logged(job['job_key'], dec.slot, 'dry',
                                               dec.reason):
                    sch_repo.log(job['job_key'], action='dry', reason=dec.reason,
                                 slot=dec.slot, dry_run=True)
                    sch_repo.save_state(job['job_key'],
                                        dec=dataclasses.replace(dec, slot_consumed=False),
                                        cron_expr=cron_expr, result='dry')
                continue
            try:
                task_id = sch_repo.enqueue(job, env.target_date or now.date(),
                                           slot=dec.slot, actor='scheduler')
            except Exception as exc:  # noqa: BLE001
                _log(f'!! {job["job_key"]} 入队失败：{type(exc).__name__}: {exc}')
                sch_repo.log(job['job_key'], action='error',
                             reason=f'入队失败：{exc}', slot=dec.slot)
                continue
            sch_repo.save_state(job['job_key'], dec=dec, cron_expr=cron_expr,
                                result='fire')
            sch_repo.log(job['job_key'], action='fire', reason=dec.reason,
                         slot=dec.slot, task_id=task_id)
            if not quiet:
                _log(f'入队 {job["job_key"]} → task {task_id}（{dec.reason}）')
        elif dec.action == 'skip':
            sch_repo.save_state(job['job_key'], dec=dec, cron_expr=cron_expr,
                                result='skip')
            if not sch_repo.already_logged(job['job_key'], dec.slot, 'skip',
                                           dec.reason):
                sch_repo.log(job['job_key'], action='skip', reason=dec.reason,
                             slot=dec.slot)
        else:                                   # wait / idle：只推进 next_fire_at
            sch_repo.save_state(job['job_key'], dec=dec, cron_expr=cron_expr,
                                result=dec.action)

    if not explain:
        sch_repo.heartbeat('scheduler', meta={
            'tick_at': now.isoformat(), 'target': str(env.target_date),
            'note': note, 'fired': stat['fire'], 'jobs': stat['jobs'],
            'enabled': stat['enabled'], 'dry_run': stat['dry_run'],
            'pid': os.getpid()})
    else:
        # 命令行 --explain 直接打表；页面上的那颗按钮走 stat['rows']。
        print(f'目标交易日 {stat["target"]}（{note}）；'
              f'开关 enabled={stat["enabled"]} dry_run={stat["dry_run"]}')
        for r in stat.get('rows', []):
            print(f'{r["作业"]:<26} {r["结论"]:<5} {r["槽"]:<18} {r["原因"]}')
    return stat


def capture_explain() -> list[dict]:
    """
    给 /schedule 的「核对全部判定」按钮用：跑一遍判定、返回行，**不写任何东西**。

    和 `tick(explain=True)` 是同一份代码路径 —— 这一点比"页面上另写一套只读判定"
    重要得多：两套判据迟早会漂，而这一颗按钮存在的全部意义就是让人核对
    **真那个调度器**会做什么。
    """
    stat = tick(explain=True)
    return stat.get('rows', [])


def _clock_drift_check() -> None:
    """
    本机时钟与 PG 时钟偏差超过 60 秒就告警（docs/09 §7.7）。

    调度判定全按本机墙钟走，时钟飘了会把 15:10 的槽在 14:40 或 15:40 触发。
    只告警不替换成 PG 的时间：每 tick 多一次往返不值得，而且真正飘起来时
    人需要看到的是"现在几点了"这句话本身有问题。
    """
    try:
        pg_now = engine.scalar('SELECT now()')
    except Exception as exc:  # noqa: BLE001
        _log(f'!! 读不到 PG 时间，跳过时钟偏差检查：{exc}')
        return
    if pg_now is None:
        return
    local = dt.datetime.now(pg_now.tzinfo)
    drift = abs((local - pg_now).total_seconds())
    if drift > 60:
        _log(f'!! 本机时钟与数据库相差 {drift:.0f} 秒，触发时刻会判错，'
             f'请先同步 Windows 时间')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='ft-scheduler', description='FinanceTool 调度器')
    ap.add_argument('--once', action='store_true', help='只跑一个 tick 就退出')
    ap.add_argument('--explain', action='store_true',
                    help='只打印判定结果，不写库、不入队')
    ap.add_argument('--tick', type=int, default=None,
                    help='覆盖 tick 秒数（默认读 config.setting 的 scheduler_tick_sec）')
    ap.add_argument('--verbose', action='store_true', help='每个 tick 都打统计')
    args = ap.parse_args(argv)

    config.ensure_dirs()
    engine.set_app_name('ft-scheduler')

    if not engine.healthcheck()['ok']:
        _log('连不上 PostgreSQL，退出。检查服务 postgresql-x64-17 是否在跑')
        return 1

    with engine.session_advisory_lock(LOCK_KEY) as got:
        if not got:
            _log('已经有另一个调度器实例在跑（会话级 advisory lock 被占）。'
                 '两个调度器会把同一个触发槽各触发一遍 —— 拒绝启动')
            return 3
        _clock_drift_check()
        _log(f'调度器启动。单实例锁已持有，tick='
             f'{args.tick or sch_repo.settings()["scheduler_tick_sec"]}s')
        loop = 0
        while not _stop:
            t0 = time.monotonic()
            try:
                stat = tick(explain=args.explain, quiet=not args.verbose)
            except Exception as exc:  # noqa: BLE001
                # tick 里的意外不能让进程消失：进程没了就是 §7.1 那种
                # "看起来在跑其实没跑"，比判错一次严重得多。
                _log(f'!! tick 异常：{type(exc).__name__}: {exc}')
                stat = {}
            if args.verbose and not args.explain:
                _log(f'tick#{loop} 目标={stat.get("target")} 作业={stat.get("jobs")} '
                     f'入队={stat.get("fire")} 跳过={stat.get("skip")} '
                     f'开关={stat.get("enabled")}/dry={stat.get("dry_run")}')
            loop += 1
            if args.once:
                return 0
            # tick 秒数每轮重读：页面上改了立刻生效，不用重启进程。
            interval = int(args.tick or sch_repo.settings().get(
                'scheduler_tick_sec') or 15)
            left = max(interval - (time.monotonic() - t0), 1.0)
            # 分片睡：整体 sleep(left) 的话 SIGTERM 之后最多要等一个 tick 才退，
            # 关服务时会显得"卡住了"。
            end = time.monotonic() + left
            while not _stop and time.monotonic() < end:
                time.sleep(min(1.0, max(end - time.monotonic(), 0)))
        _log('调度器退出')
        return 0


if __name__ == '__main__':
    sys.exit(main())
