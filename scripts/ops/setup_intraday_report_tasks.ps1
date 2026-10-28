# 注册/覆盖 竞价速览 + 概念热度 两个单次短跑任务。改节奏后重跑本脚本即可。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py   = Join-Path $root '.venv\Scripts\python.exe'
$userId = whoami
function New-TaskXml($uri, $desc, $triggers, $module, $extraArgs) {
    $argsLine = "-X utf8 -m $module --send --channel feishu-main $extraArgs"
    $t = ($triggers | ForEach-Object { @"
    <CalendarTrigger>
      <StartBoundary>$_</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
"@ }) -join "`n"
    return @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>$desc</Description><URI>\$uri</URI></RegistrationInfo>
  <Principals><Principal id="Author"><UserId>$userId</UserId><LogonType>S4U</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT2M</Interval><Count>2</Count></RestartOnFailure>
    <IdleSettings><StopOnIdleEnd>true</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
$t
  </Triggers>
  <Actions Context="Author"><Exec><Command>$py</Command><Arguments>$argsLine</Arguments><WorkingDirectory>$root</WorkingDirectory></Exec></Actions>
</Task>
"@
}
$auction = New-TaskXml 'YuanShan-AuctionReport' 'YuanShan: 交易日09:25:30 竞价双榜+一字+概念计数 -> analytics.auction_board/intraday_concept_count + 盯盘群; 非交易日静默' @('2026-10-27T09:25:30') 'ingest.jobs.auction_report' ''
Register-ScheduledTask -TaskName 'YuanShan-AuctionReport' -Xml $auction -Force | Out-Null
$heat = New-TaskXml 'YuanShan-HeatReport' 'YuanShan: 交易日10:05/10:55/14:18 涨跌停池概念计数(>=3) -> intraday_concept_count + 盯盘群' @('2026-10-27T10:05:00','2026-10-27T10:55:00','2026-10-27T14:18:00') 'ingest.jobs.heat_report' ''
Register-ScheduledTask -TaskName 'YuanShan-HeatReport' -Xml $heat -Force | Out-Null
foreach ($n in 'YuanShan-AuctionReport','YuanShan-HeatReport') {
    $t = Get-ScheduledTask -TaskName $n; $i = Get-ScheduledTaskInfo -TaskName $n
    '{0} state={1} 触发器数={2} next={3}' -f $n, $t.State, $t.Triggers.Count, $i.NextRunTime
}
