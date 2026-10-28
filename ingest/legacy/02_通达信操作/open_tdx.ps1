<#
.SYNOPSIS
  一键打开并登录通达信，关闭弹窗，最小化到托盘（右下角图标）。
.DESCRIPTION
  流程：检查是否已登录 -> 打开通达信 -> 登录 -> 关闭弹窗 -> 最小化到托盘。
  依赖 T0002\user.ini 中 Min2Tray=1（最小化进托盘，而不是任务栏）。
#>
param(
    [string]$TdxExe = "D:\new_tdx64\TdxW.exe",
    [string]$User = "18969969696",
    [string]$Pass = "Chengzi.888"
)

$ErrorActionPreference = 'Stop'
$script:procName = "TdxW"

# ---------- Win32 基础函数 ----------
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;

public class TdxWin {
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
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);

  public const uint WM_KEYDOWN = 0x0100;
  public const uint WM_KEYUP   = 0x0101;
  public const uint WM_CLOSE   = 0x0010;
  public const uint BM_CLICK   = 0x00F5;
  public const uint LEFTDOWN   = 0x02;
  public const uint LEFTUP     = 0x04;
  public const int  SW_MINIMIZE = 6;

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

  public static void Click(int x, int y) {
    SetCursorPos(x, y);
    System.Threading.Thread.Sleep(120);
    mouse_event(LEFTDOWN, 0, 0, 0, UIntPtr.Zero);
    mouse_event(LEFTUP, 0, 0, 0, UIntPtr.Zero);
    System.Threading.Thread.Sleep(250);
  }
}
"@

function Get-TdxProc {
    Get-Process -Name $script:procName -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq (Get-Process -Id $PID).SessionId }
}

function Get-TopWindowsOf($proc) {
    if (-not $proc) { return @() }
    [TdxWin]::TopWindows($proc.Id)
}

function Write-Step($msg) {
    Write-Host ("[步骤] " + $msg) -ForegroundColor Cyan
}

function Write-Info($msg) {
    Write-Host ("  " + $msg) -ForegroundColor Gray
}

# ---------- 1. 检查是否已登录 ----------
Write-Step "1/5 检查通达信是否已打开并登录"

$proc = Get-TdxProc
$mainWin = $null
$loggedIn = $false

if (-not $proc) {
    Write-Info "未检测到 TdxW 进程 -> 未登录，需要打开"
} else {
    Write-Info ("TdxW 进程存在 PID=" + $proc.Id)
    $wins = Get-TopWindowsOf $proc
    foreach ($w in $wins) {
        $parts = $w.Split('|')
        $title = $parts[1]
        if ($title -match '行情报价|自选股|通达信金融终端.*-') {
            $loggedIn = $true
            $mainWin = $parts[0]
            Write-Info ("检测到主界面窗口: " + $title + " (hwnd=" + $mainWin + ") -> 已登录")
            break
        }
    }
    if (-not $loggedIn) {
        Write-Info "有进程但未看到主界面窗口 -> 可能停在登录窗口，需要登录"
    }
}

