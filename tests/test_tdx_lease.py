# -*- coding: utf-8 -*-
"""收尾租约：程序自己拉起的通达信实例，拉起后 N 分钟到点就收（用户 2026-11-12 点名）。

钉的是三件事：
1. 判据本身（`duanxian/tdx_lease.verdict`，纯函数，不打桩）——九档出口逐档钉；
2. 记账只发生在**真起了新实例**那一刻（复用已在跑的不刷新计时，否则永远到不了点）；
3. 到点真收时走的是 `_close_tdx_client()` 那一个温和退出出口，且让路闸成立时**一次都不收**。

conftest 已把 ASTOCK_DATA_HOME 指到临时目录，所以这里的落盘不碰用户真账本。
"""

from __future__ import annotations

import datetime as dt

import pytest

from duanxian import tdx_lease as L

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 11, 12, 10, 0, tzinfo=TZ)


def _lease(minutes_ago: float, *, pid=71124, status="open", deferred=None):
    born = NOW - dt.timedelta(minutes=minutes_ago)
    return {"pid": pid, "launched_at": born.isoformat(timespec="seconds"),
            "day": born.strftime("%Y-%m-%d"), "why": "收盘链 ③c", "status": status,
            "deferred": deferred or [], "closed_at": None, "close_reason": None}


def _v(lease, live, **kw):
    args = dict(now=NOW, live=live, after_min=30, max_defer_min=120)
    args.update(kw)
    return L.verdict(lease, **args)


@pytest.fixture(autouse=True)
def _clean_lease():
    """账本是落盘的，而 ASTOCK_DATA_HOME 是整轮会话一份 ⇒ 每条用例前清干净。

    不清的话「没账本」那几条会读到上一条用例留下的账本，红的是测试自己。
    """
    import os
    try:
        os.remove(L.lease_path())
    except OSError:
        pass
    yield


# ---------------------------------------------------------------- ① 判据九档（纯函数）

def test_未到点不收():
    act, why = _v(_lease(10), [71124])
    assert act == "wait"
    assert "20.0 分钟后到点" in why


def test_到点该收():
    act, why = _v(_lease(40), [71124])
    assert act == "close"
    assert "71124" in why and "已到点" in why


def test_恰好到点那一分钟也算到点():
    assert _v(_lease(30), [71124])[0] == "close"


def test_pid对不上就作废_绝不收别人的实例():
    act, why = _v(_lease(40), [999])
    assert act == "stale"
    assert "999" in why and "71124" in why


def test_没有活实例时清账本():
    assert _v(_lease(40), [])[0] == "gone"


def test_没账本就不动手():
    act, why = _v(None, [71124])
    assert act == "none"
    assert "不是程序拉起的" in why


def test_keep_alive档豁免():
    act, why = _v(_lease(40), [71124], mode="keep_alive")
    assert act == "off"
    assert "keep_alive" in why


def test_开关关着就不收():
    assert _v(_lease(40), [71124], enabled=False)[0] == "off"


def test_顺延用满上限就停手交人工():
    act, why = _v(_lease(200), [71124])
    assert act == "expired"
    assert "120" in why


def test_顺延没用满还在到点档():
    assert _v(_lease(100), [71124])[0] == "close"


def test_已经收过或作废过就不重复动手():
    for st in ("closed", "cleared", "expired"):
        assert _v(_lease(40, status=st), [71124])[0] == "done"


def test_拉起时刻读不出来时判不了而不是该关():
    bad = _lease(40)
    bad["launched_at"] = "不是时间"
    assert _v(bad, [71124])[0] == "wait"


def test_还没确认到pid时不动手():
    assert _v(_lease(5, pid=None), [71124])[0] == "wait"


def test_超过认领窗口还没pid就作废_不许永远停在wait():
    act, why = _v(_lease(40, pid=None), [71124])
    assert act == "stale"
    assert "没确认到 pid" in why


def test_改分钟数判据跟着变():
    assert _v(_lease(40), [71124], after_min=60)[0] == "wait"
    assert _v(_lease(10), [71124], after_min=5)[0] == "close"


# ---------------------------------------------------------------- ② 落盘与认领

