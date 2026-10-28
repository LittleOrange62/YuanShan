@echo off
REM 激活通达信窗口并模拟登录
powershell -Command ^
  "$wshell = New-Object -ComObject wscript.shell;" ^
  "$wshell.AppActivate('TdxW');" ^
  "Start-Sleep -Seconds 1;" ^
  "$wshell.SendKeys('{ENTER}');" ^
  "echo 已发送回车"
echo 完成