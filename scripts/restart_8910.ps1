# 本地重启 8910 的那一对动作（Stop 走 stop.py，起用 Start-Process 挂到独立进程）。
# 为什么要落成 .ps1：从 Git Bash 传 `powershell -Command "..."` 时，双引号里的
# `$env:X` 会被 **bash 先展开成空串**，PowerShell 根本看不到那条赋值 —— 于是
# uvicorn 起来了但 `ASTOCK_LAUNCH_ID` 是空的，`/api/astock/health` 回 launch_id:""，
# 体检与"这次是谁起的"就接不上。用文件跑没有这层展开。
$ErrorActionPreference = 'Stop'
$root = 'D:\finance\YuanShan'
$lid = 'cli-' + (Get-Date -Format 'yyyyMMddHHmmss')
$env:ASTOCK_LAUNCH_ID = $lid
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
New-Item -ItemType Directory -Force -Path (Join-Path $root '.local') | Out-Null
# 上一份日志先归档再启动。`-RedirectStandardOutput` 是**覆盖**，不是追加：
# 不归档就等于每次重启都把"上一次到底跑成什么样"的那份证据删掉 ——
# 2026-10-10 那次 tdx_watch 假绿（台账冻 18 小时、页面写"正常"），
# 证据只在 stdout 里，而它恰好断在我自己 13:25 的一次重启上。
# 归档名 `uvicorn.out.<LastWriteTime>.log` 与 `vr/logtail.py` 的白名单读取配套，
# `/api/system/log?name=uvicorn.out&run=<时间戳>` 能翻旧的那一次；只留最近 14 份。
function Save-LogTail([string]$name) {
  $live = Join-Path $root ".local\$name.log"
  if (-not (Test-Path $live)) { return }
  $item = Get-Item $live
  if ($item.Length -eq 0) { return }        # 空的没什么可留
  $stamp = $item.LastWriteTime.ToString('yyyyMMdd-HHmmss')
  Move-Item -Path $live -Destination (Join-Path $root ".local\$name.$stamp.log") -Force
  Get-ChildItem -Path (Join-Path $root '.local') -Filter "$name.2*.log" |
    Sort-Object LastWriteTime -Descending | Select-Object -Skip 14 | Remove-Item -Force -ErrorAction SilentlyContinue
}
Save-LogTail 'uvicorn.out'
Save-LogTail 'uvicorn.err'
# `-u` = stdout **不缓冲**。日志是重定向进 .local/uvicorn.out.log 的，没有这个参数就是块缓冲：
# 10-08 实测文件停在 14:37，而 15:59~17:21 那条链的进度全压在缓冲区里，
# "今天到底跑到哪一步"只能靠 state.json + 逐步台账 + 进程表反推（取证成本全在这）。
Start-Process -FilePath (Join-Path $root '.venv\Scripts\python.exe') `
  -ArgumentList '-u','-X','utf8','-m','uvicorn','server:app','--host','0.0.0.0','--port','8910','--no-access-log' `
  -WorkingDirectory $root -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $root '.local\uvicorn.out.log') `
  -RedirectStandardError (Join-Path $root '.local\uvicorn.err.log')
Set-Content -Path (Join-Path $root '.local\uvicorn.launch_id') -Value $lid -Encoding ascii
Write-Output "launch_id=$lid"
