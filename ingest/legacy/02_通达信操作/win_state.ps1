param([string]$Hwnd = "0")
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinState {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool GetWindowPlacement(IntPtr hWnd, ref WINDOWPLACEMENT wp);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] public struct WINDOWPLACEMENT {
    public int length; public int flags; public int showCmd;
    public int ptMinPositionX, ptMinPositionY, ptMaxPositionX, ptMaxPositionY;
    public int rcNormalLeft, rcNormalTop, rcNormalRight, rcNormalBottom;
  }
}
"@
$h = [System.IntPtr]::new([int64]$Hwnd)
$r = New-Object WinState+RECT
[WinState]::GetWindowRect($h, [ref]$r) | Out-Null
$wp = New-Object WinState+WINDOWPLACEMENT
$wp.length = [Runtime.InteropServices.Marshal]::SizeOf($wp)
[WinState]::GetWindowPlacement($h, [ref]$wp) | Out-Null
Write-Output ("rect=" + $r.L + "," + $r.T + "," + $r.R + "," + $r.B)
Write-Output ("visible=" + [WinState]::IsWindowVisible($h) + " iconic=" + [WinState]::IsIconic($h) + " zoomed=" + [WinState]::IsZoomed($h) + " showCmd=" + $wp.showCmd)
