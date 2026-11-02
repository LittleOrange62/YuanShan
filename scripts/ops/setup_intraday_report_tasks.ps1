# YuanShan 盘中播报（竞价 + 热度）：按配置重注册这两条 Windows 计划任务。
#
# 2026-11-02 起这份脚本**不再持有任务定义**：时点、渠道、开关与 XML 全部由
# `app/intraday/tasks.py` 从作业配置（`jobs/config.json` 的 `intraday_broadcast`）生成。
# 原来这里写死两份 XML —— 界面上改了时点，这里再跑一次就悄悄把改动覆盖回去
# （两处各写一份迟早打架，正是本项目明令禁止的那种假生效）。
#
# 改节奏（推荐走界面：系统配置 →「盘中播报」）：
#     .venv\Scripts\python.exe -m app.intraday.tasks --set heat_times=10:05,14:18 --apply
# 看现状（只读）：
#     .venv\Scripts\python.exe -m app.intraday.tasks --show
# 关掉某一路（注销它的任务，判据代码留在仓库）：
#     .venv\Scripts\python.exe -m app.intraday.tasks --set heat_enabled=false --apply
# 四条一起对齐：去掉 --only 即可（或跑 setup_intraday_watch_task.ps1 的同款调用）。
param([switch]$Remove, [string]$Which = 'heat')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py = Join-Path $root '.venv\Scripts\python.exe'
if ($Remove) {
    & $py -X utf8 -m app.intraday.tasks --set "${Which}_enabled=false" --apply --only $Which
} else {
    & $py -X utf8 -m app.intraday.tasks --apply --only auction,heat
}
exit $LASTEXITCODE