def test_记账落盘且读得回():
    L.mark_launched(why="测试拉起", now=NOW)
    got = L.read()
    assert got and got["pid"] is None and got["why"] == "测试拉起"
    assert got["launched_at"] == NOW.isoformat(timespec="seconds")


def test_认领pid只补一次():
    L.mark_launched(now=NOW)
    L.attach_pid(71124, now=NOW)
    L.attach_pid(999, now=NOW)
    assert L.read()["pid"] == 71124


def test_太老的账本不许认领现在活着的实例():
    L.mark_launched(now=NOW - dt.timedelta(minutes=30))
    L.attach_pid(999, now=NOW)
    assert L.read()["pid"] is None, '老账本认到的很可能是人自己开的那一个'


def test_重新拉起会重置计时与pid():
    L.mark_launched(now=NOW - dt.timedelta(minutes=50))
    L.attach_pid(111, now=NOW - dt.timedelta(minutes=50))
    L.mark_launched(now=NOW)
    got = L.read()
    assert got["pid"] is None
    assert got["launched_at"] == NOW.isoformat(timespec="seconds")


def test_顺延逐次累加():
    L.mark_launched(now=NOW)
    L.defer("链在跑", now=NOW)
    L.defer("人在用键鼠", now=NOW)
    got = L.read()
    assert len(got["deferred"]) == 2
    assert got["status"] == "deferred"
    assert got["deferred"][-1]["why"] == "人在用键鼠"


def test_收掉之后留痕():
    L.mark_launched(now=NOW)
    L.attach_pid(71124, now=NOW)
    L.mark_closed(reason="已请客户端退出", now=NOW)
    got = L.read()
    assert got["status"] == "closed" and got["closed_at"]
    assert got["close_reason"] == "已请客户端退出"


def test_没账本时读回None而不是编一份():
    assert L.read() is None
    assert L.attach_pid(1, now=NOW) is None
    assert L.defer("x", now=NOW) is None
    assert L.mark_closed(now=NOW) is None


# ---------------------------------------------------------------- ③ 交给屏幕的读数

def test_没账本那一格说清不是程序开的():
    f = L.facts(None, now=NOW)
    assert f["available"] is False
    assert "不是程序拉起的" in f["reason"]
    assert "left_min" not in f


def test_读数交出还差几分钟与顺延次数():
    f = L.facts(_lease(10, deferred=[{"at": "x", "why": "链在跑"}]), now=NOW, after_min=30)
    assert f["available"] is True
    assert f["left_min"] == 20.0 and f["age_min"] == 10.0
    assert f["deferred_count"] == 1
    assert f["due_at"] == (NOW + dt.timedelta(minutes=20)).isoformat(timespec="seconds")


# ---------------------------------------------------------------- ④ 到点那一手（server）

@pytest.fixture
def wired(monkeypatch):
    """把 server 那一手的出口全换成可观察的桩，并钉住时钟。

    `_close_tdx_client` 换成桩是必须的：真那一份会发窗口消息。
    桩的返回形状与真出口一致（acted/closed/reason），不然钉的是另一件事。
    """
    import server
    from duanxian import jobs as jstore
    from duanxian import util as dutil
    from app.tdx_client import win32

    calls: list[str] = []
    cfg = {"client_mode": "on_demand", "lease_close_enabled": True,
           "lease_close_after_min": 30, "lease_max_defer_min": 120}
    monkeypatch.setattr(jstore, "settings", lambda name, defaults: (dict(cfg), []))
    monkeypatch.setattr(jstore, "stage_busy", lambda: None)
    monkeypatch.setattr(jstore, "record",
                        lambda name, **kw: calls.append("record:%s:%s" % (name, kw.get("ok"))) or {})
    monkeypatch.setattr(server, "_gui", lambda *a, **kw: calls.append("gui:%s" % (a[2] if len(a) > 2 else "?")))
    monkeypatch.setattr(win32, "input_idle_sec", lambda: 3600)
    monkeypatch.setattr(dutil, "china_now", lambda: NOW)
    monkeypatch.setattr(dutil, "china_today", lambda: NOW.strftime("%Y-%m-%d"))
    monkeypatch.setattr(L, "china_now", lambda: NOW)

    def fake_close(**kw):
        calls.append("close")
        return {"acted": True, "closed": True, "reason": "已请客户端退出（pid 71124），重探确实不在了"}

    monkeypatch.setattr(server, "_close_tdx_client", fake_close)
    return {"calls": calls, "cfg": cfg, "server": server,
            "detail": {"pid": 71124, "live": [71124], "running": True}}


