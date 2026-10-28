param([string]$Hwnd = "0")
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$h = [System.IntPtr]::new([int64]$Hwnd)
$el = [System.Windows.Automation.AutomationElement]::FromHandle($h)
try {
  $vp = $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
  $v = $vp.Current.Value
  Write-Output ("value='" + $v + "' len=" + $v.Length)
} catch {
  Write-Output "no value pattern"
}
try {
  $tp = $el.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
  $doc = $tp.DocumentRange.GetText(-1)
  Write-Output ("text='" + $doc + "'")
} catch {
  Write-Output "no text pattern"
}
