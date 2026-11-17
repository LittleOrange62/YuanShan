# -*- coding: utf-8 -*-
"""程序自己拉起的通达信实例的租约账本：pid + 拉起时刻 + 到点收尾判据。

用户 2026-11-12 的原话：「记录下开启后的通达信的进程id然后放个半小时后关闭的脚本，
去定时关闭通达信客户端」。这条与已有的「带外主动关」是两把不同的尺：

- 已有那把（`server._tdx_close_when_idle`）按绝对时钟带判：带内保持开着、带外才关。
  本机拨的是 09:00–19:00（用户 10-19 自己设的），所以程序早上为补数拉起的那一个
  会一直挂到晚上 —— 它没错，但它回答不了「程序自己开的，用完就该收」。
- 这一把按相对时长判：拉起时刻 + `lease_close_after_min`（默认 30）到点就收，
  不受时间带保护。

三条不许破的边界：

1. 只收账本里记着的那个 pid。人自己开的、或人重启过换了实例的，一律不动
   （pid 对不上就作废账本并出声，不是「差不多就关」）。
2. 真关的时候走 `server._close_tdx_client()` 那一个温和退出出口：同步中不收、
   vipdoc 还在写不收、绝不 /F 强杀、确认框只点全文等于「退出」那一个按钮。
   本模块只判「该不该收」，不自己发任何窗口消息。
3. `keep_alive` 那一档豁免：用户选它就是「全天候不关」，这条租约不许推翻那个选择。

落盘而不是只住进程内存：8910 一重启就忘的账本等于没有账本
（同族缺陷见 `_client_down_day` / `_keep_alive_restarts`）。
"""

from __future__ import annotations

import datetime as dt
import json
import os

from .paths import data_path
from .util import atomic_write_json, china_now

LEASE_FILE = "tdx_lease.json"

#: 拉起之后多久还没确认到 pid 就不再认领现探到的那个实例。
#: 超过这个岁数的账本去认一个「现在活着的 pid」，认到的很可能是人自己开的那一个。
ATTACH_WINDOW_SEC = 600


def lease_path() -> str:
    return data_path(LEASE_FILE)


def _now(now: dt.datetime | None = None) -> dt.datetime:
    return now or china_now()


def _stamp(now: dt.datetime | None = None) -> str:
    return _now(now).isoformat(timespec="seconds")


def _parse(text) -> dt.datetime | None:
    if not text:
        return None
    try:
        got = dt.datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return got if got.tzinfo is not None else got.replace(tzinfo=_now().tzinfo)


def read() -> dict | None:
    """读回账本。读不到/读坏了都返回 None —— 判不了就不动手，不猜一份出来。"""
    try:
        with open(lease_path(), encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) and got.get("launched_at") else None


def _write(payload: dict) -> dict:
    atomic_write_json(lease_path(), payload)
    return payload


def mark_launched(*, why: str = "", now: dt.datetime | None = None) -> dict:
    """真起了一个新实例那一刻记账（唯一调用点：`s1_launch.act` 的 Popen 成功之后）。

    pid 先留空：Popen 只证明「发了启动」，进程号要等 `s1_launch.verify` 看到窗口才算数。
    复用已在跑的客户端（precheck 那条）不进这里 —— 否则每轮都把计时刷新一遍，永远到不了点。
    """
    return _write({"pid": None, "launched_at": _stamp(now), "day": _now(now).strftime("%Y-%m-%d"),
                   "why": why or "程序拉起", "status": "open", "deferred": [],
                   "closed_at": None, "close_reason": None})


def attach_pid(pid: int, *, now: dt.datetime | None = None) -> dict | None:
    """把确认到的 pid 补进账本。只补一次，且只在拉起后 ATTACH_WINDOW_SEC 内认。"""
    cur = read()
    if not cur or cur.get("pid") or not pid:
        return cur
    born = _parse(cur.get("launched_at"))
    if born is None or (_now(now) - born).total_seconds() > ATTACH_WINDOW_SEC:
        return cur
    cur["pid"] = int(pid)
    return _write(cur)


def defer(why: str, *, now: dt.datetime | None = None) -> dict | None:
    """到点了但这一刻不该动手（链在跑/下载在跑/人在用键鼠）⇒ 记一次顺延，下一轮再判。"""
    cur = read()
    if not cur:
        return None
    cur.setdefault("deferred", []).append({"at": _stamp(now), "why": why})
    cur["status"] = "deferred"
    return _write(cur)


def mark_closed(*, reason: str = "", now: dt.datetime | None = None) -> dict | None:
    cur = read()
    if not cur:
        return None
    cur.update(status="closed", closed_at=_stamp(now), close_reason=reason)
    return _write(cur)


def expire(*, reason: str = "", now: dt.datetime | None = None) -> dict | None:
    """顺延用完了：停手、留痕、交人工。不再每轮去催同一个窗口。"""
    cur = read()
    if not cur:
        return None
    cur.update(status="expired", closed_at=None, close_reason=reason, expired_at=_stamp(now))
    return _write(cur)


