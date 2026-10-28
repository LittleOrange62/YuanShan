"""会话元数据：分类可复现、改名与标签不破坏既有会话锁。"""
import json

import pytest

from review_agent import classify
from review_agent.classify import CATEGORIES, FALLBACK, category
from review_agent.evidence import EvidenceError
from review_agent.store import Store


def bundle(anchor="2026-09-03"):
    return lambda: {"anchor": anchor, "dates": [anchor], "revision": "r-" + anchor, "evidence": [], "targets": []}


SOURCE = {"provider": "api-compatible", "model": "m", "baseURL": "https://gw.example/v1"}


def started(tmp_path, question="今天涨停家数怎么样"):
    store = Store(tmp_path / "state")
    turn, created = store.start("2026-09-03", question, "a" * 32, SOURCE, bundle(), context={"allow_network": False, "symbol": "", "mode": "agent", "page": "首页"})
    store.finish(turn["id"], result={"status": "complete"})
    return store, turn["conversation_id"]


def add_turn(store, cid, question, request_id):
    turn, _ = store.start("2026-09-03", question, request_id, SOURCE, bundle(), conversation_id=cid)
    store.finish(turn["id"], result={"status": "complete"})
    return store.turn(turn["id"])


def test_categories_are_rule_based_and_stable():
    assert category("今天的涨停梯队和龙头怎么样") == "涨停与梯队"
    assert category("板块轮动和持续性如何") == "板块与题材"
    assert category("情绪进入退潮了吗") == "情绪与周期"
    assert category("你好") == FALLBACK
    assert category("问" * 5000) == FALLBACK
    names = [row[0] for row in CATEGORIES]
    for name, words in CATEGORIES:
        for word in words:
            # 关键字互相嵌套时（"连板高度"里含"连板"）由表内顺序定胜负，这正是要的可复现性。
            hit = category(word)
            assert hit == name or names.index(hit) < names.index(name), (word, hit)
    assert category("涨停板块哪个先动") == "板块与题材"  # 多条命中按表内顺序，唯一可复现


def test_new_conversation_is_categorised_from_the_first_question(tmp_path):
    store, cid = started(tmp_path, "板块轮动和持续性怎么看")
    row = store.conversation(cid)
    assert row["category"] == "板块与题材" and row["tags"] == []
    assert row["title"] == "板块轮动和持续性怎么看" and row["updated"] >= row["created"]


@pytest.mark.parametrize("title,expect", [
    ("我的机器人产业链复盘", "我的机器人产业链复盘"),
    ("  带空白  ", "带空白"),
    ("含\x00控制\n字符", "含控制字符"),
])
def test_rename_accepts_cleaned_text(tmp_path, title, expect):
    store, cid = started(tmp_path)
    assert store.rename(cid, title) == {"ok": True, "title": expect}
    assert store.conversation(cid)["title"] == expect


@pytest.mark.parametrize("title", ["", "   ", "\x00", "x" * 61])
def test_rename_rejects_empty_or_overlong(tmp_path, title):
    store, cid = started(tmp_path)
    with pytest.raises(EvidenceError):
        store.rename(cid, title)


def test_tags_dedupe_strip_and_limit(tmp_path):
    store, cid = started(tmp_path)
    out = store.retag(cid, [" 机器人 ", "机器人", "产业链", "#PCB"])
    assert out["tags"] == ["机器人", "产业链", "PCB"]
    assert store.conversation(cid)["tags"] == out["tags"]
    with pytest.raises(EvidenceError):
        store.retag(cid, ["x" * 17])
    with pytest.raises(EvidenceError):
        store.retag(cid, [str(i) for i in range(9)])
    assert store.retag(cid, [])["tags"] == []


def test_filters_search_and_facets_only_offer_real_values(tmp_path):
    store, first = started(tmp_path, "机器人产业链怎么看")
    store.retag(first, ["产业链"])
    add_turn(store, first, "今天涨停家数多少", "b" * 32)
    assert [r["title"] for r in store.list_conversations(category="板块与题材")] == ["机器人产业链怎么看"]
    assert [r["title"] for r in store.list_conversations(tag="产业链")] == ["机器人产业链怎么看"]
    assert [r["title"] for r in store.list_conversations(tag="其产业链他")] == []  # 标签整值匹配，不做子串
    assert [r["title"] for r in store.list_conversations(search="涨停家数")] == ["机器人产业链怎么看"]  # 命中轮次提问
    facets = store.facets()
    assert {"name": "板块与题材", "count": 1} in facets["categories"]
    assert facets["tags"] == [{"name": "产业链", "count": 1}] and facets["total"] == 1


def test_existing_session_locks_stay_intact(tmp_path):
    store, cid = started(tmp_path)
    store.rename(cid, "改了名")
    with pytest.raises(EvidenceError, match="来源"):
        store.start("2026-09-03", "换个来源", "c" * 32, {**SOURCE, "model": "other"}, bundle(), conversation_id=cid)
    with pytest.raises(EvidenceError, match="日期"):
        store.start("2026-09-04", "换日期", "d" * 32, SOURCE, bundle("2026-09-04"), conversation_id=cid)
    turn = add_turn(store, cid, "同一会话继续问", "e" * 32)
    assert store.conversation(cid)["title"] == "改了名", "改名不能影响既有会话的归属判定"
    assert turn["status"] == "complete"


def test_old_databases_gain_the_new_columns_without_inventing_categories(tmp_path):
    store = Store(tmp_path / "state")
    old = {"anchor": "2026-09-03", "dates": ["2026-09-03"], "revision": "old", "evidence": []}
    with store.connect() as db:
        db.execute("DROP TABLE conversations")
        db.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY, anchor TEXT NOT NULL, title TEXT NOT NULL,"
                   " source TEXT NOT NULL, bundle TEXT NOT NULL, created REAL NOT NULL)")
        db.execute("INSERT INTO conversations VALUES (?,?,?,?,?,1)", ("f0" + "1" * 30, "2026-09-03", "旧会话", "{}", json.dumps(old)))
    Store(tmp_path / "state")  # 重新打开即迁移
    row = store.conversation("f0" + "1" * 30)
    assert row["category"] == "" and row["tags"] == [] and row["updated"] == 0
    assert store.facets()["categories"] == [{"name": "未分类", "count": 1}]