# ---------- 2. 打开通达信 ----------
if (-not $loggedIn) {
    Write-Step "2/5 打开通达信"
    if (-not $proc) {
        Start-Process -FilePath $TdxExe -WorkingDirectory (Split-Path $TdxExe)
        Write-Info "已启动 TdxW.exe，等待窗口出现..."
    } else {
        Write-Info "进程已存在，等待/激活登录窗口..."
    }

    # 等待登录窗口出现（特征：有 SafeEdit 子控件 / 标题为"通达信金融终端"）
    $loginWin = $null
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 800
        $proc = Get-TdxProc
        if (-not $proc) { continue }
        $wins = Get-TopWindowsOf $proc
        foreach ($w in $wins) {
            $parts = $w.Split('|')
            if ($parts[1] -match '通达信金融终端' -and -not ($parts[1] -match '-\s*\[|行情报价')) {
                $hwnd = [System.IntPtr]::new([int64]$parts[0])
                $kids = [TdxWin]::ChildWindows($hwnd)
                if ($kids -match 'SafeEdit') {
                    $loginWin = $parts[0]
                    break
                }
            }
        }
        if ($loginWin) { break }
    }
    if (-not $loginWin) {
        throw "等待登录窗口超时（60秒），请手动检查通达信状态"
    }
    Write-Info ("登录窗口已出现 (hwnd=" + $loginWin + ")")

    # ---------- 3. 登录 ----------
    Write-Step "3/5 登录通达信"
    $hLogin = [System.IntPtr]::new([int64]$loginWin)
    [TdxWin]::SetForegroundWindow($hLogin) | Out-Null
    Start-Sleep -Milliseconds 400

    # 解析 SafeEdit 位置并找"登录"按钮
    # 注意：枚举顺序不稳定，必须按 Y 坐标排序（账号框在上、密码框在下）
    $kids = [TdxWin]::ChildWindows($hLogin)
    $safeEdits = @()
    $loginBtn = $null
    foreach ($k in $kids) {
        $p = $k.Split('|')
        if ($p[1] -eq 'SafeEdit') {
            $coords = $p[3].Split(',')
            $cx = ([int]$coords[0] + [int]$coords[2]) / 2
            $cy = ([int]$coords[1] + [int]$coords[3]) / 2
            $safeEdits += @{ X = $cx; Y = $cy }
        }
        if ($p[1] -eq 'Button' -and $p[2] -eq '登录') {
            $loginBtn = $p[0]
        }
    }
    if ($safeEdits.Count -lt 2 -or -not $loginBtn) {
        throw "登录窗口控件解析失败，请手动登录"
    }
    # 按 Y 排序：y 小(上)=账号框，y 大(下)=密码框
    $sorted = @($safeEdits | Sort-Object Y)
    $userRect = @([int]$sorted[0].X, [int]$sorted[0].Y)
    $passRect = @([int]$sorted[1].X, [int]$sorted[1].Y)
    Write-Info ("账号框(" + $userRect[0] + "," + $userRect[1] + ") 密码框(" + $passRect[0] + "," + $passRect[1] + ") 登录按钮 hwnd=" + $loginBtn)

    $ws = New-Object -ComObject WScript.Shell

    # 账号框：点击后全选删除再输入（不管原来有什么，直接重填）
    [TdxWin]::Click([int]$userRect[0], [int]$userRect[1])
    Start-Sleep -Milliseconds 200
    $ws.SendKeys("^a"); Start-Sleep -Milliseconds 200
    $ws.SendKeys("{DELETE}"); Start-Sleep -Milliseconds 200
    $ws.SendKeys($User); Start-Sleep -Milliseconds 250

    # 密码框：同样全选删除再输入
    [TdxWin]::Click([int]$passRect[0], [int]$passRect[1])
    Start-Sleep -Milliseconds 200
    $ws.SendKeys("^a"); Start-Sleep -Milliseconds 200
    $ws.SendKeys("{DELETE}"); Start-Sleep -Milliseconds 200
    $ws.SendKeys($Pass); Start-Sleep -Milliseconds 250

    # 点击登录
    $hBtn = [System.IntPtr]::new([int64]$loginBtn)
    [TdxWin]::SendMessage($hBtn, [TdxWin]::BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
    Write-Info "已点击登录，等待主界面..."

    # 等待主界面出现
    $mainWin = $null
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 1000
        $proc = Get-TdxProc
        if (-not $proc) { continue }
        $wins = Get-TopWindowsOf $proc
        foreach ($w in $wins) {
            $parts = $w.Split('|')
            if ($parts[1] -match '行情报价|自选股') {
                $mainWin = $parts[0]
                Write-Info ("主界面已出现: " + $parts[1])
                break
            }
        }
        if ($mainWin) { break }
    }
    if (-not $mainWin) {
        throw "等待登录完成超时（60秒），账号密码可能错误或需要验证码"
    }
}

