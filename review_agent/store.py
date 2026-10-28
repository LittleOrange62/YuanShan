"""Conversation identity, turn state and request idempotency.

存储后端可以是本机 SQLite 文件，也可以是 PostgreSQL 的 `ys` schema —— 语义只有一份，
差异全在 `session_backend.py`（连接、占位符、表名、建表、独占写、两条 JSON 筛选表达式）。
用户 2026-09-25 裁定"PG 是唯一数据库"，所以线上默认走 PG，SQLite 退成只读备份；
测试与离线机器仍可用 `YS_SESSION_BACKEND=sqlite` 落在临时目录里。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import classify, session_backend
from .evidence import EvidenceError, canonical, digest, valid_date


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise EvidenceError("会话或任务编号无效")
    return value


class Store:
    def __init__(self, root: Path, *, backend: str | None = None):
        self.root = Path(root)
        self.backend = session_backend.build(self.root, backend)
        self.name = self.backend.name
        # 只有 SQLite 后端有文件；PG 模式下 `root` 仍然要建 —— 它装着 backend.lock、
        # runs/、daily/ 这些非数据库产物，Manager 依赖它。
        self.path = getattr(self.backend, "path", None)
        self.backend.ensure_schema()

    @contextmanager
    def connect(self):
        with self.backend.connect() as db:
            yield db

    def write_lock(self, db) -> None:
        self.backend.begin_immediate(db)

    @staticmethod
    def _recover(db) -> None:
        # A worker has a shorter hard timeout. On process restart no new worker
        # resumes or re-bills the old turn; its terminal state is made explicit.
        now = time.time()
        if not db.execute("SELECT 1 FROM {turn} WHERE status='running' AND updated<? LIMIT 1",
                          (now - 720,)).fetchone():
            return
        db.execute("UPDATE {turn} SET status='failed', error=?, updated=? "
                   "WHERE status='running' AND updated<?",
                   ("上次任务已中断或超时，可重新提问；此前成功结果已保留。", now, now - 720))

    def start(self, anchor: str, question: str, request_id: str, source: dict,
              bundle_factory, conversation_id: str | None = None, context: dict | None = None) -> tuple[dict, bool]:
        valid_date(anchor)
        identifier(request_id)
        if conversation_id is not None:
            identifier(conversation_id)
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
            raise EvidenceError("问题不能为空，且最多两千字")
        source_json = canonical(source)
        fingerprint = digest([anchor, question, conversation_id, source, context])
        with self.connect() as db:
            self.write_lock(db)
            self._recover(db)
            old = db.execute("SELECT * FROM {turn} WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise EvidenceError("同一请求编号不能用于不同问题或配置")
                return self._decode_turn(old), False
            if db.execute("SELECT 1 FROM {turn} WHERE status='running'").fetchone():
                raise EvidenceError("已有 Agent 任务在运行，请等待完成或取消")
            if conversation_id:
                conv = db.execute("SELECT * FROM {conv} WHERE id=?", (conversation_id,)).fetchone()
                if not conv or conv["anchor"] != anchor:
                    raise EvidenceError("会话不属于所选复盘日期")
                if conv["source"] != source_json:
                    raise EvidenceError("AI 来源已变化，请新建会话")
                saved_context = json.loads(conv["bundle"]).get("context", {"allow_network": False, "symbol": ""})
                if context is not None and saved_context != context:
                    raise EvidenceError("取数范围已变化，请新建会话")
                if db.execute("SELECT count(*) AS n FROM {turn} WHERE conversation_id=?",
                              (conversation_id,)).fetchone()["n"] >= 20:
                    raise EvidenceError("本会话已到二十轮，请新建会话")
            else:
                bundle = bundle_factory()
                conversation_id = uuid.uuid4().hex
                now = time.time()
                db.execute("INSERT INTO {conv} (id,anchor,title,source,bundle,created,category,updated)"
                           " VALUES (?,?,?,?,?,?,?,?)",
                           (conversation_id, anchor, question[:60], source_json, canonical(bundle), now,
                            classify.category(question), now))
            turn_id = uuid.uuid4().hex
            db.execute("INSERT INTO {turn} (id,conversation_id,request_id,fingerprint,question,status,created,updated)"
                       " VALUES (?,?,?,?,?,'running',?,?)",
                       (turn_id, conversation_id, request_id, fingerprint, question, time.time(), time.time()))
            db.execute("UPDATE {conv} SET updated=? WHERE id=?", (time.time(), conversation_id))
        return self.turn(turn_id), True

    def turn(self, turn_id: str) -> dict:
        identifier(turn_id)
        with self.connect() as db:
            self._recover(db)
            row = db.execute("SELECT * FROM {turn} WHERE id=?", (turn_id,)).fetchone()
        if row is None:
            raise EvidenceError("任务不存在")
        return self._decode_turn(row)

    @staticmethod
    def _decode_turn(row) -> dict:
        out = dict(row)
        out["result"] = json.loads(out["result"]) if out["result"] else None
        out["events"] = json.loads(out["events"])
        out.pop("fingerprint", None)
        return out

    def conversation(self, conversation_id: str, *, internal: bool = False) -> dict:
        identifier(conversation_id)
        with self.connect() as db:
            self._recover(db)
            row = db.execute("SELECT * FROM {conv} WHERE id=?", (conversation_id,)).fetchone()
            turns = db.execute("SELECT id FROM {turn} WHERE conversation_id=? ORDER BY created",
                               (conversation_id,)).fetchall()
            tags = self._tags_for(db, [conversation_id]).get(conversation_id, [])
        if not row:
            raise EvidenceError("会话不存在")
        out = dict(row)
        bundle = json.loads(out.pop("bundle"))
        out["source"] = json.loads(out["source"])
        out["revision"] = bundle["revision"]
        out["dates"] = bundle["dates"]
        out["context"] = bundle.get("context", {"allow_network": False, "symbol": ""})
        out["targets"] = bundle.get("targets", [])
        out["tags"] = tags
        out["turns"] = [self.turn(t["id"]) for t in turns]
        if internal:
            out["bundle"] = bundle
        return out

    def rename(self, conversation_id: str, title: str) -> dict:
        identifier(conversation_id)
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 60:
            raise EvidenceError("会话名称不能为空，且最多六十字")
        clean = re.sub(r"[\x00-\x1f\x7f]", "", title).strip()
        if not clean:
            raise EvidenceError("会话名称没有有效内容")
        with self.connect() as db:
            self.write_lock(db)
            self._recover(db)
            if db.execute("UPDATE {conv} SET title=?,updated=? WHERE id=?",
                          (clean, time.time(), conversation_id)).rowcount != 1:
                raise EvidenceError("会话不存在")
        return {"ok": True, "title": clean}

    def recategorize(self, conversation_id: str, category: str) -> dict:
        identifier(conversation_id)
        if not isinstance(category, str) or len(category.strip()) > 20:
            raise EvidenceError("分类名最多二十字")
        clean = re.sub(r"[\x00-\x1f\x7f]", "", category).strip()
        with self.connect() as db:
            self.write_lock(db)
            self._recover(db)
            if db.execute("UPDATE {conv} SET category=?,updated=? WHERE id=?",
                          (clean, time.time(), conversation_id)).rowcount != 1:
                raise EvidenceError("会话不存在")
        return {"ok": True, "category": clean}

    def retag(self, conversation_id: str, tags: list) -> dict:
        identifier(conversation_id)
        if not isinstance(tags, list) or len(tags) > 8:
            raise EvidenceError("标签最多八个")
        clean = []
        for tag in tags:
            if not isinstance(tag, str):
                raise EvidenceError("标签必须是文字")
            item = re.sub(r"[\x00-\x1f\x7f]", "", tag).strip().strip("#")
            if not 1 <= len(item) <= 16:
                raise EvidenceError("每个标签一到十六字")
            if item not in clean:
                clean.append(item)
        with self.connect() as db:
            self.write_lock(db)
            self._recover(db)
            if db.execute("SELECT 1 FROM {conv} WHERE id=?", (conversation_id,)).fetchone() is None:
                raise EvidenceError("会话不存在")
            # 标签整体覆盖：先删后插，顺序按用户给的次序存进 position，读回来还是那个次序。
            db.execute("DELETE FROM {tag} WHERE conversation_id=?", (conversation_id,))
            now = time.time()
            for position, item in enumerate(clean):
                db.execute("INSERT INTO {tag} (conversation_id,tag,position,created) VALUES (?,?,?,?)",
                           (conversation_id, item, position, now))
            db.execute("UPDATE {conv} SET updated=? WHERE id=?", (now, conversation_id))
        return {"ok": True, "tags": clean}

    def _tags_for(self, db, conversation_ids: list[str]) -> dict[str, list[str]]:
        """一次问完一批会话的标签：逐个会话查会让"打开历史列表"变成 N 次往返。"""
        ids = [identifier(v) for v in conversation_ids]
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = db.execute(f"SELECT conversation_id,tag FROM {{tag}} WHERE conversation_id IN ({marks})"
                          " ORDER BY conversation_id,position", ids).fetchall()
        out: dict[str, list[str]] = {v: [] for v in ids}
        for row in rows:
            out.setdefault(row["conversation_id"], []).append(row["tag"])
        return out

    def _scope_sql(self) -> str:
        return (f"(?='' OR anchor=?)\n"
                f"                AND {self.backend.mode_sql()}=?\n"
                f"                AND {self.backend.page_sql()}=?")

    def list_conversations(self, anchor: str = "", mode: str = "agent", page: str = "",
                           *, category: str = "", tag: str = "", search: str = "", limit: int = 50) -> list[dict]:
        if anchor:
            valid_date(anchor)
        if mode not in ("agent", "direct") or len(page) > 80 or len(category) > 20 or len(tag) > 16:
            raise EvidenceError("会话范围无效")
        if not isinstance(search, str) or len(search) > 100:
            raise EvidenceError("搜索词最多一百字")
        limit = max(1, min(int(limit or 50), 200))
        needle = f"%{search.replace('%', '').replace('_', '')}%"
        with self.connect() as db:
            rows = db.execute(f"""
                SELECT id,title,anchor,created,category,updated FROM {{conv}}
                WHERE {self._scope_sql()}
                AND (?='' OR category=?)
                AND (?='' OR EXISTS (SELECT 1 FROM {{tag}} mt WHERE mt.conversation_id={{conv}}.id AND mt.tag=?))
                AND (?='' OR title LIKE ? OR id IN (SELECT conversation_id FROM {{turn}} WHERE question LIKE ?))
                ORDER BY updated DESC, created DESC LIMIT ?
            """, (anchor, anchor, mode, page, category, category, tag, tag, search, needle, needle, limit)).fetchall()
            found = [dict(r) for r in rows]
            tags = self._tags_for(db, [r["id"] for r in found])
            for item in found:
                item["tags"] = tags.get(item["id"], [])
            return found

    def facets(self, mode: str = "agent", page: str = "") -> dict:
        """界面筛选用的字典：本范围内真实出现过的分类与标签，附条数，不给空选项。"""
        if mode not in ("agent", "direct") or len(page) > 80:
            raise EvidenceError("会话范围无效")
        with self.connect() as db:
            rows = db.execute(f"SELECT id,category FROM {{conv}} WHERE {self._scope_sql()}",
                              ("", "", mode, page)).fetchall()
            ids = [r["id"] for r in rows]
            marks = ",".join("?" for _ in ids)
            tag_rows = db.execute(f"SELECT tag, count(*) AS n FROM {{tag}} WHERE conversation_id IN ({marks})"
                                  " GROUP BY tag", ids).fetchall() if ids else []
        categories: dict[str, int] = {}
        tags: dict[str, int] = {}
        for row in rows:
            key = row["category"] or "未分类"
            categories[key] = categories.get(key, 0) + 1
        for row in tag_rows:
            tags[row["tag"]] = int(row["n"])
        rank = lambda items: [{"name": name, "count": count} for name, count in
                              sorted(items.items(), key=lambda item: (-item[1], item[0]))]
        return {"categories": rank(categories), "tags": rank(tags), "total": len(rows)}

    def delete_conversation(self, conversation_id: str) -> dict:
        identifier(conversation_id)
        with self.connect() as db:
            self.write_lock(db)
            self._recover(db)
            if db.execute("SELECT 1 FROM {turn} WHERE conversation_id=? AND status='running'",
                          (conversation_id,)).fetchone():
                raise EvidenceError("会话仍有运行中的任务，请先取消并等待结束")
            if not db.execute("SELECT 1 FROM {conv} WHERE id=?", (conversation_id,)).fetchone():
                raise EvidenceError("会话不存在")
            db.execute("DELETE FROM {chk} WHERE observation_id IN "
                       "(SELECT id FROM {obs} WHERE turn_id IN (SELECT id FROM {turn} WHERE conversation_id=?))",
                       (conversation_id,))
            db.execute("DELETE FROM {obs} WHERE turn_id IN "
                       "(SELECT id FROM {turn} WHERE conversation_id=?)", (conversation_id,))
            db.execute("DELETE FROM {tag} WHERE conversation_id=?", (conversation_id,))
            db.execute("DELETE FROM {turn} WHERE conversation_id=?", (conversation_id,))
            db.execute("DELETE FROM {conv} WHERE id=?", (conversation_id,))
        return {"ok": True}

    def event(self, turn_id: str, message: str) -> None:
        with self.connect() as db:
            row = db.execute("SELECT events FROM {turn} WHERE id=? AND status='running'", (turn_id,)).fetchone()
            if row:
                events = json.loads(row["events"])
                if not events or events[-1]["message"] != message:
                    events.append({"at": time.time(), "message": message})
                db.execute("UPDATE {turn} SET events=?,updated=? WHERE id=?",
                           (canonical(events[-80:]), time.time(), turn_id))

    def finish(self, turn_id: str, *, result: dict | None = None, error: str | None = None) -> None:
        status = result["status"] if result else "failed"
        with self.connect() as db:
            db.execute("UPDATE {turn} SET status=?,result=?,error=?,updated=? WHERE id=? AND status='running'",
                       (status, canonical(result) if result else None, error, time.time(), turn_id))

    def cancel(self, turn_id: str) -> dict:
        identifier(turn_id)
        with self.connect() as db:
            db.execute("UPDATE {turn} SET status='cancelled',error='任务已取消',updated=? "
                       "WHERE id=? AND status='running'", (time.time(), turn_id))
        return self.turn(turn_id)
