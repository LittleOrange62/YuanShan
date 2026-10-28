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


def snapshot() -> list[tuple[str, int]]:
    """当前还活着的相关进程 `[(镜像名, pid)]`。"""
    hits: list[tuple[str, int]] = []
    for name in EXACT_NAMES:
        hits += [(name, p) for p in win32.pids_by_name(name)]
    for pre in PREFIXES:
        hits += win32.pids_by_prefix(pre)
    return hits


def kill_all(*, log=None) -> tuple[bool, str]:
    """
    杀掉所有相关进程并**等到它们真的消失**。

    返回 `(清干净了吗, 说明)`。清不干净不抛异常 —— 调用方（重试循环）
    要能把这件事记下来继续往下判断，而不是在这里断掉。
    """
    def say(msg: str) -> None:
        if log:
            log(msg)

    before = snapshot()
    if not before:
        return True, '本来就没有通达信相关进程在跑'
    say(f'  要清掉的进程：{", ".join(f"{n}({p})" for n, p in before)}')

    names = {n for n, _ in before}
    for n in sorted(names):
        ok = win32.kill(n)
        say(f'  taskkill /F /IM {n} → {"成功" if ok else "失败（可能已退出）"}')

    deadline = time.monotonic() + GONE_TIMEOUT
    while True:
        left = snapshot()
        if not left:
            time.sleep(SETTLE)
            return True, f'已清掉 {len(before)} 个进程（{", ".join(sorted(names))}）'
        if time.monotonic() >= deadline:
            return False, (f'等了 {GONE_TIMEOUT:.0f}s 还有进程没退：'
                           f'{", ".join(f"{n}({p})" for n, p in left)} —— '
                           f'通常是权限不够（换管理员跑）或进程已经僵死')
        time.sleep(GONE_INTERVAL)


def main() -> int:
    """`python -m app.tdx_client.reset` —— 手动清一次。"""
    ok, why = kill_all(log=lambda m: print(m, flush=True))
    print(why)
    return 0 if ok else 1


if __name__ == '__main__':
    import sys

    sys.exit(main())
