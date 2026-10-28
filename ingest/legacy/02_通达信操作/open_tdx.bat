@echo off
chcp 65001 >nul
echo ============================================
echo  打开通达信 - 一键流程
echo  检查登录 - 打开 - 登录 - 关弹窗 - 托盘化
echo ============================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0open_tdx.ps1"
echo.
pause