def test_到点且没人在用就走同一个温和退出出口(wired):
    L.mark_launched(now=NOW - dt.timedelta(minutes=40))
    L.attach_pid(71124, now=NOW - dt.timedelta(minutes=40))
    out = wired["server"]._tdx_close_lease_round(wired["detail"])
    assert out["action"] == "closed"
    assert "close" in wired["calls"], '到点了却没走那个唯一的温和退出出口'
    assert L.read()["status"] == "closed"


def test_链在跑就顺延_一次都不收(wired, monkeypatch):
    from duanxian import jobs as jstore
    monkeypatch.setattr(jstore, "stage_busy", lambda: "daily_close")
    L.mark_launched(now=NOW - dt.timedelta(minutes=40))
    L.attach_pid(71124, now=NOW - dt.timedelta(minutes=40))
    out = wired["server"]._tdx_close_lease_round(wired["detail"])
    assert out["action"] == "defer"
    assert "close" not in wired["calls"]
    assert L.read()["deferred"][-1]["why"].startswith("收盘链")


def test_人在动键鼠就顺延_一次都不收(wired, monkeypatch):
    from app.tdx_client import win32
    monkeypatch.setattr(win32, "input_idle_sec", lambda: 30)
    L.mark_launched(now=NOW - dt.timedelta(minutes=40))
    L.attach_pid(71124, now=NOW - dt.timedelta(minutes=40))
    out = wired["server"]._tdx_close_lease_round(wired["detail"])
    assert out["action"] == "defer"
    assert "close" not in wired["calls"]


def test_键鼠空闲判不了也保守不收(wired, monkeypatch):
    from app.tdx_client import win32
    monkeypatch.setattr(win32, "input_idle_sec", lambda: None)
    L.mark_launched(now=NOW - dt.timedelta(minutes=40))
    L.attach_pid(71124, now=NOW - dt.timedelta(minutes=40))
    assert wired["server"]._tdx_close_lease_round(wired["detail"])["action"] == "defer"
    assert "close" not in wired["calls"]


def test_没到点不收(wired):
    L.mark_launched(now=NOW - dt.timedelta(minutes=5))
    L.attach_pid(71124, now=NOW - dt.timedelta(minutes=5))
    out = wired["server"]._tdx_close_lease_round(wired["detail"])
    assert out["action"] == "wait"
    assert "close" not in wired["calls"]


def test_keep_alive档不收(wired):
    wired["cfg"]["client_mode"] = "keep_alive"
    L.mark_launched(now=NOW - dt.timedelta(minutes=40))
    L.attach_pid(71124, now=NOW - dt.timedelta(minutes=40))
    assert wired["server"]._tdx_close_lease_round(wired["detail"])["action"] == "off"
    assert "close" not in wired["calls"]


def test_开关关着不收(wired):
    wired["cfg"]["lease_close_enabled"] = False
    L.mark_launched(now=NOW - dt.timedelta(minutes=40))
    L.attach_pid(71124, now=NOW - dt.timedelta(minutes=40))
    assert wired["server"]._tdx_close_lease_round(wired["detail"])["action"] == "off"
    assert "close" not in wired["calls"]


def test_人重启过换了实例就不收_并作废账本(wired):
    L.mark_launched(now=NOW - dt.timedelta(minutes=40))
    L.attach_pid(71124, now=NOW - dt.timedelta(minutes=40))
    out = wired["server"]._tdx_close_lease_round({"pid": 999, "live": [999], "running": True})
    assert out["action"] == "stale"
    assert "close" not in wired["calls"]
    assert L.read()["status"] == "cleared"


