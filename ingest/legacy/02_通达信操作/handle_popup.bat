@echo off
REM 等待弹窗出现（用 ping 延迟 6 秒，避免 timeout 重定向问题）
ping -n 7 127.0.0.1 >nul
REM 用PowerShell模拟回车键（选择默认选项"是"）
powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')"
echo 已模拟回车键，处理弹窗