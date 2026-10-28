Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class TdxWin {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}
"@

function Find-MainWindow {
    $found = $null
    [TdxWin]::EnumWindows({ param($h,$l)
        $len = [TdxWin]::GetWindowTextLength($h)
        if ($len -gt 0) {
            $sb = New-Object System.Text.StringBuilder $len
            [TdxWin]::GetWindowText($h, $sb, $len+1) | Out-Null
            $title = $sb.ToString()
            if ($title -match "行情报价|自选股") {
                $found = $h
                return $false
            }
        }
        return $true
    }, [IntPtr]::Zero)
    return $found
}

$hwnd = Find-MainWindow
if ($hwnd) {
    $h = [System.IntPtr]$hwnd
    [TdxWin]::SetForegroundWindow($h) | Out-Null
    Start-Sleep -Milliseconds 200
    # send .933 keystrokes (VK_OEM_PERIOD etc.)
    $VK_OEM_PERIOD = 0xBE
    $VK_9 = 0x39
    $VK_3 = 0x33
    # press '.'
    [TdxWin]::keybd_event($VK_OEM_PERIOD,0,0,([UIntPtr]::Zero))
    Start-Sleep -Milliseconds 100
    # press '9'
    [TdxWin]::keybd_event($VK_9,0,0,([UIntPtr]::Zero))
    Start-Sleep -Milliseconds 100
    # press '3' twice
    [TdxWin]::keybd_event($VK_3,0,0,([UIntPtr]::Zero))
    Start-Sleep -Milliseconds 100
    [TdxWin]::keybd_event($VK_3,0,0,([UIntPtr]::Zero))
    Start-Sleep -Milliseconds 200
    Write-Host "Sent .933 to trigger盘后数据下载" -ForegroundColor Green
} else {
    Write-Host "未找到通达信主窗口" -ForegroundColor Red
}
