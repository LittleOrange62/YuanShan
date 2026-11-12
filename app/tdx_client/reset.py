# -*- coding: utf-8 -*-
"""
「从头再来」——把通达信相关进程全部清干净。

**为什么需要这个而不是复用 s1 的重启逻辑。** s1 只管 `TdxW.exe`，而这条
链路真正会卡的是残留状态：

- 后台同步进程 `TPyth*` 还在跑时，新起的通达信会跟它抢 vipdoc 文件，
  表现是"下载完毕"了但 `.day` 时间戳没变；
- 通达信崩过一次之后（2026-08-31 用 `TCM_SETCURSEL` 崩过），进程可能只剩
  一个不响应消息的空壳，窗口枚举得到但 `SendMessage` 全部超时 ——
  这种状态下所有步骤都会以"控件没反应"失败，跟真实原因差很远；
- 「盘后数据下载」对话框如果开着且卡在下载中，重新走 s5 会 precheck
  成功直接跳过，然后 s6 在一个正在下载的对话框上再点一次开始。

所以重来之前必须把整棵进程树砍掉，让下一轮从"什么都没有"开始。

**这个模块不判断该不该重来，只负责清干净。** 什么时候重来是
`run_agent` 的 `--attempts` 循环决定的。
"""
from __future__ import annotations

import time

from app.tdx_client import locator, win32

#: 要清掉的进程。`TdxW.exe` 是主程序；`TPyth` 是盘后同步的后台进程
#: （名字带版本尾巴，所以走前缀匹配）。
EXACT_NAMES = ('TdxW.exe', 'TdxSP.exe', 'TdxNonSP.exe')
PREFIXES = (locator.SYNC_EXE_PREFIX,)

#: 等进程真的消失的上限。`taskkill /F` 返回成功不等于进程已经没了，
#: 立刻重启的话通达信会检测到"已有实例"然后静默退出 —— 表现是
#: "启动了但一直没窗口"，s1 会一直等到 90s 超时。
GONE_TIMEOUT = 25.0
GONE_INTERVAL = 0.5
#: 进程全没之后再等一下。通达信退出时要写 vipdoc 的索引文件，
#: 没写完就重启的话下一轮读到的是半截文件。
SETTLE = 2.0


def _hits() -> list[tuple[str, int]]:
    """进程表里列着的相关进程（**含残影**，活没活由 `_split` 判）。"""
    hits: list[tuple[str, int]] = []
    for name in EXACT_NAMES:
        hits += [(name, p) for p in win32.pids_by_name(name)]
    for pre in PREFIXES:
        hits += win32.pids_by_prefix(pre)
    return hits


def _split() -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """`(活着, 残影)`。一次线程快照判完所有镜像名，判据走 `win32.split_live_pids` 那一份。"""
    hits = _hits()
    live, _ghost = win32.split_live_pids([p for _n, p in hits],
                                         counts=win32.thread_counts())
    return ([h for h in hits if h[1] in live],
            [h for h in hits if h[1] not in live])


def snapshot() -> list[tuple[str, int]]:
    """当前**活着**的相关进程 `[(镜像名, pid)]`。

    残影条目（`tasklist` 还列着但 0 线程）不在里面：它已经死了，收不掉也等不掉。
    把它算进来的后果是 `kill_all` 永远等不到"全没了"，25s 后报「进程已经僵死」——
    2026-11-11 一整天的收盘链就是这么停的。残影要看得见，走 `ghosts()`。
    """
    return _split()[0]


def ghosts() -> list[tuple[str, int]]:
    """已经死了但条目还在的相关进程。**只用来记账与说破**，不参与"等它消失"。"""
    return _split()[1]


def kill_all(*, log=None) -> tuple[bool, str]:
    """
    收掉所有**活着的**相关进程并等到它们真的消失。

    返回 `(清干净了吗, 说明)`。清不干净不抛异常 —— 调用方（重试循环）
    要能把这件事记下来继续往下判断，而不是在这里断掉。

    按 pid 收（带 `/T` 子树），不按镜像名群杀：`/IM` 会把下一轮刚拉起的新实例一起收掉。
    """
    def say(msg: str) -> None:
        if log:
            log(msg)

    before = snapshot()
    ghosts_now = ghosts()
    if ghosts_now:
        say(f'  残影条目（0 线程、已死、收不掉也不等）：'
            f'{", ".join(f"{n}({p})" for n, p in ghosts_now)}')
    if not before:
        return True, ('本来就没有活着的通达信相关进程'
                      + (f'（另有残影条目 {len(ghosts_now)} 个，不影响启动）'
                         if ghosts_now else '在跑'))
    say(f'  要清掉的进程：{", ".join(f"{n}({p})" for n, p in before)}')

    names = {n for n, _ in before}
    killed = win32.kill_pids([p for _n, p in before])
    for n, p in before:
        say(f'  taskkill /F /T /PID {p}（{n}）→ '
            f'{"成功" if killed.get(p) else "失败（可能已退出）"}')

    deadline = time.monotonic() + GONE_TIMEOUT
    while True:
        left = snapshot()
        if not left:
            time.sleep(SETTLE)
            return True, f'已清掉 {len(before)} 个进程（{", ".join(sorted(names))}）'
        if time.monotonic() >= deadline:
            return False, (f'等了 {GONE_TIMEOUT:.0f}s 还有进程没退：'
                           f'{", ".join(f"{n}({p})" for n, p in left)} —— '
                           f'通常是权限不够（换管理员跑）或进程真的卡在内核里')
        time.sleep(GONE_INTERVAL)


def main() -> int:
    """`python -m app.tdx_client.reset` —— 手动清一次。"""
    ok, why = kill_all(log=lambda m: print(m, flush=True))
    print(why)
    return 0 if ok else 1


if __name__ == '__main__':
    import sys

    sys.exit(main())
