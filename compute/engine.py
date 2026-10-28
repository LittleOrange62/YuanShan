"""FT `app/db/engine.py` 的本地等价层 —— 只覆盖 d 系列生产者用到的那几个入口。

**为什么不直接 import FinanceTool 的那份**：用户 2026-09-25 说得很清楚，参考项目最终会被删掉。
`compute/` 里任何一根线头搭在 `D:\\FinanceTool` 上，删的那天这套生产者就成废代码。

与 FT 那份的行为差异，都是刻意的：

- 角色只有两条：`reader`→`ft_reader`（只读输入），`app`/`etl`/`admin`→`ft_etl`（写 `ys`）。
  FT 用 `admin` 建的表全在 `market_data`，本层把它落到 `ys` —— 与 FinanceTool 不同名双写
  是这条链的硬边界，不是权限问题。
- `conn(role)` 默认**提交**，`force_rollback=True` 时整批回滚（dry-run 用，与 FT 同参数名）。
- `copy_rows` 走 `execute_values` 而不是 COPY 文本协议：点火链里有 jsonb 列，
  文本格式遇到含制表符/换行的 JSON 会静默错位，值参数化没这个风险。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from decimal import Decimal

import psycopg2
from psycopg2.extras import DictCursor, RealDictCursor

from .db import connect, env  # noqa: F401  (env 供调试与测试复用)

_WRITE_ROLES = ('app', 'etl', 'admin')


def _readonly(role: str) -> bool:
    return role not in _WRITE_ROLES


def query(sql, params=None, *, role: str = 'reader') -> list[dict]:
    conn = connect(readonly=_readonly(role))
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.rollback()
        conn.close()


def query_one(sql, params=None, *, role: str = 'reader') -> dict | None:
    rows = query(sql, params, role=role)
    return rows[0] if rows else None


def scalar(sql, params=None, *, role: str = 'reader'):
    row = query_one(sql, params, role=role)
    return next(iter(row.values())) if row else None


def execute(sql, params=None, *, role: str = 'app') -> int:
    conn = connect(readonly=_readonly(role))
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            n = cur.rowcount
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def conn(role: str = 'reader', force_rollback: bool = False):
    """一条连接用完即走。`force_rollback=True` 用来做 dry-run（跑完什么都不留）。"""
    c = connect(readonly=_readonly(role))
    try:
        yield c
        if force_rollback:
            c.rollback()
        else:
            c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def _literal(v):
    """jsonb 要先序列化成字符串 —— psycopg2 不会自己适配 dict/list。"""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def copy_rows(table: str, cols, rows, *, role: str = 'app', page_size: int = 5000) -> int:
    cols = list(cols)
    sql = 'INSERT INTO %s (%s) VALUES %%s' % (table, ', '.join(cols))
    tmpl = '(' + ', '.join(['%s'] * len(cols)) + ')'
    conn = connect(readonly=_readonly(role))
    n = 0
    try:
        from psycopg2.extras import execute_values
        with conn.cursor() as cur:
            execute_values(cur, sql,
                           [tuple(_literal(v) for v in r) for r in rows],
                           template=tmpl, page_size=page_size)
            n = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return n


def upsert(table: str, cols, rows, update_cols, conflict_cols, *, role: str = 'app') -> int:
    """给没有现成 UPSERT 常量的调用方兜底用；d 系列自带 SQL，不走这里。"""
    cols = list(cols)
    values = ', '.join(['%s'] * len(cols))
    upd = ', '.join('%s = EXCLUDED.%s' % (c, c) for c in update_cols)
    sql = ('INSERT INTO %s (%s) VALUES (%s) ON CONFLICT (%s) DO UPDATE SET %s'
           % (table, ', '.join(cols), values, ', '.join(conflict_cols), upd))
    return execute(sql, [tuple(_literal(v) for v in r) for r in rows], role=role)


def dec(v):
    """把读数统一成 Decimal 或 None —— 与 FT 的 numeric 读数保持同一种类型。"""
    if v is None or isinstance(v, Decimal):
        return v
    return Decimal(str(v))
