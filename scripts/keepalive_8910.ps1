# YuanShan 8910 看门狗（计划任务每 5 分钟跑一次，也支持 AtLogon）
#
# ⚠️ 计划任务的动作不是这个文件，是 `scripts\keepalive_8910.vbs`（10-12 加的隐藏启动器）：
# 任务直接起 powershell.exe 时，系统**先建控制台窗口**、PowerShell 才读到 -WindowStyle Hidden，
# 于是每 5 分钟在屏幕上闪一下黑窗。要把任务指回这个 ps1，先看 12 §六 的回退那一段。
#
# 为什么要有它：这台服务**没有**任何自启动 —— 没注册服务、注册表 Run 键也没写，
# 唯一入口是用户双击「启动 YuanShan.cmd」。所以机器重启、进程被误关、崩掉之后，
# 15:59 那条收盘链不会自己回来，而界面上什么都看不出来（界面本身也在这个进程里）。
#
# ⚠️ 一条硬规矩：**只在"端口上没有任何监听进程"时才动手拉起**。
# 健康检查失败但端口有人监听 ⇒ 只写告警，绝不 kill。理由很具体：
# 15:59 的收盘链就跑在这个进程里，机器忙的时候 /api/health 会慢到超时，
# 看门狗如果据此杀进程，就是把正在下载的通达信、正在算的加工段一起杀掉 ——
# 这正是本项目已经踩过的"链的耐心短于下载"那一类错，只是这次凶手变成看护本身。
# 第二个后端本来也起不来（复盘 Agent 数据目录锁会让它自己退），天然互斥，不用再加锁。

$ErrorActionPreference = 'SilentlyContinue'
$root = 'D:\finance\YuanShan'
$log  = Join-Path $root '.local\keepalive.log'
# 端口默认 8910，可用环境变量改：这是为了能把"有人监听但不健康"那条危险分支
# 单独测一遍（例如指到 5432 —— 那里有监听、又不是 HTTP），不用真把服务停掉。
$port = if ($env:YSH_KEEPALIVE_PORT) { [int]$env:YSH_KEEPALIVE_PORT } else { 8910 }

function Stamp { (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') }
function Note($line) {
    Add-Content -Path $log -Value ("{0}  {1}" -f (Stamp), $line) -Encoding utf8
    # 日志只留最近 400 行：看护脚本最坏的一种坏法是把自己写成几百 MB
    $lines = @(Get-Content $log -Encoding utf8)
    if ($lines.Count -gt 400) { Set-Content -Path $log -Value $lines[-400..-1] -Encoding utf8 }
}

# 1) 健康：200 就算活着，直接退出（绝大多数轮次都走这条）
$ok = $false
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 8 -UseBasicParsing
    $ok = ($r.StatusCode -eq 200)
} catch { $ok = $false }
if ($ok) { exit 0 }

# 2) 判不了健康，先看端口上有没有人
$listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen | Select-Object -Expand OwningProcess)
if ($listeners.Count -gt 0) {
    # 有进程但不健康：可能是正在跑重活（收盘链、全量重算），也可能是假死。
    # 这里**只出声不动手**，把决定权留给人 —— 自动杀 = 自动制造半程状态。
    Note ("健康检查失败但端口有人监听 pid={0}：不动手（怕打断正在跑的链），需要人工看一眼 /system" -f ($listeners -join ','))
    exit 0
}

# 3) 真没人监听 —— 距上一次拉起不到 3 分钟就跳过，避免连续几轮叠加
$idFile = Join-Path $root '.local\uvicorn.launch_id'
if (Test-Path $idFile) {
    $last = [IO.File]::ReadAllText($idFile).Trim()
    if ($last -match 'cli-(\d{8})(\d{6})') {
        try {
            $t = [DateTime]::ParseExact(("{0}{1}" -f $Matches[1], $Matches[2]), 'yyyyMMddHHmmss', $null)
            if ((Get-Date) - $t -lt [TimeSpan]::FromMinutes(3)) {
                Note ("端口无人监听，但 {0} 分钟前刚拉起过（{1}）：再等一轮" -f [int]((Get-Date)-$t).TotalMinutes, $last)
                exit 0
            }
        } catch { }
    }
}

Note '端口无监听且超过 3 分钟没起过：拉起'
# ⚠️ 用 Start-Process 起"启动脚本"，不要用 `&`：`&` 会让被起的 powershell 继承本进程的
# stdout 句柄，而那个句柄又往下传给 uvicorn —— 结果一次 tick 会挂好几分钟不退出，
# 计划任务的"每 5 分钟一轮"就变成叠轮（10-08 13:10 实测：拉起动作 13:10:31 就完成了，
# 脚本自己却到 13:16:18 才写出下一行）。Start-Process 是独立进程，不继承句柄。
Start-Process -FilePath 'powershell.exe' `
  -ArgumentList '-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass',
               '-File', (Join-Path $root 'scripts\restart_8910.ps1') `
  -WorkingDirectory $root -WindowStyle Hidden
Note '已交给 restart_8910.ps1 拉起，下面轮询复查'

# 复查：起服务要过导入、体检、复盘 Agent 装配那一段，十几秒不够；最多等 90 秒，
# 只把**最终结论**写一行。等不到就说"需要人工"，不自己下结论说死了。
$confirmed = $false
for ($i = 0; $i -lt 6; $i++) {
    Start-Sleep -Seconds 15
    try {
        $r2 = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 10 -UseBasicParsing
        if ($r2.StatusCode -eq 200) {
            Note ("复查通了（第 {0} 次，约 {1} 秒）：health=200" -f ($i + 1), (($i + 1) * 15))
            $confirmed = $true
            break
        }
    } catch { }
}
if (-not $confirmed) { Note '复查 90 秒仍不通：需要人工启动（双击「启动 YuanShan.cmd」）' }
exit 0
