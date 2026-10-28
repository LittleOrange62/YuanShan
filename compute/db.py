"""qd 轨的写连接 —— 与只读层 `duanxian.pg` 严格分开。

为什么要单独一份：`duanxian/pg.py` 的铁律是"只读、只认 ft_reader"，读路径不许
出现写账号。但 qd 轨的勘误回填必须落库，而 `ft_etl` 对 9 张 `qd` 表本来就有
INSERT/UPDATE/DELETE（2026-09-25 实测 `has_table_privilege`），**UPDATE 不需要
CREATE 权限** —— 所以这一步不依赖 `ys` 影子 schema 的授权，能自己做。

凭据只在 `PG_ETL_USER`/`PG_ETL_PASSWORD` 两个环境变量里（`.env`），本模块不打印
口令、不接受从命令行传密钥。
"""

from __future__ import annotations

import os

DEFAULTS = {"PG_HOST": "127.0.0.1", "PG_PORT": "5432", "PG_DB": "financetool"}
_ENV_KEYS = ("PG_HOST", "PG_PORT", "PG_DB", "PG_USER", "PG_PASSWORD",
             "PG_ETL_USER", "PG_ETL_PASSWORD")
_loaded = False


def _load_env_once() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in _ENV_KEYS and k not in os.environ:
                os.environ[k] = v


def env(name: str) -> str:
    _load_env_once()
    return (os.environ.get(name) or DEFAULTS.get(name) or "").strip()


def connect(readonly: bool = False):
    """拿一条连接。readonly=True 走 ft_reader，写路径走 ft_etl。

    写连接**不开 autocommit**：调用方要么显式 commit，要么整批回滚 —— 勘误回填
    最怕的是跑了一半留下半新半旧的基线。
    """
    import psycopg2

    user, pw = ((env("PG_USER"), env("PG_PASSWORD")) if readonly
                else (env("PG_ETL_USER"), env("PG_ETL_PASSWORD")))
    if not user or not pw:
        raise RuntimeError("缺少 %s 凭据（在 .env 里配置，别写进代码）"
                           % ("PG_USER/PG_PASSWORD" if readonly else "PG_ETL_USER/PG_ETL_PASSWORD"))
    conn = psycopg2.connect(host=env("PG_HOST"), port=env("PG_PORT"), dbname=env("PG_DB"),
                            user=user, password=pw, connect_timeout=8,
                            application_name="yuanShan-compute")
    return conn


def dict_cursor(conn):
    from psycopg2.extras import RealDictCursor
    # RealDictRow 是 dict 的子类，`dict(row)` 才拿得到；普通 DictRow 不是
    return conn.cursor(cursor_factory=RealDictCursor)


def query(sql, params=None, *, readonly: bool = True):
    """一次性查询，返回 dict 列表。默认走只读账号。"""
    conn = connect(readonly)
    try:
        with dict_cursor(conn) as cur:
            cur.execute(sql, params)
            return [] if cur.description is None else [dict(r) for r in cur.fetchall()]
    finally:
        conn.rollback()
        conn.close()
