# -*- coding: utf-8 -*-
"""
运维仓储：批次血缘、任务、审计、质量、就绪状态。

血缘是硬要求：md.* 各表的 src_batch_id 是 NOT NULL，没有批次号写不进去。
这样任何一行数据都能回答"谁、什么时候、从哪写进来的"。
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

from app.db import engine


def _json_default(v: Any):
    """
    质量/审计结果里经常直接塞查询返回的行，而 numeric 列回来是 Decimal、
    date 列回来是 datetime.date，两者 json.dumps 都不认。
    在这里统一兜住，而不是让每个调用方各自转一遍 —— 之前 B3 就因为漏转
    Decimal，预检发现了 90 万行问题却没能留痕。
    """
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, (set, frozenset)):
        return sorted(v)
    return str(v)


def _dumps(v: Any) -> str | None:
    return None if v is None else json.dumps(v, default=_json_default, ensure_ascii=False)



# ============================================================ 批次血缘

def begin_batch(source: str, target: str, *, source_path: str | None = None,
                source_mtime: dt.datetime | None = None, role: str = 'etl') -> int:
    return engine.scalar(
        """
        INSERT INTO ops.ingest_batch (source, source_path, source_mtime, target, status)
        VALUES (%s, %s, %s, %s, 'running')
        RETURNING batch_id
        """,
        (source, source_path, source_mtime, target), role=role)


def end_batch(batch_id: int, *, row_count: int = 0, status: str = 'ok',
              message: str | None = None, role: str = 'etl') -> None:
    engine.execute(
        """
        UPDATE ops.ingest_batch
           SET finished_at = now(), row_count = %s, status = %s, message = %s
         WHERE batch_id = %s
        """,
        (row_count, status, message, batch_id), role=role)


def last_batch_mtime(source: str, source_path: str, *, role: str = 'reader'):
    """增量判据：文件 mtime 与上次成功批次比对，未变则跳过。"""
    return engine.scalar(
        """
        SELECT max(source_mtime) FROM ops.ingest_batch
         WHERE source = %s AND source_path = %s AND status = 'ok'
        """,
        (source, source_path), role=role)


def lineage(table: str, key: dict, *, role: str = 'reader') -> dict | None:
    """反查某行数据的来源批次。"""
    if table == 'md.stock_daily':
        sql = """
            SELECT b.* FROM ops.ingest_batch b
             WHERE b.batch_id = (SELECT src_batch_id FROM md.stock_daily
                                  WHERE ts_code = %(ts_code)s AND trade_date = %(trade_date)s)
        """
    else:
        raise ValueError(f'不支持的表: {table}')
    return engine.query_one(sql, key, role=role)


# ============================================================ 数据就绪

def mark_ready(dataset_key: str, trade_date: dt.date, row_count: int,
               batch_id: int | None = None, *, role: str = 'etl') -> None:
    engine.execute(
        """
        INSERT INTO ops.data_ready (dataset_key, trade_date, row_count, batch_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (dataset_key, trade_date) DO UPDATE
           SET row_count = EXCLUDED.row_count, ready_at = now(),
               batch_id = EXCLUDED.batch_id
        """,
        (dataset_key, trade_date, row_count, batch_id), role=role)


def is_ready(dataset_key: str, trade_date: dt.date, *, role: str = 'reader') -> bool:
    return bool(engine.scalar(
        'SELECT 1 FROM ops.data_ready WHERE dataset_key = %s AND trade_date = %s',
        (dataset_key, trade_date), role=role))


def ready_status(start: dt.date | None = None, end: dt.date | None = None,
                 *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT dataset_key, trade_date, row_count, ready_at
          FROM ops.data_ready
         WHERE (%s::date IS NULL OR trade_date >= %s)
           AND (%s::date IS NULL OR trade_date <= %s)
         ORDER BY trade_date DESC, dataset_key
        """,
        (start, start, end, end), role=role)


def latest_ready(dataset_key: str, *, role: str = 'reader'):
    return engine.scalar(
        'SELECT max(trade_date) FROM ops.data_ready WHERE dataset_key = %s',
        (dataset_key,), role=role)


