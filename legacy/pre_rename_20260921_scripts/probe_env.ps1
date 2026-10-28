$ErrorActionPreference = 'Continue'
Write-Output ("PS_VERSION=" + $PSVersionTable.PSVersion.ToString())
Write-Output ("OS=" + [Environment]::OSVersion.VersionString)
Write-Output ("MACHINE=" + [Environment]::MachineName)
Write-Output ("USER=" + [Environment]::UserName)
Write-Output ("IS_ADMIN=" + ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))
Write-Output ("CWD=" + (Get-Location).Path)
Write-Output "--- PATHS ---"
$paths = @(
  'D:\finance',
  'D:\finance\data',
  'D:\finance\脚本',
  'D:\finance\日志',
  'D:\finance\TataPG',
  'D:\FinanceTool',
  'D:\financeWeb',
  'D:\new_tdx64\vipdoc'
)
foreach ($p in $paths) {
  Write-Output ($p + ' => ' + (Test-Path -LiteralPath $p))
}
Write-Output "--- DRIVES ---"
Get-PSDrive -PSProvider FileSystem | ForEach-Object {
  Write-Output ($_.Name + ': used=' + [math]::Round($_.Used/1GB,1) + 'GB free=' + [math]::Round($_.Free/1GB,1) + 'GB')
}
Write-Output "--- EXISTING POSTGRES ---"
$svc = Get-Service | Where-Object { $_.Name -like '*postgre*' -or $_.DisplayName -like '*PostgreSQL*' }
if ($svc) { $svc | ForEach-Object { Write-Output ('SERVICE ' + $_.Name + ' status=' + $_.Status) } } else { Write-Output 'NO_PG_SERVICE' }
foreach ($d in @('C:\Program Files\PostgreSQL','D:\PostgreSQL','C:\PostgreSQL')) {
  Write-Output ($d + ' => ' + (Test-Path -LiteralPath $d))
}
$psqlCmd = Get-Command psql -ErrorAction SilentlyContinue
if ($psqlCmd) { Write-Output ('PSQL_ON_PATH=' + $psqlCmd.Source) } else { Write-Output 'PSQL_NOT_ON_PATH' }
Write-Output "--- PYTHON ---"
foreach ($c in @('python','py')) {
  $cmd = Get-Command $c -ErrorAction SilentlyContinue
  if ($cmd) { Write-Output ($c + ' => ' + $cmd.Source) } else { Write-Output ($c + ' => NOT_FOUND') }
}
$pyExe = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe'
Write-Output ($pyExe + ' => ' + (Test-Path -LiteralPath $pyExe))
if (Test-Path -LiteralPath $pyExe) { & $pyExe -V }
Write-Output "--- NETWORK ---"
try {
  $r = Invoke-WebRequest -Uri 'https://www.enterprisedb.com' -Method Head -TimeoutSec 15 -UseBasicParsing
  Write-Output ('EDB_HEAD=' + $r.StatusCode)
} catch { Write-Output ('EDB_FAIL=' + $_.Exception.Message) }
try {
  $r2 = Invoke-WebRequest -Uri 'https://sbp.enterprisedb.com/getfile.jsp?fileid=1259661' -Method Head -TimeoutSec 15 -UseBasicParsing
  Write-Output ('EDB_FILE_HEAD=' + $r2.StatusCode + ' len=' + $r2.Headers['Content-Length'])
} catch { Write-Output ('EDB_FILE_FAIL=' + $_.Exception.Message) }
Write-Output "--- PORT 5432 ---"
$t = Test-NetConnection -ComputerName '127.0.0.1' -Port 5432 -InformationLevel Quiet -WarningAction SilentlyContinue -ErrorAction SilentlyContinue
Write-Output ('PORT5432_OPEN=' + $t)
