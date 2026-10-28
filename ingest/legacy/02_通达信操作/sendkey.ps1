param([string]$Hwnd = "0", [string]$Key = "ESC")
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class SendKey {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
  public const uint WM_CLOSE = 0x0010;
  public const uint WM_KEYDOWN = 0x0100;
  public const uint WM_KEYUP = 0x0101;
  public const uint WM_SYSCOMMAND = 0x0112;
  public const uint SC_CLOSE = 0xF060;
}
"@
$h = [System.IntPtr]::new([int64]$Hwnd)
[SendKey]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 200
if ($Key -eq "ESC") {
  [SendKey]::PostMessage($h, [SendKey]::WM_KEYDOWN, [IntPtr]0x1B, [IntPtr]0) | Out-Null
  [SendKey]::PostMessage($h, [SendKey]::WM_KEYUP, [IntPtr]0x1B, [IntPtr]0) | Out-Null
  Write-Output "sent ESC"
} elseif ($Key -eq "CLOSE") {
  [SendKey]::SendMessage($h, [SendKey]::WM_SYSCOMMAND, [IntPtr][SendKey]::SC_CLOSE, [IntPtr]0) | Out-Null
  Write-Output "sent WM_SYSCOMMAND SC_CLOSE"
}