# ============================================================ 任务

def create_task(*, trade_date: dt.date, job_key: str | None = None,
                cap_key: str | None = None, script_id: int | None = None,
                scope: dict | None = None, params: dict | None = None,
                max_retry: int = 2, concurrency_key: str | None = None,
                depends_on: list[int] | None = None, attempt: int = 0,
                role: str = 'app') -> int:
    return engine.scalar(
        """
        INSERT INTO ops.task (job_key, cap_key, script_id, trade_date, scope, params,
                              max_retry, concurrency_key, depends_on, attempt)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING task_id
        """,
        (job_key, cap_key, script_id, trade_date,
         _dumps(scope or {}), _dumps(params or {}),
         max_retry, concurrency_key, depends_on, attempt), role=role)


CLAIM_SQL = """
UPDATE ops.task t SET state = 'running', started_at = now()
 WHERE t.task_id = (
    SELECT c.task_id FROM ops.task c
     WHERE c.state = 'pending'
       AND (c.depends_on IS NULL OR NOT EXISTS (
              SELECT 1 FROM ops.task d
               WHERE d.task_id = ANY(c.depends_on) AND d.state <> 'succeeded'))
       AND (c.concurrency_key IS NULL OR NOT EXISTS (
              SELECT 1 FROM ops.task r
               WHERE r.concurrency_key = c.concurrency_key AND r.state = 'running'))
     ORDER BY c.created_at
     FOR UPDATE SKIP LOCKED
     LIMIT 1)
RETURNING t.*
"""


def claim_task(*, role: str = 'app') -> dict | None:
    """
    拉取一个可执行任务。FOR UPDATE SKIP LOCKED 让多 worker 天然安全。
    依赖未完成、或同 concurrency_key 有任务在跑时不会被选中。
    """
    return engine.query_one(CLAIM_SQL, role=role)


def update_progress(task_id: int, progress: int, message: str | None = None,
                    *, role: str = 'app') -> None:
    engine.execute(
        'UPDATE ops.task SET progress = %s, message = coalesce(%s, message) '
        'WHERE task_id = %s AND state = %s',
        (progress, message, task_id, 'running'), role=role)


def finish_task(task_id: int, state: str, *, message: str | None = None,
                error: str | None = None, result_meta: dict | None = None,
                role: str = 'app') -> None:
    """
    终态写入。cancelled 不会被 succeeded/failed 覆盖 —— 旧系统 _finish_task
    无条件覆盖状态，导致取消功能形同虚设。
    """
    engine.execute(
        """
        UPDATE ops.task
           SET state = %s, finished_at = now(),
               progress = CASE WHEN %s = 'succeeded' THEN 100 ELSE progress END,
               message = coalesce(%s, message), error = %s,
               result_meta = coalesce(%s::jsonb, result_meta)
         WHERE task_id = %s
           AND state NOT IN ('cancelled', 'succeeded', 'failed', 'skipped')
        """,
        (state, state, message, error,
         _dumps(result_meta) if result_meta else None, task_id), role=role)


def request_cancel(task_id: int, *, role: str = 'app') -> int:
    """真取消：置标记 → runner 侧 set Event → 脚本响应 → 终态 cancelled。"""
    return engine.execute(
        "UPDATE ops.task SET cancel_requested = true "
        "WHERE task_id = %s AND state IN ('pending','running')",
        (task_id,), role=role)


def cancel_requested(task_id: int, *, role: str = 'reader') -> bool:
    return bool(engine.scalar(
        'SELECT cancel_requested FROM ops.task WHERE task_id = %s', (task_id,), role=role))


def mark_skipped_downstream(task_id: int, *, role: str = 'app') -> int:
    """上游失败 → 下游标 skipped，而不是静默跑出错误结果。"""
    return engine.execute(
        "UPDATE ops.task SET state = 'skipped', finished_at = now(), "
        "message = '上游任务失败' WHERE state = 'pending' AND %s = ANY(depends_on)",
        (task_id,), role=role)


