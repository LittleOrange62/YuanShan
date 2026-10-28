# 注册本项目"每日收盘同步"的 Windows 计划任务。
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\ops\setup_daily_sync_task.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\ops\setup_daily_sync_task.ps1 -Remove
#   Custom time:  -AtTime '16:30'
#
# Why a scheduled task (not the resident scheduler): same reason as the backup
# task -- a resident process that dies silently stops running; the OS task
# scheduler retries and logs. See setup_backup_task.ps1.
#
# KEY DIFFERENCE from the backup task: backup runs as SYSTEM (no desktop).
# The daily sync's first step is Tongdaxin GUI post-close download and the TQ
# steps need the logged-in client -- BOTH require an interactive desktop.
# So this task runs as the CURRENT USER with Interactive logon (only when the
# user is logged on).
#
# WARNING: at trigger time (default weekdays 16:05) this machine must be
# logged in AND unlocked, or the GUI step is refused by run_agent's session
# self-check and that day's sync fails (logged). Non-trading days are skipped
# by the script's own guard, so it won't launch Tongdaxin needlessly.

param(
    [switch]$Remove,
    [string]$AtTime = '16:05'
)

$ErrorActionPreference = 'Stop'
$Root   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # scripts\ops -> 项目根
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = (Get-Command python).Source }
$TaskName = 'YuanShan-DailyCloseSync'
$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed $TaskName"
    }
    exit 0
}

$log = Join-Path $LogDir 'daily_sync.log'
# Trading-day guard lives in the python script; the task just fires every weekday.
# 入口用 `-m ingest.jobs.daily_close_sync`：本项目的作业全部是 ingest/jobs/ 下的模块，
# 没有 scripts\ops\daily_close_sync.py 这个文件（那是旧项目的落点，照抄会 100% 起不来）。
$cmd = "/c `"`"$Python`" -m ingest.jobs.daily_close_sync >> `"$log`" 2>&1`""
$action  = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $cmd -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $AtTime
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -MultipleInstances IgnoreNew
# Current user + Interactive: only runs in the user's logged-on desktop session,
# which is what GUI automation needs.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description 'YuanShan daily post-close: TDX download -> ingest -> compute -> TQ enrich -> verify -> close. Needs logged-in unlocked desktop.' | Out-Null

Write-Host "Registered $TaskName  ->  weekdays $AtTime  ->  $log"
Write-Host ''
Write-Host "Verify:   Get-ScheduledTask -TaskName $TaskName"
Write-Host "Test run: Start-ScheduledTask -TaskName $TaskName"
Write-Host "Log:      Get-Content -Tail 40 $log"
