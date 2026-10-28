# -*- coding: utf-8 -*-
"""复盘笔记与提醒的**路由外壳**回归：端点存在、`{"data":…}` 信封、错误码映射。

这一条文件刻意不碰数据库 —— 它只证明"`vr/app.py` 里加的那几条路由真的被
`server.py` 的合并逻辑收进去了，并且校验错误按 400、认不出的 id 按 404 交回"。
往用户那张真表里写测试行是另一回事（09-28 已经因为测试往 `_rejected/` 里写假拒稿
被坑过一次，那批文件此后被当成真实证据链引用）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import server
from duanxian import review_notes as rn

pytestmark = pytest.mark.unit

BASE = "http://127.0.0.1"     # 白名单只放行回环：别的 base_url 会整屏 403，看不出路由本身的问题


@pytest.fixture()
def client():
    return TestClient(server.app, base_url=BASE)


def test_五条端点都注册着(client):
    paths = {getattr(r, "path", "") for r in client.app.routes}
    for want in ("/api/review-note", "/api/review-notes", "/api/reminders",
                 "/api/reminders/due", "/api/reminders/{rid}/status"):
        assert want in paths, f"{want} 没被 server.py 的合并逻辑收到 —— 界面会拿到 404(not_wired)"


def test_笔记接口交出字段清单与草稿两段(monkeypatch, client):
    monkeypatch.setattr(rn, "day_bundle",
                        lambda date, refresh=False: {"available": True, "date": date,
                                                     "fields": [{"key": "env", "label": "大环境"}],
                                                     "note": None, "saved": False,
                                                     "defaults": {"fields": {}}, "expected_date": date})
    got = client.get("/api/review-note", params={"date": "2026-09-24"})
    assert got.status_code == 200
    body = got.json()
    assert set(body) == {"data"}, "顶层只许有 data 这一个键，界面的 probe 认这个信封"
    assert {"fields", "defaults", "saved", "expected_date"} <= set(body["data"])
    assert body["data"]["fields"][0]["label"] == "大环境"


def test_日期写错按400而不是502(monkeypatch, client):
    def boom(date, refresh=False):
        raise rn.NoteError("日期要写成 YYYY-MM-DD，收到的是「2026-9-8」")

    monkeypatch.setattr(rn, "day_bundle", boom)
    got = client.get("/api/review-note", params={"date": "2026-9-8"})
    assert got.status_code == 400 and "YYYY-MM-DD" in got.json()["detail"]


def test_保存失败点名不假定已生效(monkeypatch, client):
    monkeypatch.setattr(rn, "save_note",
                        lambda *a, **k: (_ for _ in ()).throw(rn.NoteError("这些字段本表没有：pice")))
    got = client.post("/api/review-note", json={"date": "2026-09-24", "values": {"pice": "1"}})
    assert got.status_code == 400 and "pice" in got.json()["detail"]


def test_提醒状态里认不出的id按404(monkeypatch, client):
    monkeypatch.setattr(rn, "set_reminder_status",
                        lambda rid, status: (_ for _ in ()).throw(KeyError(rid)))
    assert client.post("/api/reminders/999999/status", json={"status": "done"}).status_code == 404


def test_到期日预览用同一个后端函数而不是界面自己加天数(client):
    got = client.get("/api/reminders/due", params={"base_date": "2026-09-28", "sessions": 3})
    assert got.status_code == 200
    data = got.json()["data"]
    assert {"due_date", "calendar_covered", "explain"} <= set(data)
    assert data["due_date"] != "2026-10-01", "国庆是休市日，交易日口径下不该落在这里"


def test_提醒清单与时间轴形状可用(client):
    got = client.get("/api/reminders")
    assert got.status_code == 200
    data = got.json()["data"]
    assert data["available"] is True and {"rows", "next", "overdue", "calendar_horizon"} <= set(data)
