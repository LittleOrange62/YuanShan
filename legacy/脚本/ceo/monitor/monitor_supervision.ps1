# ceo 长时间任务监督循环脚本
# 用途：每 N 分钟检查一次后台任务/员工产出，直到完成或超时，全程写日志
# 用法：powershell -ExecutionPolicy Bypass -File monitor_supervision.ps1 -TaskId "xxx" -IntervalMin 5 -TimeoutMin 120 -LogFile "monitor_log.txt"
# 说明：适合 ceo 在会话中无法持续在线时，用本脚本代跑监督；也可由 ceo 每轮调用一次作为"心跳"。

param(
    [string]$TaskId = "",
    [int]$IntervalMin = 5,
    [int]$TimeoutMin = 120,
    [string]$LogFile = "supervision_log.txt",
    [string]$WatchDir = "C:\Users\Administrator\.qwenpaw\workspaces\data_in",
    [string]$DoneMarker = ""   # 可选：出现该文件名即视为完成
)

$start = Get-Date
$deadline = $start.AddMinutes($TimeoutMin)
$loop = 0

"=== 监督循环启动 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | Task=$TaskId | 间隔=${IntervalMin}min | 超时=${TimeoutMin}min ===" | Tee-Object -FilePath $LogFile -Append

while ((Get-Date) -lt $deadline) {
    $loop++
    $now = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $stamp = "[$now] 第${loop}轮检查"

    # 1) 看监控目录最近是否有新产出
    $recent = @()
    if (Test-Path $WatchDir) {
        $recent = Get-ChildItem -Path $WatchDir -Recurse -File -ErrorAction SilentlyContinue |
                 Where-Object { $_.LastWriteTime -gt (Get-Date).AddMinutes(-$IntervalMin) } |
                 Sort-Object LastWriteTime -Descending | Select-Object -First 5
    }
    if ($recent.Count -gt 0) {
        $files = ($recent | ForEach-Object { $_.FullName.Replace($WatchDir, '~') + " @" + $_.LastWriteTime.ToString('HH:mm:ss') }) -join " | "
        "$stamp | 最近产出: $files" | Tee-Object -FilePath $LogFile -Append
    } else {
        "$stamp | 最近无新产出" | Tee-Object -FilePath $LogFile -Append
    }

    # 2) 完成标记检查
    if ($DoneMarker -ne "" -and (Test-Path $DoneMarker)) {
        "$stamp | 检测到完成标记: $DoneMarker -> 任务完成" | Tee-Object -FilePath $LogFile -Append
        exit 0
    }

    # 3) 循环等待
    Start-Sleep -Seconds ($IntervalMin * 60)
}

"=== 监督超时 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | 共${loop}轮，未检测到完成 ===" | Tee-Object -FilePath $LogFile -Append
exit 1
