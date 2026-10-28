param([string]$Hwnd = "0")
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;
public class ChildEnum2 {
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr hParent, EnumProc cb, IntPtr lParam);
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  public static List<string> List(IntPtr hParent) {
    var res = new List<string>();
    EnumChildWindows(hParent, (h, l) => {
      var t = new StringBuilder(512); GetWindowText(h, t, 512);
      var c = new StringBuilder(128); GetClassName(h, c, 128);
      RECT r; GetWindowRect(h, out r);
      res.Add(h.ToString() + " | cls=" + c.ToString() + " | txt='" + t.ToString() + "' | visible=" + IsWindowVisible(h) + " | rect=" + r.L + "," + r.T + "," + r.R + "," + r.B);
      return true;
    }, IntPtr.Zero);
    return res;
  }
}
"@
$h = [System.IntPtr]::new([int64]$Hwnd)
[ChildEnum2]::List($h) | ForEach-Object { Write-Output $_ }
