# YuanShan-IntradayZtWatch 计划任务注册（覆盖式，可反复重跑）
# 交易日 09:15 起每 5 分钟一针、持续 6 小时；单次短跑（外部 10-23 事故：常驻 loop 会静默死）。
# 教训原样抄外部那份：New-ScheduledTaskTrigger -Once+Repetition 只跑当天、
# PS5.1 给 Daily 触发器赋 .Repetition 会报错 ⇒ 直接喂完整 XML。
# 改节奏：改 StartBoundary / PT5M / PT6H 后重跑本脚本。禁用：schtasks /Change /TN YuanShan-IntradayZtWatch /DISABLE
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py   = Join-Path $root '.venv\Scripts\python.exe'
$args = '-X utf8 -m ingest.jobs.zt_watch --once --send --channel feishu-main'
$userId = whoami
$xml = @'
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>YuanShan: 交易日09:15-15:15每5分钟 涨停/跌停池事件差 -&gt; 落 analytics.intraday_pool_event + 推盯盘群; 非交易日脚本内静默退; 单次短跑，调度器即看门狗</Description>
    <URI>\YuanShan-IntradayZtWatch</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>%USERID%</UserId>
      <LogonType>S4U</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure>
    <IdleSettings><StopOnIdleEnd>true</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-10-27T09:15:00</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition><Interval>PT5M</Interval><Duration>PT6H</Duration><StopAtDurationEnd>true</StopAtDurationEnd></Repetition>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>%PY%</Command>
      <Arguments>%ARGS%</Arguments>
      <WorkingDirectory>%DIR%</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'@
$xml = $xml.Replace('%USERID%', $userId).Replace('%PY%', $py).Replace('%ARGS%', $args).Replace('%DIR%', $root)
if ($Remove) { Unregister-ScheduledTask -TaskName 'YuanShan-IntradayZtWatch' -Confirm:$false; '已删除'; exit }
Register-ScheduledTask -TaskName 'YuanShan-IntradayZtWatch' -Xml $xml -Force | Out-Null
$t = Get-ScheduledTask -TaskName 'YuanShan-IntradayZtWatch'
$r = $t.Triggers[0].Repetition
Write-Host ("已注册 YuanShan-IntradayZtWatch  state={0}  Rep={1}/{2}  next={3}" -f $t.State, $r.Interval, $r.Duration,
    (Get-ScheduledTaskInfo -TaskName 'YuanShan-IntradayZtWatch').NextRunTime)
