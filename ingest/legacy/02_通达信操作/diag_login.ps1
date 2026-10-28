$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;
public class TdxWinDiag {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr hParent, EnumProc cb, IntPtr lParam);
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  public static List<string> TopWindows(int targetPid) {
    var res = new List<string>();
    EnumWindows((h, l) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid == targetPid && IsWindowVisible(h)) {
        int len = GetWindowTextLength(h);
        var sb = new StringBuilder(len + 1);
        GetWindowText(h, sb, sb.Capacity);
        res.Add(h.ToString() + "|" + sb.ToString());
      }
      return true;
    }, IntPtr.Zero);
    return res;
  }
  public static List<string> ChildWindows(IntPtr hParent) {
    var res = new List<string>();
    EnumChildWindows(hParent, (h, l) => {
      var t = new StringBuilder(512); GetWindowText(h, t, 512);
      var c = new StringBuilder(128); GetClassName(h, c, 128);
      RECT r; GetWindowRect(h, out r);
      res.Add(h.ToString() + "|" + c.ToString() + "|" + t.ToString() + "|" + r.L + "," + r.T + "," + r.R + "," + r.B);
      return true;
    }, IntPtr.Zero);
    return res;
  }
}
"@

$proc = Get-Process -Name "TdxW" -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq (Get-Process -Id $PID).SessionId }
Write-Output ("proc found: " + [bool]$proc)
if ($proc) {
    Write-Output ("proc.SessionId=" + $proc.SessionId + " mySession=" + (Get-Process -Id $PID).SessionId)
    $wins = [TdxWinDiag]::TopWindows($proc.Id)
    Write-Output ("top windows count: " + $wins.Count)
    foreach ($w in $wins) {
        $parts = $w.Split('|')
        $m1 = $parts[1] -match '通达信金融终端'
        $m2 = $parts[1] -match '-\s*\[|行情报价'
        Write-Output ("win: hwnd=" + $parts[0] + " title='" + $parts[1] + "' match1=" + $m1 + " match2=" + $m2)
        if ($parts[1] -match '通达信金融终端' -and -not ($parts[1] -match '-\s*\[|行情报价')) {
            $hwnd = [System.IntPtr]::new([int64]$parts[0])
            $kids = [TdxWinDiag]::ChildWindows($hwnd)
            Write-Output ("  child count: " + $kids.Count)
            Write-Output ("  has SafeEdit: " + [bool]($kids -match 'SafeEdit'))
        }
    }
}
