# -*- coding: utf-8 -*-
"""
块 2 · 编排层。三个角色，分工是这份代码里最容易被混淆的地方：

| | 谁 | 回答的问题 | 有没有副作用 |
|---|---|---|---|
| 判据 | `schedule.py` | 该不该跑（纯函数，不接 DB） | 无 |
| 调度 | `run_scheduler.py` | 到点没有、依赖好了没 → 入队 | 只插 `operations.task` |
| 执行 | `runner.py` + `run_worker.py` | 怎么跑起来、跑成什么样 | 起子进程、写终态 |

`tradeday.py` 是判据的输入之一："现在该追到哪一天"。单独成模块是因为
`/jobs` 页面和 `e1_fill_all.py` 要用同一套判据，抄两份的后果是
页面上写着 08-28、脚本却按 08-27 跑。

设计文档见 `docs/09-调度器功能说明.md`。

**为什么不让 `config.job` 直接存命令行。**
最"积木"的做法是把 argv 存进表里，前台改表就能改命令 —— 但 `/blocks`
页可以编辑 `config.job`，那等于给了一个远程任意命令执行入口，跟旧系统
`/api/sql/execute` 是同一类洞。所以这里的做法是：**执行器白名单在代码里**
（`runner.RUNNERS`），`config.job.params.runner` 只能从白名单里选一个名字，
`params.args` 只接受受校验的标量。可配置性保留了，任意执行没有。
"""
from __future__ import annotations

from app.orchestrator import tradeday
from app.orchestrator.runner import (
    RUNNERS,
    cancel,
    job_runner,
    jobs,
    run_claimed_task,
    run_job,
    running_tasks,
    tasks,
)
from app.orchestrator.schedule import Decision, SlotState, decide

__all__ = ['RUNNERS', 'Decision', 'SlotState', 'cancel', 'decide', 'job_runner',
           'jobs', 'run_claimed_task', 'run_job', 'running_tasks', 'tasks',
           'tradeday']
