# -*- coding: utf-8 -*-
"""推送这一族的作业脚本（按界面上的序号命名，一号一条任务）。

为什么单独一个包、还用 `tNN_` 前缀：这九条的命令行写的是**模块路径本身**
（`python -m ingest.jobs.push.t03_zt_delta --send`，存在 Windows 计划任务里），
所以"序号 = 界面上的那一行"必须落到文件名上才不会走样：屏幕上从上往下数的第几行，
就是这里第几个文件，改哪一行去找哪个文件不需要查表。
`python` 的包名/模块名不能以数字开头，故前缀是 `t`。

分工不动：判据与正文在各作业文件与 `app/intraday/templates.json`，
**什么时候跑、发到哪、谁在跑**只在 `app/intraday/tasks.py`（一份 `SPEC` 在册表）；
配置真源是作业配置 `jobs/config.json` 的 `intraday_broadcast`。
取数落库与到点推送分两层：`t01→t03`（事件差 → 变化量）、`t06→t07`（体系榜落库 → 推送）。
"""
