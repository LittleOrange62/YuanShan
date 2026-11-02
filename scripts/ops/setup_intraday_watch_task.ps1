# YuanShan-IntradayZtWatch（盘中涨跌停池事件差，每 5 分钟一针）：按配置重注册。
#
# 2026-11-02 起这份脚本**不再持有任务定义**：起始时刻、间隔、时长、渠道与 XML 全部由
# `app/intraday/tasks.py` 从作业配置（`jobs/config.json` 的 `intraday_broadcast`）生成。
#
# 为什么执行形态仍是「每日触发器 + 每 5 分钟重复的单次 --once」：外部 10-23(二) 那次
# watcher 常驻 loop 在 09:29 建完基线后静默死亡（无 traceback、无日志、任务计划程序还显示
# "运行中"），盘中 8 轮全瞎 —— 弃常驻之后，调度器本身就是看门狗，卡死由执行时限兜底。
# 原委记在 `ingest/jobs/zt_watch.py` 开头，别把它改回进程内循环。
#
# 改节奏（推荐走界面：系统配置 →「盘中播报」）：
#     .venv\Scripts\python.exe -m app.intraday.tasks --set watcher_interval_min=3 --apply
# 看现状（只读）：
#     .venv\Scripts\python.exe -m app.intraday.tasks --show
# 禁用（注销任务，判据代码留在仓库）：本脚本加 -Remove，或
#     .venv\Scripts\python.exe -m app.intraday.tasks --set watcher_enabled=false --apply
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py = Join-Path $root '.venv\Scripts\python.exe'
if ($Remove) {
    & $py -X utf8 -m app.intraday.tasks --set watcher_enabled=false --apply --only watcher
} else {
    & $py -X utf8 -m app.intraday.tasks --apply --only watcher
}
exit $LASTEXITCODE
