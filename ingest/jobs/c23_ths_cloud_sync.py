# -*- coding: utf-8 -*-
"""同花顺自选「云端 → 本机」同步：启动 → 等待 → 关闭（只碰我们自己在本次起的进程）。

为什么要这一条（用户 2026-10-08 原话）：「同花顺也需要制作一套模拟鼠标操作的启动-等待-关闭的流程。
等待的目的是同步云端的自选股分组列表和清单。」

链路事实（现测，2026-10-08）：
  · 客户端在 `D:\\hanchen\\同花顺\\hexin.exe`（`app/config.py:92` 的 `THS_DIR`，可用 `FT_THS_DIR` 覆盖）；
  · 自选的真源是**用户目录**下的 `stockblock.ini` —— `ingest/ths_local.find_user_dir()` 的判据是
    "目录里有 stockblock.ini"，实测命中的是 `炒股大王哈哈/stockblock.ini`（今天 12:56 刚被写过）；
    `system/同花顺方案/StockBlock.ini` 是另一套（2.9 MB），**不是**我们读的那一份；
  · `duanxian/watchlist_sync.py` 只读这个 ini 的**当前值**，原先没有任何地方保证它是新鲜的 ——
    云端改了分组、本机客户端没启动过，ini 就还是旧的，而页面上看不出区别（"看着同步了其实没同步"）。

三条口径（决定实现形状）：

1. **"等待"必须有判据，不能睡固定秒数**。判据 = 那一份 ini 的 `(mtime, size)` 变新。
   到点没变也**照实说"没看到变化"**（`unchanged`），不谎报成功 —— 客户端可能已登录态就直接同步完了、
   也可能今天根本没改过分组，这两种都该由读数的人判断，不该由这里替他们编一个"同步完成"。
2. **只关本次自己起的进程**。启动前先扫一遍：如果同花顺已经在跑（用户开着），那就**不启动也不关闭**，
   只等 —— 关掉用户正在用的客户端是不可接受的代价（通达信那条链已经定过同样的规矩，
   见 `docs/数据获取工作指南/03-故障排查.md:152-183`）。
3. **绝不"猜着点"**。这一步不模拟鼠标点击任何按钮：云同步是客户端启动后自己做的，
   我们只负责"把它起来、等文件变、把窗口收掉"。真遇到确认框，只**读**正文并原样写进日志与 stuck，
   **不点**（`NEVER_CLICK` 那份名单在 `app/tdx_client/steps/s8_finish.py:44`，同一个道理：
   误点一次「取消下载」掉的是正在下的数据，这里误点一次可能改掉用户的分组）。

退出码：0 = 看到 ini 变新（或已在跑且等到变化）；3 = 起了但没看到变化（不红，按 skipped 记账）；
2 = 前置不满足（找不到客户端/找不到 ini/桌面不可用）；1 = 真异常。

用法：
    python -m ingest.jobs.c23_ths_cloud_sync --check            # 只探路径与进程，不动界面
    python -m ingest.jobs.c23_ths_cloud_sync                    # 真跑一轮（第一次请人在键盘前盯着）
    python -m ingest.jobs.c23_ths_cloud_sync --wait 420 --no-close
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ingest import ths_local  # noqa: E402

PROCESS_NAMES = ('hexin.exe',)          # 主程序；helper/launcher 不单独算"客户端在跑"
WAIT_DEFAULT_SEC = 240                  # 等 ini 变新的上限（秒）
POLL_SEC = 5
USER_DATA = os.environ.get('FT_THS_STATE_DIR') or str(ROOT / 'var' / 'ths_cloud_sync')


# ---------------------------------------------------------------- 只读探测（`--check` 走这条）

def exe_path() -> Path | None:
    d = ths_local.ths_dir()
    for name in PROCESS_NAMES:
        p = d / name
        if p.is_file():
            return p
    return None


def ini_path() -> Path | None:
    """我们真正要的那一份：**用户目录下的 stockblock.ini**，与 `watchlist_sync` 同一个判据。"""
    base = ths_local.find_user_dir()
    return (base / 'stockblock.ini') if base else None


def running_pids() -> list[int]:
    """当前在跑的同花顺主进程 pid（只查，不动）。"""
    import ctypes.wintypes as wt
    if os.name != 'nt':
        return []
    class PE32(ctypes.Structure):
        _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wt.DWORD),
                    ('cntThreads', wt.DWORD), ('th32ParentProcessID', wt.DWORD),
                    ('pcPriClassBase', ctypes.c_long), ('dwFlags', wt.DWORD),
                    ('szExeFile', ctypes.c_char * 260)]
    snap = ctypes.windll.kernel32.CreateToolhelp32Snapshot(2, 0)   # TH32CS_SNAPPROCESS
    out: list[int] = []
    if snap == -1:
        return out
    try:
        entry = PE32()
        entry.dwSize = ctypes.sizeof(PE32)
        ok = ctypes.windll.kernel32.Process32First(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.decode('gbk', 'replace').lower() in PROCESS_NAMES:
                out.append(int(entry.th32ProcessID))
            ok = ctypes.windll.kernel32.Process32Next(snap, ctypes.byref(entry))
    finally:
        ctypes.windll.kernel32.CloseHandle(snap)
    return sorted(out)


def stamp(path: Path) -> tuple[str, int]:
    st = path.stat()
    return (dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds'), st.st_size)


# ---------------------------------------------------------------- 起与收（有洞，测试里硬挡）

def desktop_ok() -> tuple[bool, str]:
    """能不能用鼠标/抢前台/发真实按键 —— **判据只在一份里**（`app/tdx_client/win32.py:578`）。

    这一条流程本身不点任何按钮，但"没人能登录的桌面"上把客户端起来只会多一个卡在登录框的窗口，
    所以照样先判。读不到就当不可用（宁可跳过一轮，也不要留下一个没人收的窗口）。
    """
    try:
        from app.tdx_client import win32
        return win32.input_desktop_ok()
    except Exception as exc:  # noqa: BLE001
        return False, f'桌面判据读不出来：{type(exc).__name__}: {str(exc)[:80]}'


def spawn(pid_watch: set[int]) -> int | None:
    """启动同花顺主程序，返回新进程 pid（已在跑就返回 None）。

    ⚠️ 测试进程里硬挡：`PYTEST_CURRENT_TEST` 存在就直接返回 None，不起真进程 ——
    这条链路碰的是用户桌面上的窗口，一旦在单测里真跑起来，"绿灯"和"点掉了什么"是两回事。
    """
    if os.environ.get('PYTEST_CURRENT_TEST'):
        print('（pytest 进程：不启动同花顺，只走判据）')
        return None
    path = exe_path()
    if path is None:
        return None
    p = subprocess.Popen([str(path)], cwd=str(path.parent), close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid_watch.add(p.pid)
    return p.pid


def close_pids(pids: set[int], *, settle_sec: int = 20) -> dict:
    """只收本次自己起的 pid，而且**只有真的探不到了才算关掉**。

    死规矩：绝不按镜像名杀（`/IM`）—— 那会把用户正开着的同一个客户端一起带走，
    09-30 的通达信那条链就是靠这条规矩避免误伤。

    10-08 第一版在这里犯过两个错（同花顺 15:30 起了却没关掉，就是这么来的）：
      · 发完 `taskkill /PID x /T` 就无条件把 pid 从待观察名单里删掉，于是 20 秒后
        那档 `/F` **永远没有机会执行**；
      · `closed = pids - alive - forced` 在 alive 恒空的情况下把"没关掉"写成"已关闭"
        —— 台账记 ok，界面上说关了，桌面上还开着。这是最坏的一种错：假成功。
    现在的顺序：优雅请求 → 轮询确认真退了 → 没退的才 `/F` → 还没退的如实交 `left`，
    由调用方出声（`need_human`），不许再算成 closed。
    """
    wanted = set(pids)
    if os.environ.get('PYTEST_CURRENT_TEST'):
        return {'closed': [], 'forced': [], 'left': sorted(wanted), 'skipped': sorted(wanted)}

    def _still_alive(candidates: set[int]) -> set[int]:
        return candidates & set(running_pids())

    for pid in sorted(wanted):
        try:
            subprocess.run(['taskkill', '/PID', str(pid), '/T'], capture_output=True,
                           text=True, timeout=30, encoding='utf-8', errors='replace')
        except Exception:  # noqa: BLE001  发不出去也要靠下面的"还在不在"判，不看这条的输出
            pass
    alive = _still_alive(wanted)
    deadline = time.time() + settle_sec
    while alive and time.time() < deadline:
        time.sleep(2)
        alive = _still_alive(alive)

    forced = set()
    for pid in sorted(alive):
        try:
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True,
                           text=True, timeout=30, encoding='utf-8', errors='replace')
            forced.add(pid)
        except Exception:  # noqa: BLE001
            pass
    left = _still_alive(alive) if forced else set()
    if forced:                        # /F 之后给它两秒把窗口收干净，再判"还在不在"
        time.sleep(2)
        left = _still_alive(alive)
    return {'closed': sorted(wanted - left), 'forced': sorted(forced), 'left': sorted(left)}


# ---------------------------------------------------------------- 一轮

LOGIN_MARKS = ('登录', '密码', '验证码', '帐号', '账号')


def top_windows(pid: int) -> list[str]:
    """该 pid 的可见顶层窗口标题（**只读**，不发消息也不移动）。"""
    if os.name != 'nt' or not pid:
        return []
    user32 = ctypes.windll.user32
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    titles: list[str] = []

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        got = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
        if got.value != pid:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value.strip():
            titles.append(buf.value.strip())
        return True

    user32.EnumWindows(EnumProc(_cb), 0)
    return titles


def looks_like_login_wall(titles: list[str]) -> str | None:
    """窗口标题里能认出"停在登录框"的那些字样就报出来 —— 这一步不猜、不代填密码，
    只把'需要人工登录一次'说清楚并把自己起的窗口收掉。"""
    for t in titles:
        for mark in LOGIN_MARKS:
            if mark in t:
                return t
    return None


def run_once(*, wait_sec: int = WAIT_DEFAULT_SEC, do_close: bool = True,
             check_only: bool = False) -> dict[str, Any]:
    exe, ini = exe_path(), ini_path()
    if exe is None or ini is None:
        return {'ok': False, 'rc': 2,
                'reason': f'前置不满足：客户端={exe}，ini={ini}（检查 FT_THS_DIR 与用户目录）'}
    before = stamp(ini)
    already = running_pids()
    out: dict[str, Any] = {'exe': str(exe), 'ini': str(ini), 'before': before,
                           'already_running': already, 'launched': None, 'after': None,
                           'waited_sec': 0, 'closed': []}
    if check_only:
        out.update(ok=True, rc=0, reason='只探测：路径与进程都在，未动界面')
        return out

    spawned: set[int] = set()
    if not already:
        if os.environ.get('PYTEST_CURRENT_TEST'):
            out.update(ok=False, rc=3, skipped=True,
                       reason='测试进程不启动客户端（硬挡），只验判据')
            return out
        new = spawn(spawned)
        if new is None:
            out.update(ok=False, rc=2, reason='启动失败（进程起不来）')
            return out
        out['launched'] = new
        time.sleep(12)          # 主窗口 + 自动登录那一段，给 12 秒再开始看文件
        titles = top_windows(new)
        out['windows'] = titles[:6]
        wall = looks_like_login_wall(titles)
        if wall or not titles:
            # 要么停在登录框（我们不代填密码），要么窗口根本没出来（起不来/被安全软件拦）。
            # 两种都把本次起的进程收掉再出声 —— 留一个没人认领的窗口是最坏结果。
            res = close_pids(spawned)
            base = (f'停在登录框「{wall}」：需要人工登录一次让客户端记住，再开这条循环'
                    if wall else '启动后 12 秒没有任何可见窗口：起不来或被拦')
            out.update(ok=False, rc=2, need_human=True, closed=res.get('closed', []),
                       forced=res.get('forced', []), left=res.get('left', []),
                       reason=base + (f'；而且这次起的窗口没关掉（pid {"、".join(res.get("left") or [])}）'
                                      if res.get('left') else ''))
            return out

    t0 = time.time()
    changed = False
    while time.time() - t0 < wait_sec:
        cur = stamp(ini)
        out['after'] = cur
        out['waited_sec'] = round(time.time() - t0)
        if cur != before:
            changed = True
            break
        time.sleep(POLL_SEC)
    out['changed'] = changed

    if not already and do_close and spawned:
        out.update(close_result := close_pids(spawned))
        out['closed'] = close_result.get('closed', [])

    if changed:
        out.update(ok=True, rc=0, reason=f'stockblock.ini 已更新（等 {out["waited_sec"]}s）')
    else:
        # 没变≠失败：可能客户端本来就同步好了、也可能云端这段时间没人改过分组。
        # 记成 skipped 而不是 fail —— 否则休市/没人改的每一天都会刷一笔假故障。
        out.update(ok=False, skipped=True, rc=3,
                   reason=f'等了 {out["waited_sec"]}s 没看到 ini 变化（不当成失败）')

    # **没关掉不能说成功**。10-08 那次就是这里没判：进程还开在桌面上，台账却记 ok。
    left = out.get('left') or []
    if left:
        out.update(ok=False, rc=2, need_human=True,
                   reason=f'数已同步但本次起的客户端没关掉（还开着 pid {"、".join(str(p) for p in left)}）'
                          f'：需要人工收一下，或查 taskkill 为什么没生效')
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='同花顺自选云端同步：启动 → 等待 ini 变新 → 关闭')
    ap.add_argument('--wait', type=int, default=WAIT_DEFAULT_SEC, help=f'等 ini 变新的上限秒数（默认 {WAIT_DEFAULT_SEC}）')
    ap.add_argument('--no-close', action='store_true', help='等完不收窗口（第一次演练建议加上）')
    ap.add_argument('--check', action='store_true', help='只探路径与进程，不动界面')
    args = ap.parse_args(argv)

    res = run_once(wait_sec=args.wait, do_close=not args.no_close, check_only=args.check)
    print(f'=== 同花顺云端同步（{"只探测" if args.check else "真跑"}）===')
    print(f'  客户端 {res.get("exe")}')
    print(f'  ini    {res.get("ini")}  起前={res.get("before")}  起后={res.get("after")}')
    print(f'  已在跑={res.get("already_running")}  本次启动={res.get("launched")}  等待 {res.get("waited_sec")}s'
          f'  关闭={res.get("closed")}')
    print(('  ✅ ' if res.get('ok') else '  ⏸ ' if res.get('skipped') else '  ⚠️ ') + str(res.get('reason')))
    try:
        from duanxian import jobs
        jobs.record('ths_sync', ok=bool(res.get('ok')),
                    detail={'reason': res.get('reason'), 'skipped': res.get('skipped'),
                            'changed': res.get('changed'), 'ini': res.get('ini'),
                            # 用户点名要的三格读数：ini 前后时间戳、等了多久、窗口收没收掉。
                            # 少了这些只能像 10-08 那样反过来查进程列表才知道"没关"。
                            'before': res.get('before'), 'after': res.get('after'),
                            'waited_sec': res.get('waited_sec'),
                            'already_running': res.get('already_running'),
                            'launched': res.get('launched'),
                            'closed': res.get('closed'), 'forced': res.get('forced'),
                            'left': res.get('left')})
    except Exception as exc:  # noqa: BLE001  记账坏了不许带崩作业
        print(f'  ⚠️ 记账失败：{type(exc).__name__}: {exc}')
    return int(res.get('rc') or 1)


if __name__ == '__main__':
    raise SystemExit(main())