def requeue_stale_running(*, role: str = 'app') -> int:
    """进程重启后把 running 的任务重新入队。旧系统任务不持久化，重启全丢。"""
    return engine.execute(
        "UPDATE ops.task SET state = 'pending', started_at = NULL, progress = 0, "
        "message = '进程重启后重新入队' WHERE state = 'running'", role=role)


def list_tasks(state: str | None = None, trade_date: dt.date | None = None,
               limit: int = 200, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT task_id, job_key, cap_key, trade_date, state, progress, message,
               attempt, max_retry, cancel_requested, concurrency_key,
               created_at, started_at, finished_at, error,
               extract(epoch FROM coalesce(finished_at, now()) - started_at) AS elapsed_sec
          FROM ops.task
         WHERE (%s::text IS NULL OR state = %s)
           AND (%s::date IS NULL OR trade_date = %s)
         ORDER BY created_at DESC
         LIMIT %s
        """,
        (state, state, trade_date, trade_date, limit), role=role)


def get_task(task_id: int, *, role: str = 'reader') -> dict | None:
    return engine.query_one('SELECT * FROM ops.task WHERE task_id = %s', (task_id,), role=role)


def task_exists_succeeded(job_key: str, trade_date: dt.date, *, role: str = 'reader') -> bool:
    """cron 兜底与就绪事件双触发时，保证同一天只成功一次。"""
    return bool(engine.scalar(
        "SELECT 1 FROM ops.task WHERE job_key = %s AND trade_date = %s "
        "AND state = 'succeeded' LIMIT 1",
        (job_key, trade_date), role=role))


# ============================================================ 审计

def audit(action: str, *, target: str | None = None, params: dict | None = None,
          rows_affected: int | None = None, ok: bool = True,
          error: str | None = None, channel: str = 'web', role: str = 'app') -> None:
    engine.execute(
        """
        INSERT INTO ops.audit (channel, action, target, params, rows_affected, ok, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (channel, action, target, _dumps(params or {}), rows_affected, ok, error),
        role=role)


def recent_audit(limit: int = 100, target_like: str | None = None,
                 *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT at, actor, channel, action, target, params, rows_affected, ok, error
          FROM ops.audit
         WHERE (%s::text IS NULL OR target LIKE %s)
         ORDER BY at DESC LIMIT %s
        """,
        (target_like, target_like, limit), role=role)


def config_history(table_name: str | None = None, limit: int = 100,
                   *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT id, at, actor, table_name, row_key, op, before, after
          FROM ops.config_history
         WHERE (%s::text IS NULL OR table_name = %s)
         ORDER BY at DESC LIMIT %s
        """,
        (table_name, table_name, limit), role=role)


def revert_config(history_id: int, *, role: str = 'app') -> str:
    """
    回滚一条配置变更。返回描述文字。
    UPDATE → 用 before 覆盖；INSERT → 删除；DELETE → 重新插入。
    """
    row = engine.query_one(
        'SELECT * FROM ops.config_history WHERE id = %s', (history_id,), role='reader')
    if not row:
        raise ValueError(f'历史记录 {history_id} 不存在')

    table = row['table_name']
    # UPDATE/DELETE 的撤销 = 恢复 before；INSERT 的撤销 = 删掉 after 那一行
    payload = row['before'] if row['op'] in ('UPDATE', 'DELETE') else row['after']

    if not payload:
        raise ValueError('该记录无可用快照')

    cols = list(payload.keys())
    placeholders = ', '.join(['%s'] * len(cols))
    collist = ', '.join(f'"{c}"' for c in cols)

    if row['op'] == 'INSERT':
        # 需要主键才能删；这里按全字段匹配删除，安全但保守
        where = ' AND '.join(f'"{c}" IS NOT DISTINCT FROM %s' for c in cols)
        n = engine.execute(f'DELETE FROM {table} WHERE {where}',
                           [payload[c] for c in cols], role=role)
        return f'撤销 INSERT，删除 {n} 行'

    updates = ', '.join(f'"{c}" = EXCLUDED."{c}"' for c in cols)
    # 找主键列
    pk = engine.query(
        """
        SELECT a.attname FROM pg_index i
          JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
         WHERE i.indrelid = %s::regclass AND i.indisprimary
        """, (table,), role='reader')
    pk_cols = [r['attname'] for r in pk]
    if not pk_cols:
        raise ValueError(f'{table} 无主键，无法回滚')
    conflict = ', '.join(f'"{c}"' for c in pk_cols)
    n = engine.execute(
        f'INSERT INTO {table} ({collist}) VALUES ({placeholders}) '
        f'ON CONFLICT ({conflict}) DO UPDATE SET {updates}',
        [payload[c] for c in cols], role=role)
    return f'已恢复 {table} {n} 行到变更前状态'


# ============================================================ 质量

def save_quality_result(trade_date: dt.date | None, rule_key: str, target: str,
                        severity: str, passed: bool, *, actual: Any = None,
                        expected: Any = None, sample: Any = None,
                        batch_id: int | None = None, role: str = 'app') -> None:
    engine.execute(
        """
        INSERT INTO ops.quality_result
            (trade_date, rule_key, target, severity, passed, actual, expected, sample, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (trade_date, rule_key, target, severity, passed,
         _dumps(actual), _dumps(expected), _dumps(sample),
         batch_id), role=role)


def quality_report(trade_date: dt.date, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT q.run_at, q.rule_key, r.name, q.target, q.severity, q.passed,
               q.actual, q.expected, q.sample, r.blocking, r.description
          FROM ops.quality_result q
          LEFT JOIN config.quality_rule r USING (rule_key)
         WHERE q.trade_date = %s
           AND q.run_at = (SELECT max(run_at) FROM ops.quality_result
                            WHERE trade_date = %s)
         ORDER BY q.passed, q.severity, q.rule_key
        """,
        (trade_date, trade_date), role=role)


def has_blocking_error(trade_date: dt.date, *, role: str = 'reader') -> bool:
    """块4 的核心权力：有阻断级错误就不写 data_ready，下游加工不触发。"""
    return bool(engine.scalar(
        """
        SELECT 1 FROM ops.quality_result q
          JOIN config.quality_rule r USING (rule_key)
         WHERE q.trade_date = %s AND NOT q.passed AND r.blocking
           AND q.run_at = (SELECT max(run_at) FROM ops.quality_result WHERE trade_date = %s)
         LIMIT 1
        """,
        (trade_date, trade_date), role=role))


def quality_trend(days: int = 30, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT trade_date,
               count(*) FILTER (WHERE NOT passed AND severity = 'error')   AS errors,
               count(*) FILTER (WHERE NOT passed AND severity = 'warning') AS warnings,
               count(*) FILTER (WHERE passed)                             AS passed
          FROM ops.quality_result
         WHERE trade_date IS NOT NULL
         GROUP BY trade_date ORDER BY trade_date DESC LIMIT %s
        """,
        (days,), role=role)


def coverage(start: dt.date | None = None, end: dt.date | None = None,
             *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT dataset_key, trade_date, row_count,
               row_count::numeric / nullif(avg(row_count) OVER (PARTITION BY dataset_key), 0)
                   AS ratio_to_avg
          FROM ops.v_coverage
         WHERE (%s::date IS NULL OR trade_date >= %s)
           AND (%s::date IS NULL OR trade_date <= %s)
         ORDER BY trade_date DESC, dataset_key
        """,
        (start, start, end, end), role=role)


def save_baseline(src_db: str, src_table: str, row_count: int,
                  extra: dict | None = None, *, role: str = 'etl') -> None:
    engine.execute(
        """
        INSERT INTO ops.migration_baseline (src_db, src_table, row_count, extra)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (src_db, src_table) DO UPDATE
           SET row_count = EXCLUDED.row_count, extra = EXCLUDED.extra,
               captured_at = now()
        """,
        (src_db, src_table, row_count, _dumps(extra or {})), role=role)


def baselines(*, role: str = 'reader') -> list[dict]:
    return engine.query(
        'SELECT * FROM ops.migration_baseline ORDER BY src_db, src_table', role=role)
