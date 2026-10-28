param([int]$TargetPid = 0, [int]$MaxDepth = 5)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $TargetPid)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $win) { Write-Output "NO WINDOW for pid $TargetPid"; exit }

function Dump($el, $depth) {
    if ($depth -gt $MaxDepth) { return }
    $name = $el.Current.Name
    $type = $el.Current.ControlType.ProgrammaticName
    $auto = $el.Current.AutomationId
    $cls = $el.Current.ClassName
    $off = $el.Current.IsOffscreen
    $pad = '  ' * $depth
    Write-Output ("{0}[{1}] name='{2}' auto='{3}' cls='{4}' offscreen={5}" -f $pad, $type, $name, $auto, $cls, $off)
    $children = $el.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($c in $children) { Dump $c ($depth + 1) }
}

Dump $win 0
