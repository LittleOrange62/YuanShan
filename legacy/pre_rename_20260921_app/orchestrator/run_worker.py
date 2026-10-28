# -*- coding: utf-8 -*-
"""
`ft-worker` —— 队列消费者。

调度器只往 `ops.task` 插 pending 行，真正把子进程跑起来的是这里：

```
claim_task()  ← FOR UPDATE SKIP LOCKED，抢不到就返回 None
  ↓ 打上租约（lease_expires_at，2 分钟）
run_claimed_task()  ← 在独立线程里跑，主循环只管心跳与续租
  ↓
成功 → schedule_state.last_success_date / 失败 → consecutive_fail+1，够了就熔断
```

## 为什么不加单实例锁

和调度器不同，**允许多个 worker 同时跑**是设计的一部分：`ops.claim_task` 那条
`FOR UPDATE SKIP LOCKED` 天生就是给多消费者用的，任务级互斥由 `concurrency_key`
和租约保证。给 worker 加整机单实例锁会把这套机制白白浪费掉。

但 `scheduler_max_parallel` 默认 1，且**建议保持 1**：vipdoc 是本机单份文件，
两个采集并行不会更快，只会互相拖慢并且更难归因。

## 心跳必须独立于任务

采集作业动辄跑 4 小时（`fill_all` 的 timeout 就是 14400 秒）。如果心跳和任务
在同一个循环里，页面上会看到"worker 已停止"的红字挂整整 4 小时 —— 那种假警报
出现几次之后人就开始忽略横幅了，比没有横幅更糟。所以任务一律丢给线程，
主循环每 3 秒只做：心跳、收过期租约、补 worker 直起路径留下的僵尸行、够闲就抢任务。

## 退出

收到 SIGTERM/SIGCTRL 后：**先释放租约让子进程自己停 → 等线程收干净 → 再把自己
手上的任务退回 pending**。顺序反了就会出现"行是 pending 但子进程还在写"的窗口，
下一个 worker 抢过去就是两份并发写同一批分区。
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import os
import signal
import socket
import sys
import threading
import time

from app import config
from app.db import engine
from app.db.repo import ops as ops_repo
from app.db.repo import schedule as sch_repo
from app.orchestrator import runner, tradeday

# 主循环节拍。3 秒不是精度需求（任务粒度是分钟级），是为了让"点了等到有反应"
# 这件事在页面上感受得到。
LOOP_SEC = 3

# 心跳里带的运行中任务列表最多列几条，避免 meta jsonb 无限膨胀
_MAX_META_TASKS = 20

_stop = threading.Event()


def _log(msg: str) -> None:
    print(f'[{tradeday.now_bj():%Y-%m-%d %H:%M:%S}] {msg}', flush=True)


def _install_signals() -> None:
    def _handler(_signum, _frame):
        _stop.set()

    for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        s = getattr(signal, name, None)
        if s is not None:
            # 非主线程装不上信号；SIGBREAK 只在 Windows 有。
            # 装不上也没关系，命令行 Ctrl-C 走的是 SIGINT。
            with contextlib.suppress(ValueError, OSError, AttributeError):
                signal.signal(s, _handler)


class Pool:
    """在跑的任务集合。`max_parallel` 就是它的容量上限。"""

    def __init__(self, worker_id: str) -> None:
        self.worker_id = worker_id
        self._lock = threading.Lock()
        self._threads: dict[int, threading.Thread] = {}

    @property
    def busy(self) -> int:
        with self._lock:
            return len(self._threads)

    def snapshot(self) -> list[int]:
        with self._lock:
            return sorted(self._threads)

    def spawn(self, task: dict, timeout_sec: int) -> None:
        task_id = task['task_id']
        t = threading.Thread(target=self._run, args=(task, timeout_sec),
                             name=f'task-{task_id}', daemon=True)
        with self._lock:
            self._threads[task_id] = t
        t.start()

    def _run(self, task: dict, timeout_sec: int) -> None:
        task_id, job_key = task['task_id'], task['job_key']

        def _on_poll() -> bool:
            # 停止信号 → 主动把租约判过期，让 run_claimed_task 走"丢租约"分支
            # 自己把子进程收掉。这样停止逻辑只有一条路径，不用在这儿再 kill 一次。
            if _stop.is_set():
                sch_repo.release_lease(task_id, worker_id=self.worker_id)
                return False
            return sch_repo.renew_lease(task_id, worker_id=self.worker_id)

        try:
            res = runner.run_claimed_task(task, timeout_sec=timeout_sec,
                                          on_poll=_on_poll)
        except Exception as exc:  # noqa: BLE001
            _log(f'!! task {task_id}（{job_key}）执行异常：'
                 f'{type(exc).__name__}: {exc}')
            res = {'state': 'failed', 'message': f'{type(exc).__name__}: {exc}'}
        finally:
            with self._lock:
                self._threads.pop(task_id, None)

        if res.get('state') == 'abandoned':
            # 这行已经不是我们的了，既不算成功也不算失败。
            _log(f'task {task_id}（{job_key}）让出：租约已不在本进程手上')
            return
        self._settle(task, res)

    def _settle(self, task: dict, res: dict) -> None:
        """把终态反馈到调度器记忆里 —— 熔断和"上次成功哪天"全靠这一步。"""
        job_key, trade_date = task['job_key'], task['trade_date']
        state = res.get('state')
        if state == 'succeeded':
            sch_repo.bump_success(job_key, trade_date)
            _log(f'#{task["task_id"]} {job_key} 成功（{trade_date}）'
                 f'：{(res.get("message") or "")[:120]}')
            return
        ops_repo.mark_skipped_downstream(task['task_id'])
        if state == 'cancelled':
            _log(f'#{task["task_id"]} {job_key} 已被人工取消')
            return
        n = sch_repo.bump_failure(job_key)
        limit = int(sch_repo.settings().get('scheduler_stop_retry_after') or 0)
        _log(f'#{task["task_id"]} {job_key} 失败（连续第 {n} 次）'
             f'：{(res.get("message") or "")[:160]}')
        if limit and n >= limit:
            sch_repo.fuse(job_key)
            sch_repo.log(job_key, action='fuse',
                         reason=f'连续失败 {n} 次达阈值 {limit}，'
                                f'已自动停用该作业（可在 /blocks 恢复，'
                                f'或从 ops.config_history 回滚）')
            _log(f'!! {job_key} 连续失败 {n} 次，已熔断停用')

    def release_all(self) -> None:
        """退出前调用：让所有在跑的线程自己收掉子进程并退出。"""
        for task_id in self.snapshot():
            sch_repo.release_lease(task_id, worker_id=self.worker_id)
        end = time.monotonic() + 60
        while self.busy and time.monotonic() < end:
            time.sleep(1)
        if self.busy:
            _log(f'!! 仍有 {self.busy} 个线程没退干净，'
                 f'这些任务会被按租约超时重新入队')


def sweep(*, pool: Pool, max_parallel: int, quiet: bool) -> int:
    """
    一个循环拍。返回这一拍抢到的任务数（0 或若干）。

    顺序是有讲究的：先收尸（把不该再是 running 的行处理掉），再报活，最后才抢新的。
    反过来会出现在"僵尸行还占着 concurrency_key"的时候抢不到任何任务。
    """
    # 1. 过期租约 → pending。这是 worker 被硬杀（蓝屏、任务管理器结束进程）后
    #    唯一的自愈路径。
    n = sch_repo.requeue_expired_lease()
    if n and not quiet:
        _log(f'收回 {n} 条租约过期的任务，已退回队列')

    # 2. web 直起子进程那条路径留下的僵尸行。它自己维护 `result_meta->>'owner_pid'`，
    #    和租约是两套机制，互不干扰 —— `requeue_expired_lease` 只碰 claimed_by 非空的行，
    #    绝不会误伤正在被 web 进程跑着的任务。
    z = runner.reap_orphans()
    if z and not quiet:
        _log(f'收掉 {z} 条发起进程已不存在的僵尸任务')

    # 3. 心跳
    sch_repo.heartbeat('worker', meta={
        'pid': os.getpid(), 'worker_id': pool.worker_id,
        'running': pool.busy, 'tasks': pool.snapshot()[:_MAX_META_TASKS],
        'max_parallel': max_parallel, 'beat_at': tradeday.now_bj().isoformat()})

    # 4. 抢任务
    got = 0
    while pool.busy < max_parallel:
        task = sch_repo.claim_and_lease(worker_id=pool.worker_id)
        if not task:
            break
        timeout = int(engine.scalar(
            'SELECT timeout_sec FROM config.job WHERE job_key = %s',
            (task['job_key'],)) or runner.DEFAULT_TIMEOUT)
        pool.spawn(task, timeout)
        got += 1
        if not quiet:
            _log(f'开跑 #{task["task_id"]} {task["job_key"]} '
                 f'（{task["trade_date"]}，timeout {timeout}s）')
    return got


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='ft-worker', description='FinanceTool 任务执行进程')
    ap.add_argument('--once', action='store_true', help='只拍一拍就退出（给巡检用）')
    ap.add_argument('--max-parallel', type=int, default=None,
                    help='覆盖 config.setting 的 scheduler_max_parallel')
    ap.add_argument('--quiet', action='store_true', help='只打异常，不打常规流水')
    args = ap.parse_args(argv)

    config.ensure_dirs()
    engine.set_app_name('ft-worker')

    if not engine.healthcheck()['ok']:
        _log('连不上 PostgreSQL，退出。检查服务 postgresql-x64-17 是否在跑')
        return 1

    worker_id = f'{socket.gethostname()}:{os.getpid()}'
    pool = Pool(worker_id)
    _install_signals()
    _log(f'worker 启动，身份 {worker_id}')

    last_prune = dt.date.min
    while not _stop.is_set():
        max_par = int(args.max_parallel or
                      sch_repo.settings().get('scheduler_max_parallel') or 1)
        try:
            sweep(pool=pool, max_parallel=max_par, quiet=args.quiet)
        except Exception as exc:  # noqa: BLE001
            # 和调度器同样的理由：主循环异常不能让进程消失。
            _log(f'!! 循环拍异常：{type(exc).__name__}: {exc}')
        today = dt.date.today()
        if today != last_prune:            # 每天清一次决策日志，够用且不打扰
            last_prune = today
            with_safe_prune()
        if args.once:
            _log('--once：拍完这一拍就退出')
            break
        end = time.monotonic() + LOOP_SEC
        while not _stop.is_set() and time.monotonic() < end:
            time.sleep(0.5)

    _log('正在退出：先让在跑的任务收掉子进程…')
    pool.release_all()
    back = sch_repo.requeue_mine(worker_id)
    _log(f'退回队列 {back} 条，worker 退出')
    return 0


def with_safe_prune() -> None:
    try:
        n = sch_repo.prune_log(30)
        if n:
            print(f'[prune] 清掉 {n} 条 30 天前的调度决策日志', flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f'!! 决策日志清理失败（不影响主流程）：{exc}', flush=True)


if __name__ == '__main__':
    sys.exit(main())
