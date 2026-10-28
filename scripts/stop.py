"""一键关闭本机 YuanShan：清理服务端口与其进程树（仅标准库）。

为什么不是简单 taskkill 端口占用者：Windows 上服务挂在 engine_guard 的
Job Object 里（KILL_ON_JOB_CLOSE、不可 breakaway），任务/引擎子进程都在同一
Job 中。先杀**启动器**（manage.py）会让 guard 检测到父进程消失、自行
TerminateJobObject 收掉整棵树，比直接杀 uvicorn 干净得多。因此顺序是
启动器 → guard 树 → 端口占用者 → 产品自带引擎残留，每步之后复查。

POSIX 同样可用：scripts/stop.py 走 SIGTERM/SIGKILL 进程组。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = os.name == "nt"

# 每类进程用命令行特征识别；命中即属本产品，不靠端口猜。
PATTERNS = {
    "启动器": r"manage\.py\s+(auto|start)\b",
    "进程守卫": r"engine_guard\.py",
    "Web 服务": r"(?:^|\s)(?:python[^\s]*\s+-m\s+)?uvicorn\b.*server:app|server:app",
    "取数子进程": r"public_worker\.py|review_agent\.public_worker",
    "AI 引擎": r"@openai[/\\]codex|codex\.exe|runtime[/\\]bridge[/\\]main\.mjs|codex-win32",
}
OWN = re.compile(re.escape(str(ROOT)) + r"|manage\.py", re.IGNORECASE)

# 只允许这些镜像被认作本项目的运行体。少了这道闸，任何命令行里出现过
# "scripts/manage.py start" 的 bash/powershell（比如另一个终端或本脚本自己的
# 调用方）都会被当成"启动器"杀掉 —— 盲杀别人的 shell 是不可接受的代价。
WINDOWS_IMAGES = {"python.exe", "pythonw.exe", "python3.exe", "py.exe",
                  "pyw.exe", "node.exe", "codex.exe"}


def _ancestors(pid: int, by_pid: dict[int, dict]) -> set[int]:
    """自己的整条父链都不许碰。"""
    chain, cur, guard = set(), pid, 0
    while cur and cur not in chain and guard < 40:
        chain.add(cur)
        row = by_pid.get(cur)
        cur = int(row["ppid"]) if row else 0
        guard += 1
    return chain - {pid}


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def windows_processes() -> list[dict]:
    """Get-CimInstance 拿全量 PID/PPID/CommandLine（tasklist 给不出命令行）。"""
    cmd = ("Get-CimInstance Win32_Process | "
           "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return []
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    rows = data if isinstance(data, list) else [data]
    return [{"pid": int(r.get("ProcessId") or 0), "ppid": int(r.get("ParentProcessId") or 0),
             "name": _text(r.get("Name")), "cmd": _text(r.get("CommandLine"))}
            for r in rows if isinstance(r, dict)]


def posix_processes() -> list[dict]:
    try:
        out = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), "name": "", "cmd": parts[2]})
    return rows


def listening_pids(port: int) -> set[int]:
    """命令行认不出时的兜底：端口确实被人占着，就顺着 PID 找。"""
    try:
        if IS_WINDOWS:
            out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=30).stdout
            pat = re.compile(r":%d\s+\S+\s+LISTENING\s+(\d+)" % port)
        else:
            out = subprocess.run(["sh", "-c", f"lsof -nP -iTCP:{port} -sTCP:LISTEN -t 2>/dev/null"],
                                 capture_output=True, text=True, timeout=30).stdout
            pat = re.compile(r"^(\d+)$")
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {int(m) for m in pat.findall(out) if int(m) > 0}


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def classify(procs: list[dict], port: int) -> list[dict]:
    pids_on_port = listening_pids(port)
    me = os.getpid()
    by_pid = {row["pid"]: row for row in procs}
    kin = _ancestors(me, by_pid)
    targets = []
    for row in procs:
        if row["pid"] == me or row["pid"] in kin or not row["cmd"]:
            continue
        if IS_WINDOWS and row["name"].lower() not in WINDOWS_IMAGES:
            continue
        hit = next((label for label, pat in PATTERNS.items() if re.search(pat, row["cmd"], re.IGNORECASE)), "")
        if hit and OWN.search(row["cmd"]):
            row = {**row, "why": hit}
        elif row["pid"] in pids_on_port:
            row = {**row, "why": f"占用 {port} 端口"}
        else:
            continue
        targets.append(row)
    return targets


def kill(pid: int, tree: bool) -> None:
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), *(["/T", "/F"] if tree else ["/F"])],
                       capture_output=True, timeout=30)
        return
    try:
        if tree:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def force_kill(pid: int) -> None:
    if IS_WINDOWS:
        kill(pid, tree=True)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def lock_released() -> "bool | None":
    """backend.lock 是字节范围锁；进程被强杀后 OS 会释放。能加上锁=没有僵尸后端。"""
    home = Path(os.environ.get("ASTOCK_AGENT_HOME") or (Path.home() / ".vibe-astock-agent"))
    lock = home / "backend.lock"
    if not lock.is_file():
        return None
    try:
        with lock.open("a+b") as fh:
            if IS_WINDOWS:
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
        return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="关闭 YuanShan 并清理端口与进程")
    parser.add_argument("--port", type=int, default=int(os.environ.get("VIBE_PORT") or 8910))
    parser.add_argument("--dry-run", action="store_true", help="只列出将要关闭的进程，不动手")
    parser.add_argument("--wait", type=float, default=8.0, help="优雅等待秒数，默认 8")
    args = parser.parse_args()
    port, root = args.port, ROOT

    reader = windows_processes if IS_WINDOWS else posix_processes
    targets = classify(reader(), port)
    if not targets:
        print(f"没有发现正在运行的 YuanShan 进程；端口 {port} "
              f"{'已空闲' if port_free(port) else '仍被占用（但不是本项目的进程，已交给你确认）'}。")
        return 0 if port_free(port) else 1

    print(f"发现 {len(targets)} 个本项目进程（根目录 {root}）：")
    for row in sorted(targets, key=lambda r: r["pid"]):
        print(f"  PID {row['pid']:>7}  {row['why']:<14} {row['name'] or '-'}")
    if args.dry_run:
        print("--dry-run：未做任何改动。")
        return 0

    by_pid = {row["pid"]: row for row in targets}
    # ① 先杀启动器：guard 见父进程消失会自己把 Job 里整棵树收掉。
    for pid, row in by_pid.items():
        if row["why"] == "启动器":
            kill(pid, tree=False)
    deadline = time.monotonic() + max(args.wait, 2.0)
    alive = set(by_pid)
    while alive and time.monotonic() < deadline:
        time.sleep(.3)
        alive &= {r["pid"] for r in reader()}

    # ② 没走干净的：guard 树 → 服务/取数/AI 引擎 → 端口占用者，逐层强杀。
    for stage in ("进程守卫", "Web 服务", "取数子进程", "AI 引擎", "启动器"):
        live = {r["pid"] for r in reader()}
        for pid, row in by_pid.items():
            if row["why"] == stage and pid in live:
                force_kill(pid)
    # ③ 端口上还坐着人：重新识别一遍，只杀认得出的本项目进程。
    #     非本项目的占用者一律留着，交给你自己确认 —— 盲杀别人的端口代价太大。
    for row in classify(reader(), port):
        force_kill(row["pid"])

    for _ in range(20):
        if port_free(port):
            break
        time.sleep(.3)
    left = sorted(listening_pids(port))
    lock = lock_released()
    print()
    print(f"端口 {port}：{'已释放' if port_free(port) else '仍被占用 PID ' + (','.join(map(str, left)) or '?')}"
          f"；后端锁 {'已释放' if lock is True else ('无锁文件' if lock is None else '仍被持有——有进程没杀干净')}。")
    if port_free(port) and lock is not False:
        print("已关闭。重新启动：双击「启动 YuanShan.cmd」。")
        return 0
    print("未能完全关闭，请把上面残留的 PID 贴出来再排查（不要手工 taskkill 未知进程）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
