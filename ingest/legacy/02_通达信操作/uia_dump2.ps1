param([int]$TargetPid = 0, [int]$MaxDepth = 6)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $TargetPid)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $win) { Write-Output "NO WINDOW for pid $TargetPid"; exit }

function Get-LegacyName($el) {
    try {
        $lp = $el.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
        return $lp.Current.Name
    } catch { return $null }
}
function Get-Value($el) {
    try {
        $vp = $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        return $vp.Current.Value
    } catch { return $null }
}

function Dump($el, $depth) {
    if ($depth -gt $MaxDepth) { return }
    $name = $el.Current.Name
    $type = $el.Current.ControlType.ProgrammaticName
    $auto = $el.Current.AutomationId
    $cls = $el.Current.ClassName
    $lname = Get-LegacyName $el
    $val = Get-Value $el
    $pad = '  ' * $depth
    $extra = ''
    if ($lname) { $extra += " legacy='$lname'" }
    if ($null -ne $val -and $val -ne '') { $extra += " value='$val'" }
    Write-Output ("{0}[{1}] name='{2}' auto='{3}' cls='{4}'{5}" -f $pad, $type, $name, $auto, $cls, $extra)
    $children = $el.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($c in $children) { Dump $c ($depth + 1) }
}

Dump $win 0
