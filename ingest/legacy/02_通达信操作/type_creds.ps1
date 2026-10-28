param([string]$Hwnd = "0")
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class KeyIn {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
  [DllImport("user32.dll")] public static extern bool SetFocus(IntPtr hWnd);
  public const uint LEFTDOWN = 0x02;
  public const uint LEFTUP = 0x04;
}
"@
$ws = New-Object -ComObject WScript.Shell
$h = [System.IntPtr]::new([int64]$Hwnd)
[KeyIn]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 400

function Click($x, $y) {
  [KeyIn]::SetCursorPos($x, $y) | Out-Null
  Start-Sleep -Milliseconds 150
  [KeyIn]::mouse_event([KeyIn]::LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
  [KeyIn]::mouse_event([KeyIn]::LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 300
}

# 用户输入框中心 (777,553)-(971,574) => (874,563)
Click 874 563
$ws.SendKeys("18969969696")
Start-Sleep -Milliseconds 300

# 密码输入框中心 (777,598)-(971,617) => (874,607)
Click 874 607
$ws.SendKeys("Chengzi.888")
Start-Sleep -Milliseconds 300

Write-Output "typed credentials"