def test_温和退出没关掉就顺延而不是报成已关(wired, monkeypatch):
    monkeypatch.setattr(wired["server"], "_close_tdx_client",
                        lambda **kw: {"acted": False, "closed": False,
                                      "reason": "TPyth 还在同步 → 不收"})
    L.mark_launched(now=NOW - dt.timedelta(minutes=40))
    L.attach_pid(71124, now=NOW - dt.timedelta(minutes=40))
    out = wired["server"]._tdx_close_lease_round(wired["detail"])
    assert out["action"] == "defer"
    assert L.read()["status"] == "deferred"
    assert "还在同步" in L.read()["deferred"][-1]["why"]


def test_账本缺pid时趁年轻拿这一轮现探的补上(wired):
    L.mark_launched(now=NOW - dt.timedelta(minutes=5))
    assert L.read()["pid"] is None
    out = wired["server"]._tdx_close_lease_round(wired["detail"])
    assert out["action"] == "wait"
    assert L.read()["pid"] == 71124
    assert "close" not in wired["calls"], "还没到点就收 = 判据坏了"


def test_老账本一直没pid就作废_不认领别人的实例(wired):
    L.mark_launched(now=NOW - dt.timedelta(minutes=40))
    out = wired["server"]._tdx_close_lease_round(wired["detail"])
    assert out["action"] == "stale"
    assert "close" not in wired["calls"]
    got = L.read()
    assert got["status"] == "cleared" and got["pid"] is None


# ---------------------------------------------------------------- ⑤ 记账点只在真拉起那一刻

def test_s1真起了新实例才记账(monkeypatch, tmp_path):
    from app.tdx_client import win32
    from app.tdx_client.steps import s1_launch

    exe = tmp_path / "TdxW.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(s1_launch, "exe_path", lambda: exe)
    monkeypatch.setattr(s1_launch.time, "sleep", lambda _s: None)
    monkeypatch.setattr(win32, "split_live", lambda n: ([], []))
    monkeypatch.setattr(s1_launch.subprocess, "Popen", lambda argv, **kw: None)
    monkeypatch.setenv("FT_TDX_LAUNCH_WHY", "收盘链 ③c 盘后数据下载")

    class _Ctx:
        bag: dict = {}
        lines: list = []

        def log(self, msg):
            self.lines.append(msg)

    res = s1_launch.LaunchStep().act(_Ctx())
    assert res.ok, res.message
    got = L.read()
    assert got is not None, "真起了新实例却没记账 ⇒ 到点没人收"
    assert got["why"] == "收盘链 ③c 盘后数据下载"


def test_s1复用已在跑的客户端不刷新计时(monkeypatch):
    from app.tdx_client import win32
    from app.tdx_client.steps import s1_launch

    L.mark_launched(now=NOW - dt.timedelta(minutes=20))
    L.attach_pid(71124, now=NOW - dt.timedelta(minutes=20))
    before = L.read()["launched_at"]

    class _Win:
        pid = 71124
        text = "通达信金融终端V7.73"

    class _Ctx:
        bag: dict = {}

        def refresh_main(self):
            return _Win()

    monkeypatch.setattr(s1_launch, "_login_box", lambda pid=None: None)
    ok, _why = s1_launch.LaunchStep().precheck(_Ctx())
    assert ok is True
    assert L.read()["launched_at"] == before, "复用一轮就刷新计时 ⇒ 永远到不了点"


def test_s1复验确认到pid才认领(monkeypatch):
    from app.tdx_client import win32
    from app.tdx_client.steps import s1_launch

    monkeypatch.setattr(L, "china_now", lambda: NOW)
    L.mark_launched(now=NOW)

    class _Win:
        pid = 71124
        text = "通达信金融终端V7.73"

    class _Ctx:
        bag: dict = {}

        def refresh_main(self):
            return _Win()

    monkeypatch.setattr(win32, "split_live", lambda n: ([71124], []))
    monkeypatch.setattr(s1_launch, "_login_box", lambda pid=None: None)
    monkeypatch.setattr(s1_launch.LaunchStep, "wait_until", lambda self, fn, **kw: fn())
    ok, _why = s1_launch.LaunchStep().verify(_Ctx())
    assert ok is True
    assert L.read()["pid"] == 71124