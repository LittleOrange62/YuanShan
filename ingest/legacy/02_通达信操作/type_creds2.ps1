param([string]$Hwnd = "0")
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class KeyIn2 {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
  public const uint LEFTDOWN = 0x02;
  public const uint LEFTUP = 0x04;
}
"@
$ws = New-Object -ComObject WScript.Shell
$h = [System.IntPtr]::new([int64]$Hwnd)
[KeyIn2]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 400

function Click($x, $y) {
  [KeyIn2]::SetCursorPos($x, $y) | Out-Null
  Start-Sleep -Milliseconds 150
  [KeyIn2]::mouse_event([KeyIn2]::LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
  [KeyIn2]::mouse_event([KeyIn2]::LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 300
}

function ClearAndType($x, $y, $text) {
  Click $x $y
  # 全选并删除已有内容
  $ws.SendKeys("^a")
  Start-Sleep -Milliseconds 200
  $ws.SendKeys("{DELETE}")
  Start-Sleep -Milliseconds 200
  $ws.SendKeys($text)
  Start-Sleep -Milliseconds 300
}

# 用户输入框 (874,563)：清空后输入账号
ClearAndType 874 563 "18969969696"

# 密码输入框 (874,607)：清空后输入密码
ClearAndType 874 607 "Chengzi.888"

Write-Output "credentials set (cleared then typed)"
