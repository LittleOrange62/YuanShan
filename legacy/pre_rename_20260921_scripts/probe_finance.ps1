$ErrorActionPreference = 'Continue'
$root = 'D:\finance'
Write-Output "=== TOP LEVEL of D:\finance ==="
Get-ChildItem -LiteralPath $root -Force | Sort-Object PSIsContainer, Name | ForEach-Object {
  if ($_.PSIsContainer) {
    Write-Output ('[DIR ] ' + $_.Name)
  } else {
    Write-Output ('[FILE] ' + $_.Name + '  ' + [math]::Round($_.Length/1KB,1) + 'KB  ' + $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm'))
  }
}

Write-Output ""
Write-Output "=== SUBDIRECTORIES (depth 2) with file counts ==="
Get-ChildItem -LiteralPath $root -Directory -Force | ForEach-Object {
  $d1 = $_
  $n = (Get-ChildItem -LiteralPath $d1.FullName -File -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object).Count
  Write-Output ('[D1] ' + $d1.Name + '  files_recursive=' + $n)
  Get-ChildItem -LiteralPath $d1.FullName -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
    $d2 = $_
    $n2 = (Get-ChildItem -LiteralPath $d2.FullName -File -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Output ('     [D2] ' + $d2.Name + '  files_recursive=' + $n2)
  }
}

Write-Output ""
Write-Output "=== ALL .py FILES under D:\finance (grouped by parent dir) ==="
Get-ChildItem -LiteralPath $root -Filter *.py -Recurse -File -Force -ErrorAction SilentlyContinue |
  Group-Object { $_.DirectoryName } | Sort-Object Name | ForEach-Object {
    Write-Output ('DIR: ' + $_.Name + '   count=' + $_.Count)
  }

Write-Output ""
Write-Output "=== SCRIPT-LIKE FILE COUNT BY EXTENSION under D:\finance ==="
Get-ChildItem -LiteralPath $root -Recurse -File -Force -ErrorAction SilentlyContinue |
  Where-Object { $_.Extension -match '^\.(py|ps1|bat|cmd|sh|sql|md|txt|json|yaml|yml)$' } |
  Group-Object Extension | Sort-Object Count -Descending | ForEach-Object {
    Write-Output ($_.Name + ' => ' + $_.Count)
  }

Write-Output ""
Write-Output "=== D:\finance\data CONTENTS ==="
Get-ChildItem -LiteralPath 'D:\finance\data' -Force -ErrorAction SilentlyContinue | Sort-Object PSIsContainer, Name | ForEach-Object {
  if ($_.PSIsContainer) {
    $n = (Get-ChildItem -LiteralPath $_.FullName -File -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Output ('[DIR ] ' + $_.Name + '  files=' + $n)
  } else {
    Write-Output ('[FILE] ' + $_.Name + '  ' + [math]::Round($_.Length/1MB,1) + 'MB  ' + $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm'))
  }
}
