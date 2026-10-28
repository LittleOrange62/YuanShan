"""体检「采集作业」那一档的边界：谁该按"今天该有一次成功"判红（2026-10-18）。

起因是两条相反的错：

1. **把没人用说成没跑成**：`responses_bridge` 是本机 Responses→Chat 转换层，
   只在有人真发起一次 AI 调用时才记一笔（`review_agent/responses_bridge.py` 里那个 `_note`
   只在 `Handler.do_POST` 被调），**没有任何每日循环**。老判据按"最近成功停在 10-15 而应到 10-16"
   判红，于是"今天没人用 AI"变成一条天天挂在体检里的假故障。
2. **反过来，不能因为"它不在 CLOSE_DEFAULTS 里"就一律豁免**：`daily_close_client`、`tdx_shell_reap`
   同样不在那张清单里，而它们**每天都在跑**，是真该判的。所以豁免走一张**点名清单**
   （`manage.MANUAL_PROBE_JOBS`），放错一条就等于给一条真故障买免死牌 —— 本文件钉的就是这条边界。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import manage

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "datahome"
    (h / "jobs").mkdir(parents=True)
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(h))
    return h


def _state(home, mapping):
    (home / "jobs" / "state.json").write_text(json.dumps(mapping), encoding="utf-8")


def _row(at, **kw):
    one = {"last_run_at": at, "last_ok_at": at, "last_status": "ok", "consecutive_failures": 0}
    one.update(kw)
    return one


def test_手动探针没人使用不判红并说清为什么不判(home):
    _state(home, {"responses_bridge": _row("2026-10-15 08:53:01")})
    ok, action = manage.collector_status("2026-10-18")
    assert ok, action
    assert "responses_bridge" in action, "这一格还得念出来，只是不判红"
    assert "不判红" in action and "手动" in action, action


def test_手动探针自己失败了照样要出声(home):
    _state(home, {"responses_bridge": _row(
        "2026-10-18 09:00:00", last_ok_at=None, last_status="fail",
        consecutive_failures=2, last_error="上游 502")})
    ok, action = manage.collector_status("2026-10-18")
    assert not ok and "从未成功过" in action, action      # 跑过却一次没成＝真故障，豁免够不着它


def test_不在册的每日作业照旧按该有今天判(home):
    """`tdx_shell_reap` 不在 `CLOSE_DEFAULTS`，但它是两分钟一轮的常驻循环。
    谁要是把豁免改成"不在册就免判"，这一条会立刻红。"""
    assert "tdx_shell_reap" not in manage.declared_jobs()[0]
    _state(home, {"tdx_shell_reap": _row("2026-10-17 23:43:09")})
    ok, action = manage.collector_status("2026-10-18")
    assert not ok and "tdx_shell_reap" in action, action
    assert "这一场要么没跑要么没跑成" in action, action


def test_豁免名单里的每一条都没有每日循环():
    """名单只能放"被调用才记一笔"的东西：全仓搜一遍，记账点必须在它自己模块里，
    而 `server.py` 的常驻宿主里不许出现它（出现了就说明它变成日更作业，该从名单里撤）。"""
    from duanxian import jobs as J
    assert set(manage.MANUAL_PROBE_JOBS).isdisjoint(J.CLOSE_DEFAULTS), \
        "在册作业不许进豁免名单"
    host = (ROOT / "server.py").read_text(encoding="utf-8")
    for name in manage.MANUAL_PROBE_JOBS:
        assert 'record("%s"' % name not in host and "record('%s'" % name not in host, \
            "%s 被宿主循环记账了，它不是手动探针" % name
        own = (ROOT / "review_agent" / "responses_bridge.py").read_text(encoding="utf-8")
        assert "_note(" in own, "转换层自己那笔账没了，豁免的前提也就不成立"
