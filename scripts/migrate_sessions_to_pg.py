"""一次性把会话从本机 SQLite 搬进 PostgreSQL 的 `ys` schema。

用什么身份写：`compute/db.py` 已经在读的 `PG_ETL_USER`（ft_etl）。它对 schema `ys` 有
CREATE+USAGE（2026-09-25 实测），所以建表 + 搬数都不需要 `ft_admin`，管理员口令不进 YuanShan。

幂等：逐行 `ON CONFLICT DO NOTHING`，重跑只会报"已在库里"，不会翻倍；结束时逐表比行数。
搬完不删 SQLite —— 它退成只读备份，旁边写一份 `conversations.sqlite3.migrated.json` 记下
搬了多少行、落到哪张表、什么时候。要回退只需把 `YS_SESSION_BACKEND=sqlite` 设上再重启。

跑法：
    python scripts/migrate_sessions_to_pg.py          # 演习，只报会搬多少
    python scripts/migrate_sessions_to_pg.py --apply  # 真搬
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compute import db as pgdb                            # noqa: E402
from review_agent import session_backend                  # noqa: E402

ROOT = Path(os.environ.get("ASTOCK_AGENT_HOME", "~/.vibe-astock-agent")).expanduser()
SRC = ROOT / "conversations.sqlite3"
# 目标表名向 `session_backend` 要，脚本里不写死 `ys.xxx`：改名时真相只有一处。
T = {k: v[1] for k, v in session_backend.TABLES.items()}

# (源表, 目标键, 列, 取值函数)
SHIPS = (
    ("conversations", "conv",
     ("id", "anchor", "title", "source", "bundle", "created", "category", "updated"),
     lambda r, now: (r["id"], r["anchor"], r["title"], r["source"], r["bundle"], float(r["created"]),
                     # 旧行不回填分类：分类规则是后来才上线的，猜一个值会让"这条当时没被分类"
                     # 变成"这条被分成了其他"。界面上照原样显示「未分类」。
                     r.get("category") or "",
                     # 旧行 updated 恒为 0 会被"按最近更新排序"压到最后，那不是本意。
                     float(r.get("updated") or 0) or float(r["created"]))),
    ("turns", "turn",
     ("id", "conversation_id", "request_id", "fingerprint", "question", "status",
      "result", "error", "events", "created", "updated"),
     lambda r, now: (r["id"], r["conversation_id"], r["request_id"], r["fingerprint"], r["question"],
                     r["status"], r.get("result"), r.get("error"), r.get("events") or "[]",
                     float(r["created"]), float(r["updated"]))),
    ("observations", "obs",
     ("id", "turn_id", "metric", "direction", "baseline", "created", "rule"),
     lambda r, now: (r["id"], r["turn_id"], r["metric"], r["direction"], r["baseline"],
                     float(r["created"]), r.get("rule"))),
    ("observation_checks", "chk",
     ("id", "observation_id", "result", "created"),
     lambda r, now: (r["id"], r["observation_id"], r["result"], float(r["created"]))),
)


def read_source() -> dict[str, list[dict]]:
    if not SRC.exists():
        raise SystemExit(f"没找到源库：{SRC}")
    conn = sqlite3.connect(SRC)
    conn.row_factory = sqlite3.Row
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        out = {name: [dict(r) for r in conn.execute(f"SELECT * FROM {name}")]
               for name, *_ in SHIPS if name in have}
        out["_missing"] = sorted(name for name, *_ in SHIPS if name not in have)
        # 标签在旧库里是 conversations.tags 的 JSON 数组，新库换成关联表并记住位置。
        # 老版本库根本没这一列，所以从 `SELECT *` 里取，别写死列名。
        legacy = [{"conversation_id": r["id"], "tags": r.get("tags") or "[]",
                   "created": float(r.get("created") or 0)}
                  for r in out.get("conversations", [])]
        out["_legacy_tags"] = legacy
        return out
    finally:
        conn.close()


def tag_rows(legacy: list[dict]) -> list[tuple]:
    rows = []
    for item in legacy:
        try:
            tags = json.loads(item["tags"])
        except ValueError:
            tags = []
        if not isinstance(tags, list):
            continue
        for position, tag in enumerate(tags):
            if isinstance(tag, str) and tag.strip():
                rows.append((item["conversation_id"], tag.strip(), position, item["created"] or time.time()))
    return rows


def insert(cur, table: str, columns: tuple[str, ...], rows: list[tuple]) -> int:
    if not rows:
        return 0
    marks = ",".join(["%s"] * len(columns))
    sql = (f"INSERT INTO {table} ({','.join(columns)}) VALUES ({marks}) "
           "ON CONFLICT DO NOTHING")
    added = 0
    for row in rows:
        cur.execute(sql, row)
        added += max(0, cur.rowcount)
    return added


def main() -> int:
    apply = "--apply" in sys.argv
    source = read_source()
    if source["_missing"]:
        print(f"源库里没有这些表（当作空表）：{'、'.join(source['_missing'])}")
    payload = [(key, columns, [values(row, 0.0) for row in source.get(name, [])])
               for name, key, columns, values in SHIPS]
    tags = tag_rows(source["_legacy_tags"])
    payload.append(("tag", ("conversation_id", "tag", "position", "created"), tags))

    print(f"源库 {SRC}")
    for key, _columns, rows in payload:
        print(f"  {T[key]:26s} 待搬 {len(rows)} 行")
    if not apply:
        print("演习模式：没加 --apply，什么都没写。")
        return 0

    backend = session_backend.PgBackend(**session_backend.pg_params())
    with backend.connect() as handle:
        backend.script(handle, list(session_backend.SCHEMA))   # 建表幂等，先跑一遍
    conn = pgdb.connect(readonly=False)
    try:
        with conn.cursor() as cur:
            report = {T[key]: insert(cur, T[key], columns, rows) for key, columns, rows in payload}
        conn.commit()
        with conn.cursor() as cur:
            after = {}
            for key in ("conv", "turn", "tag", "obs", "chk"):
                cur.execute(f"SELECT count(*) FROM {T[key]}")
                after[T[key]] = int(cur.fetchone()[0])
        print("本次新插入：" + json.dumps(report, ensure_ascii=False))
        print("落库后总行数：" + json.dumps(after, ensure_ascii=False))
        sentinel = SRC.with_name(SRC.name + ".migrated.json")
        sentinel.write_text(json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": str(SRC), "target": "postgresql/ys", "tables": T,
            "inserted_now": report, "counts_after": after,
            "note": "SQLite 退为只读备份。会话读写走 PG（YS_SESSION_BACKEND 默认 auto）。"
                    "旧库 conversations.tags 那一列的内容已搬进 ys.message_tag，原列留在旧文件里不再读写。"},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"备份说明：{sentinel}")
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"搬运失败，已整体回滚：{type(exc).__name__}: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
