"""Responses 转换层的上游失败必须留下可查记录（判据 06 §1、§2）。

这台机器上 AI 通道的每一次模型调用都要经过 `review_agent/responses_bridge.py`：
引擎只会说"流被截断"，真因（连接被拒 / 读超时 / 对端断 / 上游 401）原先在
`except (httpx.HTTPError, OSError): return` 里直接丢掉，`manage.py doctor` 与
`jobs/state.json` 一个字都没有。
"""
import json
from pathlib import Path

import httpx
import pytest

from duanxian import jobs
from review_agent import responses_bridge as b

SOURCE = Path("review_agent/responses_bridge.py").read_text(encoding="utf-8")


@pytest.fixture
def spy(monkeypatch):
    """把记账函数换成假记录器：真 `record()` 写数据根目录，路径由 conftest 定。"""
    calls = []
    monkeypatch.setattr(jobs, "record",
                        lambda name, *, ok, detail=None: calls.append((name, ok, dict(detail or {}))))
    return calls


def test_上游非200也留下一句含状态码(spy):
    b._note_failure(None, "上游返回 HTTP 401")
    assert spy == [(b.STATE_KEY, False, {"reason": "上游返回 HTTP 401"})]


def test_流断了要带异常类型并截断长文本(spy):
    huge = "x" * 900
    b._note_failure(httpx.ReadError(huge), "Responses 转换层上游流中断")
    name, ok, detail = spy[0]
    assert name == b.STATE_KEY and ok is False
    assert detail["reason"].startswith("Responses 转换层上游流中断｜ReadError：")
    assert len(detail["reason"]) <= 200          # 上游错误串里带着地址，不外传整段


def test_成功也要记一句把连败归零(spy):
    """只记失败的话，恢复之后体检会一直红着一个已经不存在的故障。"""
    b._note_success()
    assert spy == [(b.STATE_KEY, True, {"reason": "Responses 转换层上游正常返回"})]


def test_记账通道自己坏了不影响翻译本身(monkeypatch):
    def boom(*a, **kw):
        raise OSError("磁盘坏了")
    monkeypatch.setattr(jobs, "record", boom)
    b._note_failure(httpx.ConnectError("拒绝连接"), "上游流中断")   # 不许抛出来


def test_两处上游失败都不再是静默return():
    """钉住接线本身：这两道分支以前只有 `return`。"""
    assert "except (httpx.HTTPError, OSError) as exc:" in SOURCE
    guard = SOURCE.split("except (httpx.HTTPError, OSError) as exc:")[1].split("\n\n")[0]
    assert "_note_failure(exc" in guard, guard[:200]
    reject = SOURCE.split("if upstream.status_code != 200:")[1].split("\n\n")[0]
    assert "_note_failure(None" in reject, reject[:200]


def test_翻译层自己不做重试并说清为什么():
    """这条通道**故意**不重试：一次 Responses 调用背后是付费模型调用，
    重试的钱要由调用方（有上限的那几道）决定。上限见 runtime 的 budget 与
    `UPSTREAM_TIMEOUT`。"""
    assert "for attempt" not in SOURCE and "retry" not in SOURCE.lower()
    assert "UPSTREAM_TIMEOUT = httpx.Timeout(connect=15, read=180, write=30, pool=15)" in SOURCE
