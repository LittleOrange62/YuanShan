# -*- coding: utf-8 -*-
"""按**进程树**收掉一个子作业。唯一一份，给两条会起子作业的链用
（收盘即时链 `ingest/jobs/daily_close_sync.py` 与追平加工链 `ingest/jobs/e1_fill_all.py`）。

为什么要有这个模块而不是各写一行 `proc.kill()`：

- `subprocess.run(timeout=)` 到点只杀**它自己起的那个进程**。链上是三层
  （宿主 → 段脚本 → `c1/c3/c16/p0/run_agent`），只杀中间那层，最里层会变成孤儿继续跑
  —— 表现是"链断了但通达信还在动"，那天到底补没补上没人说得清。
- ⚠️ **绝不按映像名杀**（`taskkill /F /IM TdxW.exe`）。参考仓库那条
  `FinanceTool-DailyCloseSync` 计划任务就是这么写的：它把用户桌面上正开着的客户端一起
  干掉，自己退出码 1 却已经写了库，出问题没人知道。`/PID <pid> /T` 精确到"我起的这一支"。
"""
from __future__ import annotations

import os
import signal
import subprocess


def kill_tree(pid: int) -> str:
    """杀掉 `pid` 及其所有子孙。**返回一句人话**，调用方必须把它打进输出 ——
    收口这件事静默做了，和没做一样（下一步的判据会把"被自己掐死"当成"作业没成"）。"""
    if not pid or pid <= 0:
        return '没有 pid，什么都没收'
    if os.name == 'nt':
        r = subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                           capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        note = (r.stdout or r.stderr or '').strip().replace('\n', ' ')[:160]
        return 'taskkill /PID %s /T /F → 退出码 %s｜%s' % (pid, r.returncode, note)
    # 非 Windows：只能保证杀到这一层。POSIX 下要连子孙一起收，起子进程时必须带
    # `start_new_session=True` 再 `killpg`；本项目只在这台 Windows 机上跑链，
    # 这里照实说明收到了谁，不假装收到了整棵树。
    try:
        os.kill(pid, signal.SIGKILL)
        return 'SIGKILL %s（非 Windows：只保证收到这一层，子孙可能仍在跑）' % pid
    except OSError as exc:
        return 'SIGKILL %s 失败：%s' % (pid, exc)
