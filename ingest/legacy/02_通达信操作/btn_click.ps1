param([string]$BtnHwnd = "0")
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class BtnClick {
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  public const uint BM_CLICK = 0x00F5;
  public const uint WM_LBUTTONDOWN = 0x0201;
  public const uint WM_LBUTTONUP = 0x0202;
}
"@
$b = [System.IntPtr]::new([int64]$BtnHwnd)
# 找到按钮所属窗口并置前
[BtnClick]::SendMessage($b, [BtnClick]::BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
Write-Output ("BM_CLICK sent to " + $b)
