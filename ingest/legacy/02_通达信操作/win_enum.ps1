param([int]$TargetPid = 0)
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;
public class WinEnum {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  public static List<string> List(int targetPid) {
    var res = new List<string>();
    EnumWindows((h, l) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid == targetPid && IsWindowVisible(h)) {
        int len = GetWindowTextLength(h);
        var sb = new StringBuilder(len + 1);
        GetWindowText(h, sb, sb.Capacity);
        res.Add(h.ToString() + " | " + sb.ToString());
      }
      return true;
    }, IntPtr.Zero);
    return res;
  }
}
"@
[WinEnum]::List($TargetPid) | ForEach-Object { Write-Output $_ }
