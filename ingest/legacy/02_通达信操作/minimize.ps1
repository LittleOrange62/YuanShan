param([string]$Hwnd = "0")
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinMin {
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
  public const int SW_MINIMIZE = 6;
}
"@
$h = [System.IntPtr]::new([int64]$Hwnd)
[WinMin]::ShowWindow($h, [WinMin]::SW_MINIMIZE) | Out-Null
Start-Sleep -Milliseconds 500
Write-Output ("minimized, iconic=" + [WinMin]::IsIconic($h))
