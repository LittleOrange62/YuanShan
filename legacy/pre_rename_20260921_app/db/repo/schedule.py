# -*- coding: utf-8 -*-
"""
调度器的数据访问层：`ops.schedule_state` / `ops.schedule_log` /
`ops.scheduler_heartbeat`，以及"给判定链准备外部事实"的那几条查询。

**为什么不复用 `repo/ops.py` 里那几个现成函数**：它们写得对，但语义不够 ——

* `ops.requeue_stale_running()` 会把**所有** `state='running'` 的任务重新入队。
  今天页面上的「运行」是 web 进程直接起子进程跑的（`runner.run_job`），
  那些任务真的在跑。worker 一启动就调它，等于把 web 正在跑的任务又排一遍队，
  同一份数据两个进程同时写。所以这里只按 `lease_expires_at` 过期来收
  （`requeue_expired_lease`），**没打过租约的任务一律不碰**。
* `ops.create_task()` 不知道 `enqueued_by` / `fire_slot` 这两列，
  而这两列正是"这个作业怎么自己跑了"的唯一线索。所以自己写 INSERT。

其余能复用的都复用：`claim_task` / `finish_task` / `mark_skipped_downstream` /
`request_cancel` 全走 `ops.py`，不再造第二套队列。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
from typing import Any

from app.db import engine
from app.orchestrator import schedule as sched

# 心跳超过这么久没更新，页面就判定该进程已经死了。3 个 tick 的量
# （tick 默认 15 秒），但要留足够余量给"一个 tick 里有个查询慢了"的情况 ——
# 判活失败的红字横幅一天跳十次，人就学会忽略它了，那比没有横幅更糟。
HEARTBEAT_STALE_SEC = 120

# worker 租约时长。worker 每 30 秒续一次，续 2 分钟 —— 允许它连续错过一次
# 心跳（正在 flush 大表、GC 卡了一下）而不被误判死亡。
LEASE_SEC = 120

SETTING_KEYS = (
    'scheduler_enabled', 'scheduler_dry_run', 'scheduler_web_via_queue',
    'scheduler_tick_sec', 'scheduler_max_parallel', 'scheduler_catchup_days',
    'scheduler_stop_retry_after', 'quality_gate_override_date',
)

_DEFAULTS: dict[str, Any] = {
    'scheduler_enabled': False,      # **默认关**。上线后要在页面上手动打开
    'scheduler_dry_run': True,       # 默认只记判定不入队
    'scheduler_web_via_queue': False,  # 默认沿用今天的行为：web 直接起子进程
    'scheduler_tick_sec': 15,
    'scheduler_max_parallel': 1,
    'scheduler_catchup_days': 5,
    'scheduler_stop_retry_after': 3,
    'quality_gate_override_date': None,
}


# ---------------------------------------------------------------- 配置
def settings(*, role: str = 'reader') -> dict[str, Any]:
    """一次读全 8 个调度器开关。每 tick 调一次，所以必须是单条查询。"""
    rows = engine.query(
        'SELECT key, value FROM config.setting WHERE key = ANY(%s)',
        (list(SETTING_KEYS),), role=role)
    got = {r['key']: r['value'] for r in rows}
    out = dict(_DEFAULTS)
    for k, v in got.items():
        # 表里显式存 JSON null 时退回默认值。`quality_gate_override_date` 的默认
        # 本来就是 null，所以对它来说这两条路径结果一致，不会把豁免日"清空又出现"。
        out[k] = _DEFAULTS[k] if v is None else v
    return out


def set_setting(key: str, value: Any, *, role: str = 'app') -> None:
    """写开关。走 config.setting，因此 ops.trg_config_history 会自动留痕、可回滚。"""
    if key not in _DEFAULTS:
        raise ValueError(f'未知的调度器开关 {key!r}')
    from app.db.repo import registry as reg_repo  # noqa: PLC0415
    reg_repo.set_setting(key, value, category='scheduler', role=role)


# ---------------------------------------------------------------- 判定要的外部事实
def schedulable_jobs(*, role: str = 'reader') -> list[dict]:
    """
    调度器该看的作业。`trigger_kind='manual'` 排除在外 —— 那是"只有人点才跑"，
    调度器替人做这个决定不合适（`fill_all` 一次 4 小时，自动起来会把队列占满）。
    """
    return engine.query(
        """
        SELECT j.*, (s.next_fire_at IS NOT NULL) AS has_state
          FROM config.job j
          LEFT JOIN ops.schedule_state s USING (job_key)
         WHERE j.trigger_kind <> 'manual'
         ORDER BY j.job_key
        """, role=role)


def state_of(job_key: str, *, role: str = 'reader') -> sched.SlotState:
    r = engine.query_one(
        'SELECT * FROM ops.schedule_state WHERE job_key = %s', (job_key,), role=role)
    if not r:
        return sched.SlotState()
    return sched.SlotState(next_fire_at=r['next_fire_at'],
                           last_fire_slot=r['last_fire_slot'],
                           consecutive_fail=int(r['consecutive_fail'] or 0),
                           cron_used=r['cron_used'])


def states(*, role: str = 'reader') -> dict[str, dict]:
    rows = engine.query('SELECT * FROM ops.schedule_state', role=role)
    return {r['job_key']: r for r in rows}


def save_state(job_key: str, *, dec: sched.Decision, cron_expr: str | None,
               result: str, role: str = 'app',
               advance_success: dt.date | None = None) -> None:
    """
    把一个判定结果写回调度器记忆。

    三个"保留原值"是刻意的，每一条都对应一种会被自己心跳刷掉的信息：

    * `dec.next_fire_at is None` → 保留。**wait 分支不推进下次触发时刻**，
      写 NULL 会让下个 tick 重新按"现在"算，于是那个永远等不到依赖的槽直接
      消失，作业跳过了本该跑的那一天。
    * `last_fire_slot` → 只有槽被真正消费才写（见 `job_slot_for_write`）。
    * `action='idle'` → **note 与 last_result 一个字都不改**。idle 的含义是
      "这个槽已经处理完了，本 tick 没有新信息"；要是照写 note，昨天那条
      "不是交易日所以跳过"会在几十个 tick 后被覆盖成空话，
      而"为什么没跑"正是这一列存在的全部理由。
    """
    if dec.action == 'idle':
        engine.execute(
            """
            INSERT INTO ops.schedule_state (job_key, next_fire_at, cron_used, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (job_key) DO UPDATE SET
                next_fire_at = coalesce(%s, ops.schedule_state.next_fire_at),
                cron_used    = coalesce(%s, ops.schedule_state.cron_used),
                updated_at   = now()
            """,
            (job_key, dec.next_fire_at, cron_expr, dec.next_fire_at, cron_expr),
            role=role)
        return

    engine.execute(
        """
        INSERT INTO ops.schedule_state (job_key, last_fire_at, last_fire_slot,
                                        next_fire_at, last_success_date,
                                        last_result, missed_count, note, cron_used,
                                        updated_at)
        VALUES (%s,
                CASE WHEN %s IN ('fire','dry') THEN now() ELSE NULL END,
                %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (job_key) DO UPDATE SET
            last_fire_at      = CASE WHEN %s IN ('fire','dry')
                                     THEN now()
                                     ELSE ops.schedule_state.last_fire_at END,
            last_fire_slot    = coalesce(%s, ops.schedule_state.last_fire_slot),
            next_fire_at      = coalesce(%s, ops.schedule_state.next_fire_at),
            last_success_date = coalesce(%s, ops.schedule_state.last_success_date),
            last_result       = EXCLUDED.last_result,
            missed_count      = ops.schedule_state.missed_count + %s,
            note              = EXCLUDED.note,
            cron_used         = coalesce(%s, ops.schedule_state.cron_used),
            updated_at        = now()
        """,
        (job_key, result,
         job_slot_for_write(dec), dec.next_fire_at, advance_success,
         result, int(dec.missed), dec.reason[:2000], cron_expr,
         result,
         job_slot_for_write(dec), dec.next_fire_at, advance_success,
         int(dec.missed), cron_expr),
        role=role)


def job_slot_for_write(dec: sched.Decision) -> str | None:
    """只有槽被真正消费（fire / skip）时才写 last_fire_slot；wait 不写。"""
    return dec.slot if dec.slot_consumed else None


def bump_success(job_key: str, trade_date: dt.date, *, role: str = 'app') -> None:
    """任务成功回来时刷新 last_success_date 并把连续失败清零。"""
    engine.execute(
        """
        INSERT INTO ops.schedule_state (job_key, last_success_date, consecutive_fail,
                                        last_result, updated_at)
        VALUES (%s, %s, 0, 'succeeded', now())
        ON CONFLICT (job_key) DO UPDATE SET
            last_success_date = greatest(
                coalesce(ops.schedule_state.last_success_date, EXCLUDED.last_success_date),
                EXCLUDED.last_success_date),
            consecutive_fail = 0,
            last_result = 'succeeded',
            updated_at = now()
        """, (job_key, trade_date), role=role)


def bump_failure(job_key: str, *, role: str = 'app') -> int:
    """
    任务失败回来时累加连续失败数，返回累加后的值（调用方据此决定是否熔断）。

    先 UPDATE 再单独读，不用 RETURNING 嵌在 CTE 里：这条要在页面和 worker
    两边都能读懂，可读性优先。
    """
    engine.execute(
        """
        INSERT INTO ops.schedule_state (job_key, consecutive_fail, last_result, updated_at)
        VALUES (%s, 1, 'failed', now())
        ON CONFLICT (job_key) DO UPDATE SET
            consecutive_fail = ops.schedule_state.consecutive_fail + 1,
            last_result = 'failed', updated_at = now()
        """, (job_key,), role=role)
    return int(engine.scalar(
        'SELECT consecutive_fail FROM ops.schedule_state WHERE job_key = %s',
        (job_key,), role=role) or 0)


def ready_datasets(trade_date: dt.date, *, role: str = 'reader') -> frozenset[str]:
    """某交易日已登记就绪的数据集 key。`ops.data_ready` 主键就是这两个字段。"""
    rows = engine.query(
        'SELECT dataset_key FROM ops.data_ready WHERE trade_date = %s',
        (trade_date,), role=role)
    return frozenset(r['dataset_key'] for r in rows)


def succeeded_jobs(trade_date: dt.date, *, role: str = 'reader') -> frozenset[str]:
    """某交易日已经成功的 job_key 集合。chain 判定和幂等判定共用这一条查询。"""
    rows = engine.query(
        """
        SELECT DISTINCT job_key FROM ops.task
         WHERE trade_date = %s AND state = 'succeeded' AND job_key IS NOT NULL
        """, (trade_date,), role=role)
    return frozenset(r['job_key'] for r in rows if r['job_key'])


def is_trade_day(day: dt.date, *, role: str = 'reader') -> bool:
    """
    日历判定。查不到那一天时**返回 False（保守）**：宁可漏跑一次手动补，
    也不要日历没同步时节假日自动跑起来 —— 后者产出的"有数但是旧的"页面
    比"今天没自动跑"更难被发现。
    """
    v = engine.scalar(
        'SELECT is_open FROM ref.trade_calendar WHERE cal_date = %s', (day,), role=role)
    return bool(v)


# ---------------------------------------------------------------- 入队
def enqueue(job: dict, trade_date: dt.date, *, slot: str | None,
            actor: str, role: str = 'app') -> int:
    """
    排一个 pending 任务。**只入队，不起进程** —— 起进程是 worker 的事。

    `ON CONFLICT` 挂在 attempt 上：同一 (job, cap, date, attempt) 撞了就往后加，
    和 `runner._open_task` 的口径一致（那里注释写过：写死 attempt=0 的话
    第二次直接撞主键，表现成"点了没反应"，是最难查的一类问题）。
    """
    attempt = int(engine.scalar(
        'SELECT coalesce(max(attempt), -1) + 1 FROM ops.task '
        'WHERE job_key = %s AND trade_date = %s',
        (job['job_key'], trade_date), role=role) or 0)
    return int(engine.scalar(
        """
        INSERT INTO ops.task (job_key, cap_key, trade_date, scope, params,
                              state, attempt, max_retry, concurrency_key, actor,
                              enqueued_by, fire_slot, result_meta)
        VALUES (%s, NULL, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s)
        RETURNING task_id
        """,
        (job['job_key'], trade_date,
         json.dumps(job.get('scope') or {}, ensure_ascii=False),
         json.dumps({**(job.get('params') or {}), '_actor': actor},
                    ensure_ascii=False),
         attempt, job.get('max_retry') or 2, job.get('concurrency_key'),
         actor, 'scheduler' if actor == 'scheduler' else actor, slot,
         json.dumps({'scheduled_by': actor, 'slot': slot})), role=role))


# ---------------------------------------------------------------- 决策日志
def log(job_key: str, *, action: str, reason: str, slot: str | None = None,
        task_id: int | None = None, dry_run: bool = False, role: str = 'app') -> None:
    engine.execute(
        """
        INSERT INTO ops.schedule_log (job_key, slot, action, task_id, reason, dry_run)
        VALUES (%s, %s, %s, %s, %s, %s)
        """, (job_key, slot, action, task_id, reason[:2000], dry_run), role=role)


def already_logged(job_key: str, slot: str | None, action: str, reason: str,
                   *, role: str = 'reader') -> bool:
    """
    这个 (作业, 槽, 结论, 原因) 是不是已经记过了。dry-run 与 skip 共用。

    dry-run 不消费槽（否则试跑几天会把所有槽 burn 掉，真开的那天反而一个都不跑），
    于是同一个槽每 15 秒重判一次、每次都想写日志 —— 不查这一笔的话
    `ops.schedule_log` 一天几万行，全是重复。

    为什么连 `reason` 一起比：只比 (作业, 槽, 结论) 的话，同一个槽上
    "原因变了"这一类新信息会被吞掉。实测就撞上过 —— 13 个作业的 skip 在
    第一个 tick 各记一条"总开关未打开"，之后不管原因怎么变（打开开关、
    改成"不是交易日"、改成"质量门禁拦截"），页面里的决策流水就再也不动了，
    看的人以为调度器卡住了。
    """
    return bool(engine.scalar(
        """
        SELECT 1 FROM ops.schedule_log
         WHERE job_key = %s AND coalesce(slot, '') = coalesce(%s, '')
           AND action = %s AND reason = %s
         LIMIT 1
        """, (job_key, slot, action, reason[:2000]), role=role))


def recent_log(limit: int = 100, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT l.at, l.job_key, j.name, l.slot, l.action, l.reason, l.task_id,
               l.dry_run
          FROM ops.schedule_log l
          LEFT JOIN config.job j USING (job_key)
         ORDER BY l.id DESC LIMIT %s
        """, (int(limit),), role=role)


def prune_log(keep_days: int = 30, *, role: str = 'app') -> int:
    return engine.execute(
        "DELETE FROM ops.schedule_log WHERE at < now() - (%s || ' days')::interval",
        (int(keep_days),), role=role)


# ---------------------------------------------------------------- 心跳与存活
def heartbeat(role_: str, *, meta: dict | None = None, pid: int | None = None,
              db_role: str = 'app') -> None:
    """
    常驻进程报活。页面据此判"调度器还在不在" —— 见 docs/09 §7.1，
    这是自动化最危险的失效形态（进程没了、页面一切正常、cron 列照旧显示）。

    不写 config.setting：那张表挂着 config_history 触发器，每 15 秒一条会把
    真正的配置变更记录淹掉。
    """
    engine.execute(
        """
        INSERT INTO ops.scheduler_heartbeat (role, pid, hostname, heartbeat_at, meta)
        VALUES (%s, %s, %s, now(), %s)
        ON CONFLICT (role) DO UPDATE SET pid = EXCLUDED.pid,
            hostname = EXCLUDED.hostname, heartbeat_at = now(), meta = EXCLUDED.meta
        """,
        (role_, pid if pid is not None else os.getpid(), socket.gethostname(),
         json.dumps(meta or {}, ensure_ascii=False)), role=db_role)


def live_roles(*, role: str = 'reader') -> dict[str, dict]:
    rows = engine.query('SELECT * FROM ops.scheduler_heartbeat', role=role)
    now = engine.scalar('SELECT now()', role=role)
    out = {}
    for r in rows:
        hb = r['heartbeat_at']
        age = None
        if hb and now:
            age = (now - hb).total_seconds()
        out[r['role']] = {**r, 'age_sec': age,
                          'alive': age is not None and age <= HEARTBEAT_STALE_SEC}
    return out


def overview(*, role: str = 'reader') -> dict:
    """给 /schedule 页面一次性备齐数据。"""
    jobs = engine.query(
        """
        SELECT j.*, s.last_fire_at, s.last_fire_slot, s.next_fire_at,
               s.last_success_date, s.last_result, s.consecutive_fail, s.missed_count,
               s.note, s.updated_at AS state_updated_at
          FROM config.job j
          LEFT JOIN ops.schedule_state s USING (job_key)
         ORDER BY (j.trigger_kind = 'manual'), j.job_key
        """, role=role)
    return {'jobs': jobs, 'live': live_roles(role=role), 'settings': settings(role=role),
            'log': recent_log(60, role=role)}


# ---------------------------------------------------------------- worker 租约
def claim_and_lease(*, worker_id: str, role: str = 'app') -> dict | None:
    """
    抢一个任务并打上租约。复用 `ops.claim_task`（那条 `FOR UPDATE SKIP LOCKED`
    已经处理了"依赖未完成"和"同 concurrency_key 有任务在跑"两种排除），
    抢到手之后立刻续租约 —— 分两步是因为 claim 的 SQL 已经是别人写好的了，
    改它不如在它后面补一句。
    """
    from app.db.repo import ops as ops_repo  # noqa: PLC0415
    task = ops_repo.claim_task(role=role)
    if not task:
        return None
    engine.execute(
        "UPDATE ops.task SET claimed_by = %s, lease_expires_at = now() "
        "+ (%s || ' seconds')::interval WHERE task_id = %s",
        (worker_id, str(LEASE_SEC), task['task_id']), role=role)
    return task


def renew_lease(task_id: int, *, worker_id: str, role: str = 'app') -> bool:
    """续租。**带上 claimed_by 条件**：任务被 requeue 抢走后，本 worker 再续租
    会一路把它跑完并两次写终态，那正是这里要防的重复执行。"""
    n = engine.execute(
        "UPDATE ops.task SET lease_expires_at = now() + (%s || ' seconds')::interval "
        'WHERE task_id = %s AND claimed_by = %s AND state = %s',
        (str(LEASE_SEC), task_id, worker_id, 'running'), role=role)
    return bool(n)


def requeue_expired_lease(*, role: str = 'app') -> int:
    """
    把租约过期的 running 任务退回 pending。**同一条任务行被重新排队，attempt
    不递增** —— attempt 的语义是"人又点了一次"，机器重投不该混进去。

    **只收打过租约的**：`claimed_by IS NOT NULL` 这一条是刻意的，
    web 直起子进程那条路径不打租约，那些任务真的在跑，碰不得。
    """
    return engine.execute(
        """
        UPDATE ops.task SET state = 'pending', started_at = NULL, progress = 0,
               claimed_by = NULL, lease_expires_at = NULL,
               message = coalesce(message, '') || ' | 租约过期，重新入队'
         WHERE state = 'running' AND claimed_by IS NOT NULL
           AND lease_expires_at < now()
        """, role=role)


def requeue_mine(worker_id: str, *, role: str = 'app') -> int:
    """
    干净退出时把**自己**手上还在跑的任务退回 pending。

    顺序不能反：必须先把子进程收掉再调它，否则会出现"行已经是 pending、
    子进程还在往里写"的窗口，下一个 worker 抢去就是两份并发写同一批分区。
    """
    return engine.execute(
        """
        UPDATE ops.task SET state = 'pending', started_at = NULL, progress = 0,
               claimed_by = NULL, lease_expires_at = NULL,
               message = coalesce(message, '') || ' | worker 退出，退回队列'
         WHERE state = 'running' AND claimed_by = %s
        """, (worker_id,), role=role)


def release_lease(task_id: int, *, worker_id: str, role: str = 'app') -> None:
    """
    主动把这个任务的租约判过期 —— 让 `run_claimed_task` 下一轮 `on_poll` 拿到
    False，从而终止自己的子进程并退出（它不会写终态，见那里的注释）。

    worker 收到停止信号时用它，而不是直接 kill 子进程。
    """
    engine.execute(
        "UPDATE ops.task SET lease_expires_at = now() - interval '1 second' "
        'WHERE task_id = %s AND claimed_by = %s', (task_id, worker_id), role=role)


def count_running(worker_id: str, *, role: str = 'reader') -> int:
    return int(engine.scalar(
        "SELECT count(*) FROM ops.task WHERE state = 'running' AND claimed_by = %s",
        (worker_id,), role=role) or 0)


# ---------------------------------------------------------------- 熔断
def fuse(job_key: str, *, role: str = 'app') -> None:
    """
    连败到阈值就停用这个作业（docs/09 §7.5）。

    写 config.job 会经过 `ops.trg_config_history`，页面上能一键回滚 ——
    这一点必须有：自动把一个人的作业关掉，如果关错了又没有撤销入口，
    比不熔断更让人不敢开自动化。
    """
    engine.execute('UPDATE config.job SET enabled = false, updated_at = now() '
                   'WHERE job_key = %s', (job_key,), role=role)
    engine.execute(
        'UPDATE ops.schedule_state SET note = %s, last_result = %s, updated_at = now() '
        'WHERE job_key = %s',
        (f'连续失败已达熔断阈值，作业被自动停用。'
         f'确认原因后在 /blocks 里把 {job_key} 重新启用，或在 ops.config_history 里回滚这一条',
         'fused', job_key), role=role)
