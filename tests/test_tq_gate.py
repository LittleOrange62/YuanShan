"""TQ 串行闸（`app/datasource/tq_gate.py`）。

为什么值得单测：并发打 TQ 的失败方式**不是报错，是返回空/少数据**（单连接），
事后完全看不出差别 —— c18 少几只成分、c19 漏几条除权，看起来都像"本来就没有"。
所以"必须串行"和"陈旧锁能接管"这两条一旦回归，事故是静默的。
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from app.datasource import tq_gate


@pytest.fixture(autouse=True)
def _tmp_locks(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setattr(tq_gate, "STALE_SECONDS", 0.2)


def _hold_seconds(secs, sink, name):
    with tq_gate.hold(who=name):
        sink.append((name, "in"))
        time.sleep(secs)
        sink.append((name, "out"))


def test_两线程同抢时严格串行不重叠():
    sink = []
    a = threading.Thread(target=_hold_seconds, args=(0.25, sink, "a"))
    b = threading.Thread(target=_hold_seconds, args=(0.05, sink, "b"))
    a.start(); time.sleep(0.02); b.start()
    a.join(timeout=5); b.join(timeout=5)
    assert sink == [("a", "in"), ("a", "out"), ("b", "in"), ("b", "out")]   # b 必须等 a 出去


def test_同线程可重入_嵌套调用不自锁死():
    with tq_gate.hold(who="outer"):
        with tq_gate.hold(who="inner"):
            assert tq_gate.status()["held"] is True


def test_等不到锁要抛异常而不是静默跳过():
    # 必须是**另一个线程**占着：同线程嵌套走的是可重入短路（上一条测试），
    # 拿它来验超时永远验不到 —— 文件锁的超时只在真的被别人占着时才发生。
    busy = threading.Event()
    stop = threading.Event()

    def holder_thread():
        with tq_gate.hold(who="占用者"):
            busy.set()
            stop.wait(5)

    t = threading.Thread(target=holder_thread)
    t.start()
    try:
        assert busy.wait(5), "占用线程没拿到锁"
        with pytest.raises(tq_gate.TqBusy) as e:
            with tq_gate.hold(who="受害者", timeout=0.3):
                pass                       # 不该走到这里
        assert "TQ 被占着" in str(e.value)
    finally:
        stop.set()
        t.join(timeout=5)


def test_别的进程占着时同样超时抛错(monkeypatch):
    # 真实事故形状：常驻 uvicorn 的采集线程 vs 人手跑的 `python -m ingest.jobs.c18_*`
    # 是两个进程，进程内锁互看不见，只有文件锁拦得住 —— 只测上一条会漏掉这个。
    monkeypatch.setattr(tq_gate, "STALE_SECONDS", 900)     # 别当陈旧锁抢走
    monkeypatch.setattr(tq_gate, "_pid_alive", lambda pid: True)
    p = tq_gate.lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("88888 %s\n" % time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    with pytest.raises(tq_gate.TqBusy) as e:
        with tq_gate.hold(who="c18", timeout=0.6):
            pass
    assert "别的进程持有" in str(e.value) and "88888" in str(e.value)
    assert p.exists()                                      # 抢不动别人的锁，也不许删


def test_进程崩了留下的陈旧锁可被接管(monkeypatch, tmp_path):
    monkeypatch.setattr(tq_gate, "_pid_alive", lambda pid: False)
    p = tq_gate.lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("999999 2020-01-01T00:00:00\n", encoding="utf-8")
    old = time.time() - 9999
    os.utime(p, (old, old))
    with tq_gate.hold(who="接管者", timeout=2):
        assert str(os.getpid()) in p.read_text(encoding="utf-8")   # 锁已归本进程
    assert not p.exists()


def test_进程还活着的锁不许抢(monkeypatch):
    monkeypatch.setattr(tq_gate, "_pid_alive", lambda pid: True)
    with tq_gate.hold(who="第一个"):
        st = tq_gate.status()
        assert st["held"] is True and st["holder_alive"] is True


def test_释放后状态干净():
    with tq_gate.hold(who="一次性"):
        pass
    assert tq_gate.status()["held"] is False
