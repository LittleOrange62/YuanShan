# -*- coding: utf-8 -*-
"""TQ 本地接口全局串行闸。

**为什么非要有**：通达信客户端的 TQ 是**单连接**的（官方 Skill 明文：同一时刻只许一个调用）。
两个作业并发打它时的失败方式特别坏 —— 不是报错，而是**返回空/少数据**：
`c18`（板块成分）会少几只票的成分、`c19`（除权除息）会漏事件，事后看就像
"那天本来就没有"，和自选同步那次"零报错的陈旧"是同一类事故。所以串行不是性能问题，是
**正确性**问题：任何走 TQ 的代码都必须经过这里。

**两层锁**：
1. 进程内 `threading.RLock`（可重入：调用方在循环里套 `_post` 不会自己锁死）；
2. 文件锁 `var/locks/tq_http.lock`，内容写 `pid + 时刻`。
   只有进程内锁不够 —— 常驻 uvicorn 的采集线程和人手跑的 `python -m ingest.jobs.c18_*`
   是两个进程，它们互相看不见对方的内存锁。

**陈旧锁必须能接管**：Ctrl-C / 崩溃留下的锁如果永久占着，TQ 就再也没人能用了，
而那看起来像"客户端坏了"。所以 pid 已消失且超过 `FT_TQ_LOCK_STALE`（默认 900s）→ 接管。
等锁超时是**显式异常**（`TqBusy`），不静默跳过：跳过就等于少一批数据还当成功。
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from pathlib import Path

from app.config import BASE_DIR

_INPROC = threading.RLock()
_owner = threading.local()          # 持锁深度：**文件锁不可重入**，同线程嵌套必须直接放行
NAME = "tq_http.lock"
STALE_SECONDS = float(os.environ.get("FT_TQ_LOCK_STALE", 900))
WAIT_TIMEOUT = float(os.environ.get("FT_TQ_LOCK_WAIT", 300))
POLL = 0.5


class TqBusy(TimeoutError):
    """拿不到 TQ 锁。带上占用者信息，好让人知道该去停谁。"""


def lock_dir() -> Path:
    d = Path(os.environ.get("FT_LOCK_DIR") or (BASE_DIR / "var" / "locks"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def lock_path() -> Path:
    return lock_dir() / NAME


def _pid_alive(pid: int) -> bool:
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # SYNCHRONIZE
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    except Exception:      # noqa: BLE001  非 Windows / API 不可用 → 保守当活着，别乱抢
        return True


def holder() -> str:
    try:
        return lock_path().read_text(encoding="utf-8", errors="replace").strip() or "?"
    except OSError:
        return "?"


def _try_take() -> "Path | None":
    p = lock_path()
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("%d %s\n" % (os.getpid(), time.strftime("%Y-%m-%dT%H:%M:%S")))
    return p


def _steal_if_stale() -> "Path | None":
    """崩溃留下的锁要能接管，否则一次 Ctrl-C 就把 TQ 永久锁死。"""
    p = lock_path()
    try:
        raw = p.read_text(encoding="utf-8", errors="replace").strip().split()
        pid = int(raw[0]) if raw and raw[0].lstrip("-").isdigit() else None
        age = time.time() - p.stat().st_mtime
    except OSError:
        return None                      # 文件刚好消失：下一轮重试
    if age < STALE_SECONDS or (pid is not None and _pid_alive(pid)):
        return None
    try:
        p.unlink()
    except OSError:
        return None
    return _try_take()


@contextlib.contextmanager
def hold(*, who: str = "", timeout: float | None = None):
    """`with tq_gate.hold(who='c18'):` —— 拿到锁才放行，超时抛 `TqBusy`。

    ⚠️ 嵌套必须短路：`_INPROC` 是可重入锁，但**文件锁不是**。不加这段，
    c18 那种"循环里再调 `_post`"的写法会让内层去抢自己已经拿着的锁，
    干等 `WAIT_TIMEOUT`（默认 300 秒）后抛 `TqBusy` —— 表现完全像"TQ 客户端挂了"。
    """
    if getattr(_owner, "depth", 0):
        _owner.depth += 1
        try:
            yield
        finally:
            _owner.depth -= 1
        return
    limit = WAIT_TIMEOUT if timeout is None else timeout
    if not _INPROC.acquire(timeout=max(0.01, limit)):
        raise TqBusy("TQ 被占着（本进程内已有线程在用它），等 %gs 拿不到锁，占用者见 %s"
                     % (limit, holder()))
    try:
        path = _try_take() or _steal_if_stale()
        waited = 0.0
        while path is None:
            if waited >= limit:
                raise TqBusy("TQ 被占着（别的进程持有：%s），等 %gs 拿不到锁"
                             % (holder(), waited))
            time.sleep(POLL)
            waited += POLL
            path = _try_take() or _steal_if_stale()
        _owner.depth = 1
        try:
            yield
        finally:
            _owner.depth = 0
            try:
                path.unlink()
            except OSError:
                pass
    finally:
        _INPROC.release()


def status() -> dict:
    """给 doctor / /api/jobs 看的只读快照：锁在不在、谁拿着、多久没释放。"""
    p = lock_path()
    if not p.exists():
        return {"held": False, "path": str(p)}
    try:
        age = round(time.time() - p.stat().st_mtime, 1)
    except OSError:
        age = None
    raw = holder()
    pid = raw.split()[0] if raw.split() else "?"
    return {"held": True, "path": str(p), "holder_pid": pid, "age_seconds": age,
            "holder_alive": pid.lstrip("-").isdigit() and _pid_alive(int(pid)),
            "stale_after_seconds": STALE_SECONDS}
