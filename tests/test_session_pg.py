"""PG 后端的真库测试 —— 会话已经不再写 SQLite，这一段不能只靠 SQLite 那条路绿。

只在 `PG_ETL_USER/PG_ETL_PASSWORD` 连得上时跑；连不上就 skip 并说明原因（全新机器、
离线 CI 都是合法状态）。测试只认自己那个 `page`（"pg自检"），并且在结束时删掉自己造的
每一场会话 —— 库里那一条真实历史不许被测试碰。
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

from review_agent import session_backend
from review_agent.evidence import EvidenceError
from review_agent.followup import Followup
from review_agent.store import Store

PAGE = "pg自检"
ANCHOR = "2026-09-24"


def _probe() -> str | None:
    """能连上就返回 None，连不上返回 skip 原因。"""
    if not session_backend.pg_configured():
        return ".env 里没有 PG_ETL_USER/PG_ETL_PASSWORD"
    try:
        backend = session_backend.build(Path("."), "pg")
        backend.ensure_schema()
        with backend.connect() as db:
            db.execute("SELECT 1 AS one")
    except Exception as exc:
        return f"连不上 PostgreSQL 或建表失败：{type(exc).__name__}: {str(exc)[:120]}"
    return None


@pytest.fixture(scope="module")
def store():
    reason = _probe()
    if reason:
        pytest.skip(reason)
    made: list[str] = []
    handle = Store(Path("."), backend="pg")

    class Tracked(Store):
        def start(self, anchor, question, request_id, source, bundle_factory,
                  conversation_id=None, context=None):
            turn, created = super().start(anchor, question, request_id, source, bundle_factory,
                                          conversation_id, context)
            if created:
                made.append(turn["conversation_id"])
            return turn, created

    tracked = Tracked(Path("."), backend="pg")
    yield tracked
    # 清场只清自己造的。running 的轮次先取消，否则 delete 会按"还有任务在跑"拒绝。
    for conversation_id in made:
        try:
            for item in tracked.conversation(conversation_id)["turns"]:
                if item["status"] == "running":
                    tracked.cancel(item["id"])
            tracked.delete_conversation(conversation_id)
        except EvidenceError:
            pass


def bundle(context_mode="agent"):
    def build():
        return {"revision": "r-" + uuid.uuid4().hex[:8], "dates": [ANCHOR], "targets": [],
                "context": {"mode": context_mode, "page": PAGE, "allow_network": False, "symbol": ""}}
    return build


def new_turn(store, question="今天涨停家数怎么读？", mode="agent", keep_running=False):
    """默认当场收尾：库里"只许一个 Agent 任务在跑"是全局的，留着 running 会堵死后面每条测试。"""
    turn, created = store.start(ANCHOR, question, uuid.uuid4().hex,
                                {"provider": "new-api", "model": "m", "baseURL": "http://x/v1"},
                                bundle(mode), context={"mode": mode, "page": PAGE,
                                                       "allow_network": False, "symbol": ""})
    if not keep_running:
        store.finish(turn["id"], result={"status": "complete", "text": "自检收尾",
                                         "findings": [], "gaps": [], "evidence": []})
        turn["status"] = "complete"
    return turn, created


def test_pg_store_reports_its_backend(store):
    assert store.name == "pg"
    # PG 模式没有本机会话文件；root 仍然要存在（backend.lock、runs/ 都住在那儿）
    assert store.path is None
    assert store.root.exists()


def test_start_finish_and_read_back(store):
    turn, created = new_turn(store, keep_running=True)
    assert created and turn["status"] == "running"
    store.event(turn["id"], "正在核对证据")
    store.finish(turn["id"], result={"status": "complete", "text": "读数如下",
                                     "findings": [], "gaps": [], "evidence": []})
    again = store.turn(turn["id"])
    assert again["status"] == "complete"
    assert again["result"]["text"] == "读数如下"
    assert again["events"][-1]["message"] == "正在核对证据"
    assert "fingerprint" not in again


def test_request_id_is_idempotent_on_pg(store):
    request_id = uuid.uuid4().hex
    source = {"provider": "new-api", "model": "m", "baseURL": "http://x/v1"}
    first, created = store.start(ANCHOR, "同一个请求编号不该跑两遍", request_id, source,
                                 bundle(), context={"mode": "agent", "page": PAGE,
                                                    "allow_network": False, "symbol": ""})
    second, again = store.start(ANCHOR, "同一个请求编号不该跑两遍", request_id, source,
                                bundle(), context={"mode": "agent", "page": PAGE,
                                                   "allow_network": False, "symbol": ""})
    assert created and not again and first["id"] == second["id"]
    # 这条不走 new_turn（它要的是"同一个编号提交两次"），所以自己收尾，别把 running 留给后面。
    store.finish(first["id"], result={"status": "complete", "text": "自检收尾",
                                      "findings": [], "gaps": [], "evidence": []})


def test_tags_round_trip_keeps_order_and_filters(store):
    turn, _ = new_turn(store)
    cid = turn["conversation_id"]
    store.retag(cid, ["产业链", "打板", "盘后"])
    conversation = store.conversation(cid)
    assert conversation["tags"] == ["产业链", "打板", "盘后"]
    found = store.list_conversations(mode="agent", page=PAGE, tag="打板")
    assert [c["id"] for c in found] == [cid]
    # 整值匹配：查 "板" 不该命中 "打板"
    assert store.list_conversations(mode="agent", page=PAGE, tag="板") == []
    store.retag(cid, ["盘后"])
    assert store.conversation(cid)["tags"] == ["盘后"]


def test_facets_counts_only_this_page_scope(store):
    turn, _ = new_turn(store)
    cid = turn["conversation_id"]
    store.retag(cid, ["自检标签"])
    store.recategorize(cid, "市场情绪")
    facets = store.facets(mode="agent", page=PAGE)
    assert facets["total"] >= 1
    assert {"name": "市场情绪", "count": 1} in facets["categories"]
    assert {"name": "自检标签", "count": 1} in facets["tags"]
    assert facets["total"] == len(store.list_conversations(mode="agent", page=PAGE, limit=200))


def test_direct_and_agent_sessions_do_not_mix(store):
    agent_turn, _ = new_turn(store, question="普通对话不该混进证据会话")
    direct_turn, _ = new_turn(store, question="普通对话不该混进证据会话", mode="direct")
    assert agent_turn["conversation_id"] != direct_turn["conversation_id"]
    agent_ids = {c["id"] for c in store.list_conversations(mode="agent", page=PAGE)}
    direct_ids = {c["id"] for c in store.list_conversations(mode="direct", page=PAGE)}
    assert agent_turn["conversation_id"] in agent_ids and agent_turn["conversation_id"] not in direct_ids
    assert direct_turn["conversation_id"] in direct_ids and direct_turn["conversation_id"] not in agent_ids


def test_search_hits_title_and_question(store):
    turn, _ = new_turn(store, question="这条只有问题里有needle关键词")
    cid = turn["conversation_id"]
    assert [c["id"] for c in store.list_conversations(mode="agent", page=PAGE, search="needle关键词")] == [cid]
    store.rename(cid, "标题里有needleneedle")
    assert cid in {c["id"] for c in store.list_conversations(mode="agent", page=PAGE, search="needleneedle")}
    # 百分号与下划线会被剥掉：搜索词是字面量，不是模式
    assert store.list_conversations(mode="agent", page=PAGE, search="100%_") == []


def test_delete_takes_turns_tags_and_observations_with_it(store):
    turn, _ = new_turn(store, question="删会话要把核验条件一起带走", keep_running=True)
    cid = turn["conversation_id"]
    store.finish(turn["id"], result={"status": "complete", "text": "x", "findings": [], "gaps": [],
                                     "evidence": [{"kind": "metric", "metric": "limit_up_count",
                                                   "date": ANCHOR, "available": True, "value": 42.0,
                                                   "id": "e1", "title": "涨停家数", "scope": "s",
                                                   "citations": []}]})
    followup = Followup(store, Path("evals"))
    item = followup.add(turn["id"], "limit_up_count", "上升")
    store.retag(cid, ["待删"])
    assert followup.list(cid)
    assert store.delete_conversation(cid)["ok"]
    assert store.list_conversations(mode="agent", page=PAGE, tag="待删") == []
    # 会话没了，核验条件也不该留壳：按会话列举要先读会话，读不到就明确报错，不返回"看起来是空"。
    with pytest.raises(EvidenceError):
        followup.list(cid)
    with pytest.raises(EvidenceError):
        followup.get(item["id"])


def test_running_turn_blocks_a_second_one_and_cancel_frees_it(store):
    first, _ = new_turn(store, question="全局只许一个 Agent 任务在跑", keep_running=True)
    second_request = uuid.uuid4().hex
    with pytest.raises(EvidenceError, match="已有 Agent 任务在运行"):
        store.start(ANCHOR, "第二个问题", second_request,
                    {"provider": "new-api", "model": "m", "baseURL": "http://x/v1"},
                    bundle(), context={"mode": "agent", "page": PAGE,
                                       "allow_network": False, "symbol": ""})
    cancelled = store.cancel(first["id"])
    assert cancelled["status"] == "cancelled"
    third, created = new_turn(store, question="取消之后可以再来")
    assert created and third["status"] == "complete"
    assert store.conversation(third["conversation_id"])["turns"][0]["status"] == "complete"


def test_conversation_locks_are_per_thread(store):
    """每线程一条长连接：两条线程各自提交，不该把彼此的事务搅在一起。"""
    import threading

    results: list = []

    def worker(question: str):
        try:
            turn, created = store.start(ANCHOR, question, uuid.uuid4().hex,
                                        {"provider": "new-api", "model": "m", "baseURL": "http://x/v1"},
                                        bundle(), context={"mode": "agent", "page": PAGE,
                                                           "allow_network": False, "symbol": ""})
            results.append((turn["conversation_id"], created))
        except Exception as exc:          # 全局单任务锁本来就是"一次只许一个"
            results.append(exc)

    threads = [threading.Thread(target=worker, args=(f"线程问题 {i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
        thread.join(timeout=30)
    # 允许一成一败（第二个撞上"已有任务在运行"），但不许出现连接层报错
    assert len(results) == 2
    assert not any(isinstance(r, (RuntimeError, OSError, ValueError)) and
                   "connect" in str(r).lower() for r in results)
    for item in results:
        if isinstance(item, tuple):
            store.finish(store.conversation(item[0])["turns"][0]["id"], error="自检收尾")


def test_backend_selection_is_explicit(tmp_path):
    assert session_backend.build(tmp_path, "sqlite").name == "sqlite"
    with pytest.raises(EvidenceError):
        session_backend.build(tmp_path, "oracle")


def test_migration_script_columns_match_the_tables(store):
    """搬运脚本的列名必须真在目标表里 —— 这类漂移只有搬家当天才会炸，提前查。"""
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "scripts" / "migrate_sessions_to_pg.py"
    spec = importlib.util.spec_from_file_location("migrate_sessions_to_pg", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with store.connect() as db:
        for _source, key, columns, _values in module.SHIPS:
            have = store.backend.columns(db, key)
            missing = [c for c in columns if c not in have]
            assert not missing, f"{store.backend.tables[key]} 缺列：{missing}"
        assert store.backend.columns(db, "tag") >= {"conversation_id", "tag", "position", "created"}


def test_migration_shapes_rows_without_inventing_values(store, tmp_path, monkeypatch):
    """搬家的三条口径：旧分类不回填、updated=0 退回 created、tags JSON 拆成带顺序的关联行。"""
    import importlib.util
    import sqlite3

    path = Path(__file__).resolve().parent.parent / "scripts" / "migrate_sessions_to_pg.py"
    spec = importlib.util.spec_from_file_location("migrate_sessions_to_pg", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = tmp_path / "conversations.sqlite3"
    db = sqlite3.connect(source)
    db.executescript("""
        CREATE TABLE conversations (id TEXT PRIMARY KEY, anchor TEXT, title TEXT, source TEXT,
            bundle TEXT, created REAL, category TEXT, tags TEXT, updated REAL);
        CREATE TABLE turns (id TEXT PRIMARY KEY, conversation_id TEXT, request_id TEXT,
            fingerprint TEXT, question TEXT, status TEXT, result TEXT, error TEXT,
            events TEXT, created REAL, updated REAL);
        CREATE TABLE observations (id TEXT PRIMARY KEY, turn_id TEXT, metric TEXT, direction TEXT,
            baseline TEXT, created REAL, rule TEXT);
        CREATE TABLE observation_checks (id TEXT PRIMARY KEY, observation_id TEXT, result TEXT, created REAL);
    """)
    conversation = "c" * 32
    bundle = '{"revision":"r-mig","dates":[],"context":{"mode":"direct","page":"迁移自检"}}'
    source_json = '{"baseURL":"http://x/v1","model":"m","provider":"new-api"}'
    db.execute("INSERT INTO conversations VALUES (?,?, '旧标题', ?, ?, 100.0, '', '[\"盘后\",\"打板\"]', 0)",
               (conversation, ANCHOR, source_json, bundle))
    db.execute("INSERT INTO turns VALUES (?,?,'req','fp','问题','complete',NULL,NULL,'[]',101.0,102.0)",
               ("d" * 32, conversation))
    db.commit()
    db.close()

    monkeypatch.setattr(module, "SRC", source)
    data = module.read_source()
    shipped = {key: [values(row, 0.0) for row in data[name]] for name, key, columns, values in module.SHIPS}
    row = dict(zip(module.SHIPS[0][2], shipped["conv"][0]))
    assert row["category"] == ""                     # 不猜分类
    assert row["updated"] == 100.0                   # updated=0 → 退回 created，别被排序压到最后
    assert module.tag_rows(data["_legacy_tags"]) == [(conversation, "盘后", 0, 100.0),
                                                     (conversation, "打板", 1, 100.0)]

    # 真搬进 PG 再清掉：验证列与类型吃得下，不是只在 Python 里自洽。
    from compute import db as pgdb

    conn = pgdb.connect(readonly=False)
    try:
        with conn.cursor() as cur:
            inserted = module.insert(cur, module.T["conv"], module.SHIPS[0][2], [shipped["conv"][0]])
            inserted += module.insert(cur, module.T["turn"], module.SHIPS[1][2], [shipped["turn"][0]])
            inserted += module.insert(cur, module.T["tag"], ("conversation_id", "tag", "position", "created"),
                                      module.tag_rows(data["_legacy_tags"]))
            twice = module.insert(cur, module.T["conv"], module.SHIPS[0][2], [shipped["conv"][0]])
        conn.commit()
        assert inserted == 4 and twice == 0          # 重跑不翻倍
        loaded = store.conversation(conversation)
        assert loaded["tags"] == ["盘后", "打板"]     # 关联表读回来还是原顺序
        assert loaded["category"] == "" and loaded["revision"] == "r-mig"
        assert loaded["context"]["page"] == "迁移自检"
    finally:
        store.delete_conversation(conversation)
        conn.close()
