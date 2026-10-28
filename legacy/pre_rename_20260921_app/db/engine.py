# -*- coding: utf-8 -*-
"""
数据库连接管理。

按角色维护独立连接池：
  reader  只读，statement_timeout=60s   —— 智能体与页面查询
  app     业务读写                     —— 主程序
  etl     md/ref 写                    —— 同步与批量导入
  admin   DDL                          —— 迁移

连接的 application_name 会带上调用方标识，这样 ops.audit 里能看出是谁改的。
"""
from __future__ import annotations

import atexit
import contextlib
import threading
from collections.abc import Iterator
from typing import Any

import psycopg
from psycopg import sql as pgsql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import config

_pools: dict[str, ConnectionPool] = {}
_lock = threading.Lock()
_app_name = 'financetool'


def set_app_name(name: str) -> None:
    """设置 application_name，便于在 ops.audit 中区分调用方。"""
    global _app_name
    _app_name = name


def _dsn(role: str) -> str:
    dsn = config.DSN.get(role)
    if not dsn:
        raise RuntimeError(
            f'角色 {role!r} 的 DSN 未配置。检查 D:\\FinanceTool\\.env 中的 FT_DSN_{role.upper()}')
    return dsn


def pool(role: str = 'reader') -> ConnectionPool:
    if role not in _pools:
        with _lock:
            if role not in _pools:
                _pools[role] = ConnectionPool(
                    _dsn(role),
                    min_size=1,
                    max_size=config.POOL_SIZE.get(role, 4),
                    kwargs={'application_name': _app_name,
                            'options': f'-c timezone={config.TIMEZONE}'},
                    open=True,
                )
    return _pools[role]


@contextlib.contextmanager
def conn(role: str = 'reader') -> Iterator[psycopg.Connection]:
    """从对应角色的池里借一个连接，退出时自动 commit/rollback 并归还。"""
    with pool(role).connection() as c:
        yield c


@contextlib.contextmanager
def cursor(role: str = 'reader') -> Iterator[psycopg.Cursor]:
    with conn(role) as c, c.cursor(row_factory=dict_row) as cur:
        yield cur


def query(sql: str, params: Any = None, *, role: str = 'reader') -> list[dict]:
    with cursor(role) as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def query_one(sql: str, params: Any = None, *, role: str = 'reader') -> dict | None:
    with cursor(role) as cur:
        cur.execute(sql, params)
        return cur.fetchone() if cur.description else None


def scalar(sql: str, params: Any = None, *, role: str = 'reader') -> Any:
    with conn(role) as c, c.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


def execute(sql: str, params: Any = None, *, role: str = 'app') -> int:
    with conn(role) as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def executemany(sql: str, rows: list[tuple], *, role: str = 'app') -> int:
    with conn(role) as c, c.cursor() as cur:
        cur.executemany(sql, rows)
        return cur.rowcount


@contextlib.contextmanager
def advisory_lock(c: psycopg.Connection, key: str, *, wait: bool = False) -> Iterator[bool]:
    """
    事务级 advisory lock，用于"同一交易日的同一数据集只允许一个写入者"。

    事务结束自动释放，进程崩了也不会留下死锁 —— 比自己维护锁表可靠。
    拿不到锁默认立即返回 False，不死等。
    """
    with c.cursor() as cur:
        if wait:
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (key,))
            got = True
        else:
            cur.execute('SELECT pg_try_advisory_xact_lock(hashtext(%s))', (key,))
            got = bool(cur.fetchone()[0])
    yield got


@contextlib.contextmanager
def session_advisory_lock(key: str, *, role: str = 'app') -> Iterator[bool]:
    """
    **会话级** advisory lock，用于"这类进程整机只允许有一个实例"。

    和上面的 `advisory_lock()` 不是一回事，别混用：那个是 xact 级的，事务一提交
    就释放。调度器每 15 秒一个 tick、每个查询都是独立事务，拿它守单实例会在
    两次 tick 之间把锁松开 —— 第二个调度器正好挤进来，同一个槽触发两遍。

    所以这里**独占一条裸连接**（不走池：池会把连接还回去，会话锁随之失效），
    进程退出或被强杀时连接断开，PG 自动释放，不会留下需要人工清理的死锁。
    """
    dsn = config.DSN.get(role)
    if not dsn:
        raise RuntimeError(f'角色 {role!r} 的 DSN 未配置，检查 .env 的 FT_DSN_{role.upper()}')
    c = psycopg.connect(dsn, application_name=_app_name,
                        options=f'-c timezone={config.TIMEZONE}', autocommit=True)
    got = False
    try:
        with c.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(hashtext(%s))', (key,))
            got = bool(cur.fetchone()[0])
        yield got
    finally:
        if got:
            with contextlib.suppress(Exception), c.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock(hashtext(%s))', (key,))
        with contextlib.suppress(Exception):
            c.close()


def copy_rows(table: str, columns: list[str], rows: Iterator[tuple],
              *, role: str = 'etl') -> int:
    """
    用 COPY 批量写入。比逐行 executemany 快一个数量级。

    调用方通常先 COPY 进 UNLOGGED 临时表，再用一条 INSERT ... ON CONFLICT
    合并到主表，从而兼顾速度与幂等。
    """
    stmt = pgsql.SQL('COPY {} ({}) FROM STDIN').format(
        pgsql.SQL('.').join(pgsql.Identifier(p) for p in table.split('.')),
        pgsql.SQL(', ').join(pgsql.Identifier(col) for col in columns),
    )
    n = 0
    with conn(role) as c, c.cursor() as cur, cur.copy(stmt) as cp:
        for row in rows:
            cp.write_row(row)
            n += 1
    return n


def close_all() -> None:
    with _lock:
        for p in _pools.values():
            with contextlib.suppress(Exception):
                p.close()
        _pools.clear()


# 连接池自带后台工作线程。若拖到解释器 finalization 阶段才由 __del__ 回收，
# join 线程会抛 PythonFinalizationError（Python 3.14 实测）。用 atexit 提前收干净。
atexit.register(close_all)


def healthcheck() -> dict:
    try:
        with conn('reader') as c, c.cursor() as cur:
            cur.execute('SELECT version(), current_database(), now()')
            ver, db, now = cur.fetchone()
        return {'ok': True, 'version': ver.split(',')[0], 'database': db, 'now': str(now)}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': str(exc)}
