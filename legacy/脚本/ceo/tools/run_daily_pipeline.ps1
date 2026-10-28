# run_daily_pipeline.ps1
# ------------------------------------------------------------
# 全链路数据抓取、补齐、校验、评分卡生成、飞书推送
# 采用方案 B：tq 为主、data_from 为补，若全部失效则硬补重点股
# ------------------------------------------------------------

$logDir = "$PSScriptRoot\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

function Log([string]$msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -FilePath "$logDir\pipeline_$(Get-Date -Format 'yyyyMMdd').log" -Encoding utf8 -Append
    Write-Host $msg
}

Log "=== DAILY PIPELINE START ==="

# ------------------------------------------------------------
# 1️⃣ 检查本地缺失（data_in）
# ------------------------------------------------------------
Log "Step 1: 检查本地缺失..."
python "$PSScriptRoot\scripts\data_in\verify_local.py" --mode check_missing > "$logDir\check_missing.log" 2>&1
if ($LASTEXITCODE -ne 0) { Log "❌ 检查脚本异常，退出"; exit 1 }
$missing = Get-Content "$logDir\check_missing.log" | ConvertFrom-Json
if ($missing.Count -eq 0) {
    Log "✅ 本地已完整，无需抓取"
    goto GenerateScore
}

# ------------------------------------------------------------
# 2️⃣ 尝试 tq 本地抓取（Priority 0）
# ------------------------------------------------------------
$symbols = $missing.Keys -join " "
$dates   = ($missing.Values | ForEach-Object { $_ }) -join " "
Log "Step 2: TQ 本地抓取（Priority 0） - symbols: $symbols"
python "$PSScriptRoot\scripts\tq_strategy\fetch_from_tdx.py" --symbols $symbols --dates $dates > "$logDir\tq_fetch.log" 2>&1
if ($LASTEXITCODE -eq 0) {
    Log "✅ TQ 抓取完成，重新检查缺失..."
    python "$PSScriptRoot\scripts\data_in\verify_local.py" --mode check_missing > "$logDir\check_missing_after_tq.log" 2>&1
    $missing = Get-Content "$logDir\check_missing_after_tq.log" | ConvertFrom-Json
    if ($missing.Count -eq 0) { Log "✅ TQ 已补齐全部缺失"; goto GenerateScore }
}
Log "⚠️ TQ 抓取未全成功，进入 data_from 补齐"

# ------------------------------------------------------------
# 3️⃣ data_from 分层补齐（P0 → P1 → P2）
# ------------------------------------------------------------
# 3.1 P0
Log "Step 3.1: data_from P0（Baostock/AKShare/EastMoney）"
python "$PSScriptRoot\scripts\data_from\fetch_p0.py" > "$logDir\p0_fetch.log" 2>&1
python "$PSScriptRoot\scripts\data_in\verify_local.py" --mode check_missing > "$logDir\check_missing_after_p0.log" 2>&1
$missing = Get-Content "$logDir\check_missing_after_p0.log" | ConvertFrom-Json
if ($missing.Count -eq 0) { Log "✅ P0 已补齐全部缺失"; goto GenerateScore }

# 3.2 P1
Log "Step 3.2: data_from P1（Tushare/Skills）"
python "$PSScriptRoot\scripts\data_from\fetch_p1.py" > "$logDir\p1_fetch.log" 2>&1
python "$PSScriptRoot\scripts\data_in\verify_local.py" --mode check_missing > "$logDir\check_missing_after_p1.log" 2>&1
$missing = Get-Content "$logDir\check_missing_after_p1.log" | ConvertFrom-Json
if ($missing.Count -eq 0) { Log "✅ P1 已补齐全部缺失"; goto GenerateScore }

# 3.3 P2
Log "Step 3.3: data_from P2（AlphaFeed/Tencent API）"
python "$PSScriptRoot\scripts\data_from\fetch_p2.py" > "$logDir\p2_fetch.log" 2>&1
python "$PSScriptRoot\scripts\data_in\verify_local.py" --mode check_missing > "$logDir\check_missing_after_p2.log" 2>&1
$missing = Get-Content "$logDir\check_missing_after_p2.log" | ConvertFrom-Json
if ($missing.Count -eq 0) { Log "✅ P2 已补齐全部缺失"; goto GenerateScore }

# ------------------------------------------------------------
# 4️⃣ 重点股专线（Priority 4）
# ------------------------------------------------------------
Log "Step 4: 重点股专线补齐（若前面均失败）"
python "$PSScriptRoot\scripts\data_from\fetch_priority.py" > "$logDir\priority_fetch.log" 2>&1
python "$PSScriptRoot\scripts\data_in\verify_local.py" --mode final_check > "$logDir\final_check.log" 2>&1
$missing = Get-Content "$logDir\final_check.log" | ConvertFrom-Json
if ($missing.Count -ne 0) {
    Log "❌ 仍有缺失，写入告警并飞书通知"
    $alert = $missing | ConvertTo-Json -Compress
    $alert | Out-File -FilePath "$logDir\alert_missing.txt" -Encoding utf8
    $msg = "⚠️ 数据缺失告警:`n$alert"
    # 使用已有的飞书推送脚本（支持读取 STDIN）
    $msg | & "$PSScriptRoot\manager\temp\push_feishu_now.sh"
    exit 1
}

# ------------------------------------------------------------
# 5️⃣ 生成评分卡
# ------------------------------------------------------------
:GenerateScore
Log "Step 5: 生成远山评分卡..."
python "$PSScriptRoot\scripts\data_in\daily_score_update.py" > "$logDir\score_update.log" 2>&1
if ($LASTEXITCODE -ne 0) { Log "❌ 评分卡生成失败，请检查日志"; exit 1 }
Log "✅ 评分卡生成完成"

# ------------------------------------------------------------
# 6️⃣ 飞书推送
# ------------------------------------------------------------
Log "Step 6: 推送至飞书群聊..."
& "$PSScriptRoot\manager\temp\push_feishu_now.sh"
if ($LASTEXITCODE -ne 0) { Log "⚠️ 飞书推送失败，请手动检查" } else { Log "✅ 飞书推送成功" }

Log "=== DAILY PIPELINE END ==="
exit 0
