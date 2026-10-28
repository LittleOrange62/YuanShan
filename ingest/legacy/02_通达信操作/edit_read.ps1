param([string]$Hwnd = "0")
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class EditRead {
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  public const uint WM_GETTEXT = 0x000D;
  public const uint WM_GETTEXTLENGTH = 0x000E;
  public static string GetText(IntPtr hWnd) {
    int len = GetWindowTextLength(hWnd);
    var sb = new StringBuilder(len + 2);
    GetWindowText(hWnd, sb, sb.Capacity);
    return sb.ToString();
  }
}
"@
$h = [System.IntPtr]::new([int64]$Hwnd)
$txt = [EditRead]::GetText($h)
Write-Output ("text='" + $txt + "' len=" + $txt.Length)
