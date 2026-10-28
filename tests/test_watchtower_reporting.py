"""盯盘常驻循环的失败可见性（判据：docs/系统配置工作指南/06-自动执行机制自查表.md §1、§2）。

这一簇钉的是**坏了要出声、出声要可查、记账要有节奏**。原先 `_loop` 里是
`except Exception: pass`：盘中报价轮询整轮坏掉时，页面照样渲染上一轮的老快照，
日志和 `jobs/state.json` 一个字都没有 —— 用户只能把"页面正常"读成"盯盘正常"。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "vr") not in sys.path:
    sys.path.append(str(_ROOT / "vr"))      # 与 server.py 同一套路：`vr/` 是平铺模块目录
from vr import watchtower as wt             # noqa: E402


@pytest.fixture
def recorded(monkeypatch):
    """记账通道换成假记录器 —— 真的 `record()` 会写数据根目录下的 state.json。"""
    calls: list[tuple] = []

    def fake_record(name, *, ok, detail=None):
        calls.append((name, ok, dict(detail or {})))
        return {}
    monkeypatch.setattr(wt, "_jobs_module", SimpleNamespace(record=fake_record))
    return calls


@pytest.fixture
def state(monkeypatch):
    """模块级读数按测试开始时样子还原：这是一条常驻线程的共享状态。"""
    saved = (wt._snapshot, wt._loop_error, wt._loop_streak, wt._last_ok_at)
    monkeypatch.setattr(wt, "_loop_streak", 0)
    monkeypatch.setattr(wt, "_loop_error", None)
    yield wt
    wt._snapshot, wt._loop_error, wt._loop_streak, wt._last_ok_at = saved


@pytest.fixture
def sources(monkeypatch):
    """一轮要碰的五路外部源换成内存假数据（conftest 本来就拦出站网络）。"""
    quotes = {"600000": {"name": "测试票", "price": 11.0, "pct": 2.0, "amount": 1e8,
                         "quote_time": "", "zt_price": 12.1, "high": 11.5, "low": 10.5}}
    monkeypatch.setattr(wt, "_market_phase", lambda: "open")
    monkeypatch.setattr(wt, "_holdings", lambda: [{"code": "600000", "cost": 10.0, "shares": 100}])
    monkeypatch.setattr(wt, "_bigcaps", lambda: [{"code": "600000", "name": "测试票"}])
    monkeypatch.setattr(wt, "_previous_cohort", lambda: {
        "available": True, "quote_date": "2026-10-06", "sample_date": "2026-10-06",
        "stocks": [{"code": "600000", "name": "测试票", "boards": 3}]})
    monkeypatch.setattr(wt, "_turnover_yesterday", lambda: ("昨日成交前十", [{"code": "600000"}]))
    monkeypatch.setattr(wt, "_tencent_batch", lambda codes: dict(quotes))
    return quotes


def test_整轮失败经循环入口出声并记账(recorded, state, sources, monkeypatch):
    """走 `_loop` 里那道 except 本身（不是只测助手函数），并且线程不能被弄死。"""
    def broken(codes):
        raise RuntimeError("行情源挂了")
    monkeypatch.setattr(wt, "_tencent_batch", broken)
    state._snapshot = {}
    stop = RuntimeError("测试收尾：跑满一轮就停")
    monkeypatch.setattr(wt._wake, "wait", lambda _s: (_ for _ in ()).throw(stop))
    monkeypatch.setattr(wt._wake, "clear", lambda: None)
    with pytest.raises(RuntimeError, match="测试收尾"):
        wt._loop()
    assert wt._loop_error["reason"] == "RuntimeError：行情源挂了"
    assert wt._loop_error["consecutive_rounds"] == 1
    assert recorded and recorded[0][0] == "watchtower" and recorded[0][1] is False
    assert "行情源挂了" in recorded[0][2]["reason"]


def test_坏轮里旧快照要标旧并交出年龄(recorded, state, sources, monkeypatch):
    """第一轮好、第二轮坏：`_loop` 必须保留旧价格但明确标注它是旧账 + 为什么旧。"""
    good = wt._tencent_batch
    flag = {"broken": False}
    monkeypatch.setattr(wt, "_tencent_batch",
                        lambda codes: (_ for _ in ()).throw(RuntimeError("行情源挂了"))
                        if flag["broken"] else good(codes))
    state._snapshot = {}
    calls = {"n": 0}

    def wait(_seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            flag["broken"] = True          # 下一次进循环就坏
            return
        raise RuntimeError("测试收尾")
    monkeypatch.setattr(wt._wake, "wait", wait)
    monkeypatch.setattr(wt._wake, "clear", lambda: None)
    with pytest.raises(RuntimeError, match="测试收尾"):
        wt._loop()
    snap = wt.get_snapshot()
    assert snap["loop_error"]["reason"] == "RuntimeError：行情源挂了"
    assert snap["snapshot_stale"] is True          # 老价格还在，但标明是旧的
    assert snap["snapshot_age_seconds"] is not None
    assert snap["generated_at"]                    # 旧账的生成时刻交得出来
    assert snap["holdings"]                        # 上一轮的价格仍然保留，不清空


def test_恢复后刷新快照并说清此前连败(recorded, state, sources):
    state._snapshot = {}
    state._loop_streak = 3
    state._loop_error = {"reason": "旧的"}
    state._last_ok_at = 0.0
    wt._round()
    wt._note_success()
    snap = wt.get_snapshot()
    assert snap["generated_at"] and snap["loop_error"] is None
    assert snap["snapshot_age_seconds"] == 0
    assert [c[1] for c in recorded] == [True]
    assert "此前连续失败 3 轮" in recorded[0][2]["reason"]


def test_同一场故障按节奏记不是一轮一次(recorded, state, monkeypatch):
    """盘中 3 秒一轮：坏的第一轮立刻记，之后每 20 轮一次。每轮都写共享的
    state.json 会把体检的连败阈值直接冲过去，也把别人的记录挤成噪音。"""
    for _ in range(21):
        wt._note_failure(RuntimeError("源坏了"), "open")
    assert len(recorded) == 2
    assert "连续 1 轮" in recorded[0][2]["reason"]
    assert "连续 20 轮" in recorded[1][2]["reason"]


def test_健康时也要有心跳否则上次成功越走越旧(recorded, state):
    """2026-09-28 的活例：快讯循环不往 state.json 记，体检对着每 30 秒都在变大的
    jsonl 报了 30 小时假故障。健康侧也要留心跳。"""
    import time as clock
    state._last_ok_at = clock.monotonic() - wt.HEARTBEAT_SECONDS - 1
    wt._note_success()
    assert len(recorded) == 1 and recorded[0][2]["reason"] == "盯盘轮询心跳"
    wt._note_success()                             # 刚记过，心跳没到期不该再写
    assert len(recorded) == 1
    state._last_ok_at = clock.monotonic() - wt.HEARTBEAT_SECONDS - 1
    wt._note_success()
    assert len(recorded) == 2


def test_记账通道自己坏了不能把观测弄没(recorded, state, monkeypatch):
    def boom():
        raise ImportError("记账模块坏了")
    monkeypatch.setattr(wt, "_jobs", boom)
    wt._note_failure(RuntimeError("源坏了"), "open")        # 不许抛出来
    assert wt._loop_error["reason"] == "RuntimeError：源坏了"


def test_子源缺口走degraded不写成作业失败(recorded, state, sources, monkeypatch):
    """持仓读不到时整轮是好的。把它记成 fail 会让一条在跑的循环变红 ——
    那和原来的静默一样误导人，只是方向相反。"""
    state._snapshot = {}
    monkeypatch.setattr(wt, "_holdings", lambda: (_ for _ in ()).throw(RuntimeError("日志库坏了")))
    wt._round()
    snap = wt.get_snapshot()
    assert any("持仓读取失败" in text for text in snap["degraded"])
    assert "持仓读取失败" in snap["holdings_error"]
    assert recorded == []


def test_行情返回空算明说的缺口而不是今天没有(recorded, state, sources, monkeypatch):
    monkeypatch.setattr(wt, "_tencent_batch", lambda codes: {})
    state._snapshot = {}
    wt._round()
    snap = wt.get_snapshot()
    assert any("批量行情返回空" in text for text in snap["degraded"])


def test_从未成功过一轮也要说清为什么(recorded, state, monkeypatch):
    state._snapshot = {}
    state._loop_error = {"reason": "RuntimeError：行情源挂了", "consecutive_rounds": 7,
                         "at": "2026-10-06 14:00:00", "phase": "open"}
    snap = wt.get_snapshot()
    assert snap["warming_up"] is True
    assert snap["loop_error"]["consecutive_rounds"] == 7
    assert snap["generated_at"] is None
