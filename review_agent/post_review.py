"""Deterministic post-review capture; never invokes a model or changes the report.

Each result is retained next to the generation evidence. Network/data failures
are explicit and may be retried without regenerating a paid AI report.
"""
from .engine_guard import guard_python
from threading import Lock

_CAPTURE_LOCK = Lock()

from duanxian import archive, backtest, theme_tree, reflection


def capture_after_review(date: str) -> dict:
    with _CAPTURE_LOCK:
        return _capture_after_review(date)


def _capture_after_review(date: str) -> dict:
    results = {}
    # Capture caches before archiving their source rows.
    for name, capture in (("theme_reasons", theme_tree.capture),
                          ("backtest_corpus", backtest.capture),
                          ("archive", archive.capture_day)):
        try:
            result = capture(date)
            results[name] = result if isinstance(result, dict) else {"ok": False, "reason": "未返回保存结果"}
        except Exception as exc:
            results[name] = {"ok": False, "reason": f"{type(exc).__name__}：未完成，可单独重试"}
    try:
        value = reflection.auto_evaluate_prior(date, strict=True)
        results["reflection"] = {"ok": True, "status": "evaluated" if value else "not_applicable", "reason": "已回评" if value else "没有可完成的上期回评；历史预测或次日数据未覆盖"}
    except Exception as exc:
        results["reflection"] = {"ok": False, "reason": "回评所需资料尚未完整，未完成，可重试" if str(exc) == "回评所需资料尚未完整，未完成，可重试" else f"{type(exc).__name__}：回评未完成，请检查数据源后重试"}
    return results


def _note(ok: bool, reason: str) -> None:
    """归档结果要能被别人查到。

    只写 `post_review.json` 那份明细是不够的 —— 实测全仓只有写它的两行和一个测试在碰它，
    **没有任何读者**（判据 06 §7：没人读的现场等于没留）。所以同一句话再记进
    `jobs/state.json`，`manage.py doctor` 的「采集作业」按 state 的每个键在读。
    """
    try:
        from duanxian import jobs
        jobs.record("review_capture", ok=ok, detail={"reason": reason[:300]})
    except Exception:  # noqa: BLE001  记账通道坏了不该影响归档本身
        pass


# Both automatic capture and explicit retry enter through this bounded worker.
# Isolate network calls so cancellation can actually stop a stalled fetch.
def capture_bounded(date: str, check=None, timeout: float = 90) -> dict:
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    import tempfile
    import time
    from review_agent.evidence import valid_date
    from review_agent.runtime import REPO, stop_process
    valid_date(date)
    if not _CAPTURE_LOCK.acquire(blocking=False):
        _note(False, "归档正在处理，本次跳过（报告已保存，可稍后重试）")
        return {"capture": {"ok": False, "reason": "归档正在处理，请稍后重试"}}
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="astock-capture-") as directory:
            result_path = Path(directory) / "result.json"
            process = subprocess.Popen([guard_python(), str(REPO / "review_agent/engine_guard.py"),
                                       str(timeout), str(os.getpid()), sys.executable, "-m", "review_agent.post_review", date, str(result_path)],
                                       cwd=Path(__file__).resolve().parents[1],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                                       start_new_session=os.name == "posix")
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if check is not None:
                    check()
                if time.monotonic() >= deadline:
                    _note(False, f"归档取数超时（{timeout:.0f} 秒），报告已保存，可单独重试")
                    return {"capture": {"ok": False, "reason": "归档取数超时，报告已保存，可单独重试"}}
                try:
                    process.wait(timeout=min(0.2, max(0.01, deadline-time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode or not result_path.is_file():
                _note(False, f"归档进程未完成（退出码 {process.returncode}），报告已保存，可重试")
                return {"capture": {"ok": False, "reason": "归档进程未完成，报告已保存，可重试"}}
            results = json.loads(result_path.read_text(encoding="utf-8"))
            # 逐条隔离成立时，缺哪一块要能说出来；否则"整条归档绿了"会盖住少掉的那一块。
            missing = {key: (value.get("reason") if isinstance(value, dict) else value)
                       for key, value in results.items()
                       if not (isinstance(value, dict) and value.get("ok"))}
            _note(not missing, (f"归档 {len(results)} 块全部完成" if not missing else
                                "归档部分未完成：" + "、".join(
                                    f"{key}（{str(reason or '')[:60]}）"
                                    for key, reason in missing.items())))
            return results
    finally:
        if process is not None:
            stop_process(process)
        _CAPTURE_LOCK.release()


if __name__ == "__main__":
    import json
    from pathlib import Path
    import sys
    from review_agent.evidence import valid_date
    valid_date(sys.argv[1])
    Path(sys.argv[2]).write_text(json.dumps(_capture_after_review(sys.argv[1]), ensure_ascii=False), encoding="utf-8")
