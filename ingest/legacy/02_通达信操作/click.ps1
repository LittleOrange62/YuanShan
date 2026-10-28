param([string]$Hwnd = "0", [int]$X = 0, [int]$Y = 0, [int]$Double = 0)
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Mouse {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
  public const uint LEFTDOWN = 0x02;
  public const uint LEFTUP = 0x04;
}
"@
$h = [System.IntPtr]::new([int64]$Hwnd)
[Mouse]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 300
[Mouse]::SetCursorPos($X, $Y) | Out-Null
Start-Sleep -Milliseconds 150
if ($Double -eq 1) {
  [Mouse]::mouse_event([Mouse]::LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
  [Mouse]::mouse_event([Mouse]::LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 80
}
[Mouse]::mouse_event([Mouse]::LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
[Mouse]::mouse_event([Mouse]::LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
Write-Output ("clicked " + $X + "," + $Y)
