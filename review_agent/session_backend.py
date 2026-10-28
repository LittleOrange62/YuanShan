"""会话库的两个后端：本机 SQLite 文件与 PostgreSQL 的 `ys` schema。

**一份 Store 逻辑、两个后端，而不是两份 Store。** 会话身份、请求幂等编号、全局单任务
锁、二十轮上限这些语义只能有一份实现；抄成两份一定会漂移，而漂移的表现是"PG 上跑得通、
测试里跑不通"或反之，最难查。所以 `store.py` 里的 SQL 两边通用，差异只有六类，全收在这里：

  · 连接与游标 —— sqlite3 直接 `db.execute`，psycopg2 要开游标；
  · 占位符 —— `?` 对 `%s`；
  · 表名 —— SQLite 沿用既有复数表名（老库不改名），PG 按 2026-09-25 裁定用单数 + `ys.` 前缀；
    SQL 里一律写 `{conv}`/`{turn}`/`{tag}`/`{obs}`/`{chk}`，由后端替换；
  · 建表与补列 —— `executescript`+`PRAGMA table_info` 对 `information_schema`+`ALTER … ADD COLUMN`；
  · 独占写 —— `BEGIN IMMEDIATE` 对表级 `SHARE ROW EXCLUSIVE`（不挡读，写只许一个）；
  · 两条 JSON 筛选表达式 —— `json_extract` 与 `::jsonb ->>` 没有通用写法。

JSON 列（source/bundle/result/events/tags）在两边**都存成文本而不是 jsonb**：Python 侧
`json.loads` 的语义于是完全一致，`canonical()` 出来的字符串两边逐字节相同，只有真正需要
在库里筛 JSON 的那两处走方言表达式。标签（tags）例外，它落在 `{tag}` 表里 —— 两边同一条
`EXISTS` 就能筛，不用各自的 JSON 表值函数，代价是要多维护一张关联表。

凭据走 `compute/db.py` 已经在读的 `PG_ETL_USER`/`PG_ETL_PASSWORD`：`ft_etl` 对 schema `ys`
有 CREATE+USAGE（2026-09-25 实测 `has_schema_privilege` 双双为 true），所以建表不需要
`ft_admin`，本模块也绝不碰别的 schema。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .evidence import EvidenceError

# 语义名 -> (SQLite 表名, PG 表名)。SQLite 那列故意保留既有名字：迁移只是把行拷过去，
# 不是把 SQLite 重建一遍，旧文件要能原样当只读备份打开。
TABLES = {
    "conv": ("conversations", "ys.conversation"),
    "turn": ("turns", "ys.turn"),
    "tag": ("message_tags", "ys.message_tag"),
    "obs": ("observations", "ys.observation"),
    "chk": ("observation_checks", "ys.observation_check"),
}

# 索引名不能带 schema 前缀（PG 的索引固定待在所属表的 schema 里），所以索引名单独一张表：
# SQLite 沿用既有名字（老库上 `IF NOT EXISTS` 直接命中，不重复建），PG 用去前缀的单数名。
INDEXES = {
    "ix_turn_conv": ("turns_conversation", "turn_conversation"),
    "ix_tag": ("message_tags_tag", "message_tag_tag"),
}

# 建表语句两边通用：类型只用双方都接受的写法（`double precision` 在 SQLite 里落到 REAL 亲和，
# 存的就是 8 字节浮点，与原来的 `REAL` 无差）。
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS {conv} (
           id TEXT PRIMARY KEY, anchor TEXT NOT NULL, title TEXT NOT NULL,
           source TEXT NOT NULL, bundle TEXT NOT NULL, created double precision NOT NULL,
           category TEXT NOT NULL DEFAULT '', updated double precision NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS {turn} (
           id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, request_id TEXT UNIQUE NOT NULL,
           fingerprint TEXT NOT NULL, question TEXT NOT NULL, status TEXT NOT NULL,
           result TEXT, error TEXT, events TEXT NOT NULL DEFAULT '[]',
           created double precision NOT NULL, updated double precision NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS {tag} (
           conversation_id TEXT NOT NULL, tag TEXT NOT NULL,
           position integer NOT NULL DEFAULT 0, created double precision NOT NULL,
           PRIMARY KEY (conversation_id, tag))""",
    """CREATE TABLE IF NOT EXISTS {obs} (
           id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, metric TEXT NOT NULL,
           direction TEXT NOT NULL, baseline TEXT NOT NULL, created double precision NOT NULL,
           rule TEXT, UNIQUE(turn_id, metric, direction))""",
    """CREATE TABLE IF NOT EXISTS {chk} (
           id TEXT PRIMARY KEY, observation_id TEXT NOT NULL, result TEXT NOT NULL,
           created double precision NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS {ix_turn_conv} ON {turn}(conversation_id, created)",
    "CREATE INDEX IF NOT EXISTS {ix_tag} ON {tag}(tag)",
)

# 旧库补列。SQLite 的 ADD COLUMN 没有 IF NOT EXISTS，所以统一先查列再补。
ADDITIONS = {"conv": (("category", "TEXT NOT NULL DEFAULT ''"),
                      ("updated", "double precision NOT NULL DEFAULT 0")),
             "obs": (("rule", "TEXT"),)}


class Row(dict):
    """`row["col"]` 与 `row[0]` 都支持 —— 老代码两种写法都有，不为此改调用点。"""

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


class Backend:
    """共同面：SQL 改写、建表、补列。子类只补真正不同的那几处。"""

    name = ""
    pg = False

    def __init__(self):
        self.tables = {**{k: v[1 if self.pg else 0] for k, v in TABLES.items()},
                       **{k: v[1 if self.pg else 0] for k, v in INDEXES.items()}}

    def q(self, sql: str) -> str:
        for name, table in self.tables.items():
            sql = sql.replace("{" + name + "}", table)
        if "{" in sql or "}" in sql:
            raise EvidenceError(f"会话库 SQL 里有未知表名占位：{sql[:80]}")
        return self.placeholder(sql)

    def placeholder(self, sql: str) -> str:
        return sql

    def script(self, db, statements) -> None:
        for statement in statements:
            db.execute(statement)

    def columns(self, db, table_key: str) -> set[str]:
        raise NotImplementedError

    @contextmanager
    def connect(self):
        raise NotImplementedError

    def begin_immediate(self, db) -> None:
        raise NotImplementedError

    def ensure_schema(self) -> None:
        with self.connect() as db:
            self.script(db, list(SCHEMA))
            for key, extra in ADDITIONS.items():
                have = self.columns(db, key)
                for name, definition in extra:
                    if name not in have:
                        # 旧会话不猜分类：留空，界面上如实显示"未分类"。
                        db.execute(f"ALTER TABLE {self.tables[key]} ADD COLUMN {name} {definition}")

    # 只有这两处必须在库里读 JSON：context.mode 与 context.page 决定会话归属哪个页面/模式。
    def mode_sql(self) -> str:
        raise NotImplementedError

    def page_sql(self) -> str:
        raise NotImplementedError


class SqliteBackend(Backend):
    name = "sqlite"

    def __init__(self, root: Path):
        super().__init__()
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "conversations.sqlite3"
        if self.path.is_symlink():
            raise EvidenceError("会话数据库不能是符号链接")

    @contextmanager
    def connect(self):
        raw = sqlite3.connect(self.path, timeout=10)
        raw.row_factory = sqlite3.Row
        try:
            with raw:
                yield _SqliteDb(self, raw)
        finally:
            raw.close()

    def columns(self, db, table_key):
        return {row[1] for row in db.execute(f"PRAGMA table_info({self.tables[table_key]})")}

    def begin_immediate(self, db) -> None:
        db.execute("BEGIN IMMEDIATE")

    def ensure_schema(self) -> None:
        super().ensure_schema()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
        os.chmod(self.path, 0o600)

    def mode_sql(self) -> str:
        return "coalesce(json_extract({conv}.bundle,'$.context.mode'),'agent')"

    def page_sql(self) -> str:
        return "coalesce(json_extract({conv}.bundle,'$.context.page'),'')"


class _SqliteDb:
    def __init__(self, backend: SqliteBackend, raw):
        self._be, self._raw = backend, raw

    def execute(self, sql, params=()):
        return self._raw.execute(self._be.q(sql), params)

    def executescript(self, script):
        return self._raw.executescript(script)


class PgBackend(Backend):
    name = "pg"
    pg = True

    def __init__(self, *, user: str, password: str, host: str, port: str, database: str,
                 application_name: str = "yuanShan-sessions"):
        super().__init__()
        if not user or not password:
            raise EvidenceError("会话库要用 PostgreSQL，但 .env 里缺 PG_ETL_USER/PG_ETL_PASSWORD")
        self.params = dict(host=host, port=port, dbname=database, user=user, password=password)
        self.application_name = application_name
        self._local = threading.local()

    # 每线程一条长连接：单开一条要 ~160ms，而前端轮询 `/turns/{id}` 一秒一次。
    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None and conn.closed:
            conn = None
        if conn is None:
            import psycopg2

            conn = psycopg2.connect(connect_timeout=8, application_name=self.application_name,
                                    **self.params)
            self._local.conn = conn
        return conn

    @contextmanager
    def connect(self):
        conn = self._conn()
        try:
            yield _PgDb(self, conn)
            conn.commit()
        except Exception:
            conn.rollback()   # 事务里出错只回滚这一段，连接留给本线程下一次用
            raise

    def placeholder(self, sql: str) -> str:
        # 会话库的 SQL 里 `?` 只当占位符（中文提示用的是全角问号），直接换 %s。
        return sql.replace("?", "%s")

    def columns(self, db, table_key):
        schema, _, table = self.tables[table_key].partition(".")
        return {row["column_name"] for row in db.execute(
            "select column_name from information_schema.columns "
            "where table_schema=? and table_name=?", (schema, table))}

    def begin_immediate(self, db) -> None:
        # 对 SQLite `BEGIN IMMEDIATE` 的等价物：读不挡，写只放一个事务进来。
        db.execute("SET LOCAL lock_timeout = '10s'")
        db.execute(f"LOCK TABLE {self.tables['turn']} IN SHARE ROW EXCLUSIVE MODE")
        db.execute(f"LOCK TABLE {self.tables['conv']} IN SHARE ROW EXCLUSIVE MODE")

    def mode_sql(self) -> str:
        return "coalesce((NULLIF({conv}.bundle,'')::jsonb->'context'->>'mode'),'agent')"

    def page_sql(self) -> str:
        return "coalesce((NULLIF({conv}.bundle,'')::jsonb->'context'->>'page'),'')"


class _PgDb:
    """把 psycopg2 的游标包成 sqlite3 那副样子：`execute` 直接交回可 fetch/可迭代的结果。"""

    def __init__(self, backend: PgBackend, conn):
        self._be, self._conn = backend, conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        try:
            cur.execute(self._be.q(sql), params)
            return _PgResult(cur)
        finally:
            cur.close()

    def executescript(self, script):
        for statement in (s for s in script.split(";") if s.strip()):
            self.execute(statement)


class _PgResult:
    def __init__(self, cur):
        self.rowcount = cur.rowcount
        names = [d[0] for d in cur.description] if cur.description else []
        self._rows = [Row(zip(names, values)) for values in cur.fetchall()] if names else []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


def pg_params() -> dict:
    from compute import db as compute_db

    return dict(user=compute_db.env("PG_ETL_USER"), password=compute_db.env("PG_ETL_PASSWORD"),
                host=compute_db.env("PG_HOST"), port=compute_db.env("PG_PORT"),
                database=compute_db.env("PG_DB"))


def pg_configured() -> bool:
    try:
        params = pg_params()
    except Exception:
        return False
    return bool(params["user"] and params["password"])


def build(root: Path, backend: str | None = None) -> Backend:
    """挑后端。默认 `auto`：用户 2026-09-25 裁定"PG 是唯一数据库"，所以配了 ft_etl 就走 PG，
    只有拿不到凭据时（全新机器、离线跑测试）才退回本机 SQLite 文件。"""
    picked = (backend if backend is not None else os.environ.get("YS_SESSION_BACKEND")) or "auto"
    picked = picked.strip().lower()
    if picked == "auto":
        return PgBackend(**pg_params()) if pg_configured() else SqliteBackend(root)
    if picked == "sqlite":
        return SqliteBackend(root)
    if picked == "pg":
        return PgBackend(**pg_params())
    raise EvidenceError(f"YS_SESSION_BACKEND 只认 auto/sqlite/pg，收到 {picked!r}")
