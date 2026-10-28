# 重启 qwenpaw-backend 并自检飞书通道（2026-08-13）
# 用法：powershell -ExecutionPolicy Bypass -File restart_backend_and_verify.ps1
$log = 'C:\Users\Administrator\.qwenpaw\restart_backend_log.txt'
function W($msg) { $ts = Get-Date -Format 'HH:mm:ss'; Write-Output ("[$ts] " + $msg) | Tee-Object -FilePath $log -Append }

W '=== backend restart begin ==='

# 1. 等 8 秒，让当前会话回复先渲染完
Start-Sleep -Seconds 8

# 2. 杀掉旧后端
$old = Get-Process -Name 'qwenpaw-backend' -ErrorAction SilentlyContinue
if ($old) {
    W ("killing old backend PID=" + ($old.Id -join ','))
    $old | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
} else {
    W 'no old backend found'
}

# 3. 等待新后端出现（桌面监督自动拉起，最多等 90 秒）
$newPid = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 3
    $p = Get-Process -Name 'qwenpaw-backend' -ErrorAction SilentlyContinue
    if ($p -and $p.Id -ne $old.Id) { $newPid = $p.Id; W ("new backend appeared PID=" + $newPid); break }
    if (-not $p) { W ("waiting for backend restart... " + ($i*3) + 's') }
}

# 4. 兜底：桌面没自动拉起就手动启动
if (-not $newPid) {
    W 'desktop did not auto-restart backend, starting manually...'
    Start-Process -FilePath 'D:\QwenPaw\QwenPaw Desktop\binaries\qwenpaw-backend\qwenpaw-backend.exe' -WorkingDirectory 'D:\QwenPaw\QwenPaw Desktop\binaries\qwenpaw-backend'
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 3
        $p = Get-Process -Name 'qwenpaw-backend' -ErrorAction SilentlyContinue
        if ($p) { $newPid = $p.Id; W ("manually started backend PID=" + $newPid); break }
    }
}

if (-not $newPid) { W 'FATAL: backend did not start'; exit 1 }

# 5. 等待后端 API 就绪（63162 端口）
$apiOk = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 3
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:63162/api/version' -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $apiOk = $true; W 'backend API ready (api/version 200)'; break }
    } catch {}
}
if (-not $apiOk) { W 'WARN: backend API not ready in time' }

# 6. 网络与飞书 API 自检
W ('DNS open.feishu.cn -> ' + ((Resolve-DnsName 'open.feishu.cn' -ErrorAction SilentlyContinue | Where-Object { $_.Type -eq 'A' } | Select-Object -First 3 -ExpandProperty IPAddress) -join ', '))
try {
    $token = Invoke-RestMethod -Uri 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal' -Method Post -ContentType 'application/json' -Body '{"app_id":"<REDACTED-LARK-APP-ID>","app_secret":"<REDACTED-LARK-APP-SECRET>"}' -TimeoutSec 10
    W ("feishu token API -> code=" + $token.code + " msg=" + $token.msg)
} catch { W ('feishu token API FAILED: ' + $_.Exception.Message) }

W '=== backend restart done ==='