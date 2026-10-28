# 注册本项目数据库备份的 Windows 计划任务（需管理员 PowerShell 运行）
#
#   .\scripts\ops\setup_backup_task.ps1              # 注册/更新三个任务
#   .\scripts\ops\setup_backup_task.ps1 -Remove      # 删除
#
# 为什么用计划任务而不是常驻进程：备份是每天跑一次的低频作业，
# 常驻调度器一旦崩了就静默不备份；计划任务由系统负责重试和记录。
# 校验（恢复到临时库比对行数）由 app/ops/backup.py 自己做，这里不重复。
#
# 任务名前缀用 YuanShan-：本项目是唯一写库方。旧项目那条
# FinanceTool-DailyCloseSync 一直在每个交易日 16:05 真跑并写 PG（2026-09-25 实测
# c18/c19/c20 三步 OK），已 Disable-ScheduledTask 停用 —— 别复用它的名字，也别顺手 Enable。

param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$Root   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # scripts\ops -> 项目根
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = (Get-Command python).Source }

$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# 只保留三档：日/周/月。时间避开 PG 白天的 ETL 窗口。
$Tasks = @(
    @{ Name = 'YuanShan-Backup-Daily';   Kind = 'daily';   Trigger = (New-ScheduledTaskTrigger -Daily -At 22:30) },
    @{ Name = 'YuanShan-Backup-Weekly';  Kind = 'weekly';  Trigger = (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 23:00) },
    @{ Name = 'YuanShan-Backup-Monthly'; Kind = 'monthly'; Trigger = (New-ScheduledTaskTrigger -Weekly -WeeksInterval 4 -DaysOfWeek Saturday -At 23:30) }
)

if ($Remove) {
    foreach ($t in $Tasks) {
        if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
            Write-Host "已删除 $($t.Name)"
        }
    }
    exit 0
}

foreach ($t in $Tasks) {
    $log = Join-Path $LogDir "backup_$($t.Kind).log"
    # 用 cmd /c 重定向，把 stdout/stderr 都留在日志里 —— 计划任务本身只记退出码，
    # 光看退出码没法知道是哪张表对不上。
    $cmd = "/c `"`"$Python`" -m app.ops.backup $($t.Kind) >> `"$log`" 2>&1`""
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $cmd -WorkingDirectory $Root
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
        -MultipleInstances IgnoreNew
    # SYSTEM 账户：不依赖某个用户是否登录。PG 密码来自 .env，不进任务定义。
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $t.Trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "已注册 $($t.Name)  ->  $log"
}

Write-Host ''
Write-Host '验证：Get-ScheduledTask -TaskName YuanShan-Backup-*'
Write-Host '手动试跑：Start-ScheduledTask -TaskName YuanShan-Backup-Daily'
Write-Host '看状态：python -m app.ops.backup status'
