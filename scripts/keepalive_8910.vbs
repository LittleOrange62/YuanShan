' keepalive_8910.vbs - hidden launcher for the 8910 watchdog (Windows task entry).
'
' WHY THIS FILE EXISTS. Task Scheduler starts powershell.exe, which is a
' console-subsystem program, so the OS creates a console window BEFORE powershell
' ever reads -WindowStyle Hidden. That is the black window flashing every 5
' minutes. wscript.exe is a Windows-subsystem host (it never allocates a console)
' and Run(cmd, 0, ...) starts the child with SW_HIDE, so the child's window is
' hidden from creation instead of shown-then-hidden.
'
' WHY IT WAITS (the third argument is True). The old task entry blocked until the
' watchdog returned, so Task Scheduler could see "a run is in progress" and
' MultipleInstancesPolicy=IgnoreNew plus ExecutionTimeLimit PT4M applied to it.
' Firing and forgetting would silently drop both guards (the watchdog would be
' able to overlap itself and re-launch the service twice). Keep this True.
'
' WHY NO ERROR DIALOG. Without this handler a failing WshShell.Run pops a modal
' WSH error box on the desktop - exactly the kind of window this file exists to
' remove. Failures go to the watchdog log instead, and the task exits non-zero so
' /system's scheduled-task row can show a bad LastTaskResult.
'
' KEEP THIS FILE ASCII-ONLY: WSH decodes .vbs with the local ANSI codepage (GBK on
' this box), so Chinese comments here would read as mojibake. The watchdog logic
' and its Chinese comments live in scripts\keepalive_8910.ps1.

Option Explicit
On Error Resume Next

Dim sh, root, cmd, logPath, fso, ts
root = "D:\finance\YuanShan"
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden " & _
      "-File """ & root & "\scripts\keepalive_8910.ps1"""

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = root
sh.Run cmd, 0, True

If Err.Number <> 0 Then
  logPath = root & "\.local\keepalive.log"
  Set fso = CreateObject("Scripting.FileSystemObject")
  ts = fso.OpenTextFile(logPath, 8, True)
  ts.WriteLine Now() & "  keepalive wrapper could not start powershell: " & Err.Description
  ts.Close
  WScript.Quit 1
End If

WScript.Quit 0