def clear(reason: str, *, now: dt.datetime | None = None) -> dict | None:
    """账本作废（客户端已经不在了 / pid 对不上 / 人接管了）。留着读数，但不再据此动手。"""
    cur = read()
    if not cur:
        return None
    cur.update(status="cleared", close_reason=reason, cleared_at=_stamp(now))
    return _write(cur)


def facts(lease: dict | None, *, now: dt.datetime | None = None,
          after_min: int = 30) -> dict:
    """交给屏幕的那一份读数。没账本就是没账本，不拿「此刻」充数。"""
    if not lease:
        return {"available": False, "reason": "没有租约记录：这个客户端不是程序拉起的（或账本已清）"}
    born = _parse(lease.get("launched_at"))
    out: dict = {"available": True, "pid": lease.get("pid"),
                 "launched_at": lease.get("launched_at"), "why": lease.get("why"),
                 "status": lease.get("status"), "after_min": int(after_min),
                 "deferred_count": len(lease.get("deferred") or []),
                 "last_defer": (lease.get("deferred") or [None])[-1],
                 "closed_at": lease.get("closed_at"), "close_reason": lease.get("close_reason")}
    if born is None:
        out["due_at"] = None
        out["left_min"] = None
        out["age_min"] = None
        return out
    due = born + dt.timedelta(minutes=int(after_min))
    out["due_at"] = due.isoformat(timespec="seconds")
    out["age_min"] = round((_now(now) - born).total_seconds() / 60.0, 1)
    out["left_min"] = round((due - _now(now)).total_seconds() / 60.0, 1)
    return out


def verdict(lease: dict | None, *, now: dt.datetime, live: list[int],
              after_min: int, max_defer_min: int,
              enabled: bool = True, mode: str = "on_demand") -> tuple[str, str]:
    """该不该收这一个实例。纯函数：不读盘、不写盘、不碰窗口。

    出口七档（调用方按档动作，不许把「判不了」读成「该关」）：

    - `off`     开关关着，或 client_mode=keep_alive（用户选了全天候不关）
    - `none`    没有账本 ⇒ 这不是程序拉起的，不动
    - `done`    账本已经收过/作废过 ⇒ 不重复动手
    - `gone`    账本记的那个 pid 已经不在活着的实例里，且没有别的活实例 ⇒ 清账本
    - `stale`   还有活实例，但 pid 对不上（人重启过）⇒ 清账本，绝不收别人的实例
    - `wait`    还没到点（或还没确认到 pid）
    - `close`   到点了，该走温和退出出口
    - `expired` 到点之后顺延已经用满 max_defer_min ⇒ 停手交人工
    """
    if not enabled:
        return "off", "lease_close_enabled=false：不自动收程序拉起的客户端"
    if mode == "keep_alive":
        return "off", "client_mode=keep_alive：用户选了全天候保持运行，租约这一条不生效"
    if not lease:
        return "none", "没有租约记录：这个客户端不是程序拉起的，不动它"
    status = str(lease.get("status") or "open")
    if status in ("closed", "cleared", "expired"):
        return "done", "租约已经是「%s」：%s" % (status, lease.get("close_reason") or "没写原因")
    born = _parse(lease.get("launched_at"))
    if born is None:
        return "wait", "租约里的拉起时刻读不出来 → 判不了到没到点，不动手"
    pid = lease.get("pid")
    live = [int(p) for p in (live or [])]
    if not pid:
        # 认领窗口之外还没确认到 pid（s1 的复验没跑到、或宿主中间重启过）⇒ 作废，
        # 不许永远停在 wait：那等于「记了一笔却永远没人收」，而且再去认领现在活着的
        # 那个实例，认到的很可能是人自己开的。
        if (now - born).total_seconds() > ATTACH_WINDOW_SEC:
            return "stale", ("记了拉起时刻但超过 %d 分钟仍没确认到 pid（复验没跑到）"
                             "→ 作废账本，不去认领现在活着的实例" % (ATTACH_WINDOW_SEC // 60))
        return "wait", "记了拉起时刻但还没确认到 pid（等 s1_launch 复验补上）"
    if int(pid) not in live:
        if not live:
            return "gone", "租约记的 pid %s 已经不在跑了 → 清账本" % pid
        return "stale", ("现在活着的是 %s，租约记的是 %s —— 不是同一个实例（人重启过）"
                         "→ 清账本，不收别人的客户端" % (live, pid))
    due = born + dt.timedelta(minutes=int(after_min))
    if now < due:
        left = (due - now).total_seconds() / 60.0
        return "wait", "pid %s 是程序 %s 拉起的，%.1f 分钟后到点收（%s）" % (
            pid, lease.get("launched_at"), left, lease.get("why") or "程序拉起")
    over = (now - due).total_seconds() / 60.0
    if over > int(max_defer_min):
        return "expired", ("pid %s 到点已经 %.0f 分钟，顺延用满 %d 分钟仍没能收掉 → 停手交人工"
                           % (pid, over, int(max_defer_min)))
    return "close", "pid %s 是程序 %s 拉起的，已到点（拉起后 %d 分钟）→ 该收" % (
        pid, lease.get("launched_at"), int(after_min))