# ---------- 4. 关闭弹窗 ----------
Write-Step "4/5 关闭弹窗"
$proc = Get-TdxProc
if ($proc) {
    # 最多尝试 3 轮：弹窗可能延迟弹出
    for ($round = 1; $round -le 3; $round++) {
        $wins = Get-TopWindowsOf $proc
        $closedAny = $false
        foreach ($w in $wins) {
            $parts = $w.Split('|')
            $hwndStr = $parts[0]
            $title = $parts[1]
            # 跳过主窗口
            if ($mainWin -and $hwndStr -eq $mainWin) { continue }
            # 弹窗特征：非主窗口且可见（如"通达信信息"、资讯、广告等）
            if ($title -ne '') {
                $h = [System.IntPtr]::new([int64]$hwndStr)
                # 先试 ESC，再试 WM_CLOSE，双保险
                [TdxWin]::PostMessage($h, [TdxWin]::WM_KEYDOWN, [IntPtr]0x1B, [IntPtr]0) | Out-Null
                [TdxWin]::PostMessage($h, [TdxWin]::WM_KEYUP, [IntPtr]0x1B, [IntPtr]0) | Out-Null
                [TdxWin]::SendMessage($h, [TdxWin]::WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
                Write-Info ("第" + $round + "轮：已向弹窗发送 ESC+WM_CLOSE: '" + $title + "' (hwnd=" + $hwndStr + ")")
                $closedAny = $true
                Start-Sleep -Milliseconds 400
            }
        }
        Start-Sleep -Milliseconds 1500
        # 再次检查是否还有剩余弹窗
        $wins = Get-TopWindowsOf $proc
        $left = @()
        foreach ($w in $wins) {
            $parts = $w.Split('|')
            if ($mainWin -and $parts[0] -eq $mainWin) { continue }
            if ($parts[1] -ne '') { $left += $parts[1] }
        }
        if ($left.Count -eq 0) {
            Write-Info "弹窗已全部关闭"
            break
        } else {
            Write-Info ("第" + $round + "轮后仍有: " + ($left -join ', '))
        }
    }
}

# ---------- 5. 最小化到托盘 ----------
Write-Step "5/5 最小化到托盘（右下角图标）"
if (-not $mainWin) {
    $proc = Get-TdxProc
    if ($proc) {
        $wins = Get-TopWindowsOf $proc
        foreach ($w in $wins) {
            if ($w.Split('|')[1] -match '行情报价|自选股') { $mainWin = $w.Split('|')[0]; break }
        }
    }
}
if ($mainWin) {
    $hMain = [System.IntPtr]::new([int64]$mainWin)
    [TdxWin]::ShowWindow($hMain, [TdxWin]::SW_MINIMIZE) | Out-Null
    Start-Sleep -Milliseconds 600
    $iconic = [TdxWin]::IsIconic($hMain)
    # 检查 Min2Tray 配置
    $ini = 'D:\new_tdx64\T0002\user.ini'
    $min2tray = (Select-String -Path $ini -Pattern '^Min2Tray=(\d+)' -ErrorAction SilentlyContinue).Matches.Groups[1].Value
    Write-Info ("已发送最小化命令，IsIconic=" + $iconic + ", Min2Tray=" + $min2tray)
    if ($min2tray -eq '1') {
        Write-Info "Min2Tray=1 -> 窗口已最小化到系统托盘（右下角），任务栏不保留窗口"
    } else {
        Write-Info "注意：Min2Tray 未开启，最小化后可能仍在任务栏。如需托盘请检查 user.ini"
    }
} else {
    Write-Warning "未找到主窗口，跳过最小化"
}

Write-Host ""
Write-Host "===== 完成：通达信已打开并登录，最小化到托盘 =====" -ForegroundColor Green
