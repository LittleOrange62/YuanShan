<#
.SYNOPSIS
  Sangfor SSL VPN split-route script
  Default traffic goes DIRECT with local DNS.
  ONLY the whitelist subnets (default 192.16.0.0/24, 192.18.0.0/24) go via VPN.

.DESCRIPTION
  Fixes: VPN client floods route table with huge internal ranges and points
  DNS to 127.0.0.1 local proxy, breaking public DNS (open.feishu.cn) and
  causing Feishu "internal error".

  Steps:
    1) Set physical NIC DNS back to local/public DNS (drop 127.0.0.1 proxy).
    2) Delete ALL routes pushed by VPN gateway, keep only whitelist subnets.
    3) Persist whitelist routes with "route -p".

  NOTE: VPN reconnect re-pushes routes; re-run this script afterwards
        (or install the scheduled task shown at the bottom).

  USAGE (admin PowerShell):
    powershell -ExecutionPolicy Bypass -File vpn_split_route.ps1
    powershell -ExecutionPolicy Bypass -File vpn_split_route.ps1 `
        -LocalDns @("223.5.5.5","119.29.29.29") `
        -VpnRoutes @("192.16.0.0/24","192.18.0.0/24")
#>

param(
    [string]$PhysicalNIC = "Ethernet0",                                     # physical NIC (direct)
    [string]$VpnNIC      = "",                                              # VPN NIC (auto-detect Sangfor/aTrust)
    [string]$VpnGw       = "",                                              # VPN gateway (auto-detect)
    [string[]]$LocalDns  = @("223.5.5.5","119.29.29.29","114.114.114.114"), # default DNS
    [string[]]$VpnRoutes = @("192.16.0.0/24","192.18.0.0/24")               # only these via VPN
)

$ErrorActionPreference = 'SilentlyContinue'

# ---------- 0. admin check ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] Run as Administrator." -ForegroundColor Red
    exit 1
}

# ---------- 1. auto-detect VPN NIC / gateway ----------
if (-not $VpnNIC) {
    $vpnAdapter = Get-NetAdapter | Where-Object { $_.InterfaceDescription -match 'Sangfor|aTrust|SSL VPN' -and $_.Status -eq 'Up' } | Select-Object -First 1
    if (-not $vpnAdapter) { Write-Host "[!] VPN NIC not found, specify -VpnNIC" -ForegroundColor Red; exit 1 }
    $VpnNIC = $vpnAdapter.Name
}
if (-not $VpnGw) {
    $gwRoute = Get-NetRoute -AddressFamily IPv4 -InterfaceAlias $VpnNIC | Where-Object { $_.NextHop -ne '0.0.0.0' -and $_.DestinationPrefix -ne '0.0.0.0/0' } | Select-Object -First 1
    $VpnGw = $gwRoute.NextHop
    if (-not $VpnGw) { Write-Host "[!] VPN gateway not found, specify -VpnGw" -ForegroundColor Red; exit 1 }
}
$vpnIfIndex = (Get-NetAdapter -Name $VpnNIC).ifIndex

Write-Host "[*] PhysicalNIC: $PhysicalNIC | VpnNIC: $VpnNIC (ifIndex=$vpnIfIndex) | VpnGw: $VpnGw" -ForegroundColor Cyan

# ---------- 2. DNS: physical NIC -> local DNS ----------
Write-Host "[*] Set physical NIC DNS -> $($LocalDns -join ', ')"
Set-DnsClientServerAddress -InterfaceAlias $PhysicalNIC -ServerAddresses $LocalDns
Clear-DnsClientCache
Write-Host "[*] Current DNS config:" -ForegroundColor Green
Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object { $_.ServerAddresses.Count -gt 0 } | Select-Object InterfaceAlias, ServerAddresses | Format-Table -AutoSize

# ---------- 3. delete VPN-pushed routes, keep whitelist ----------
Write-Host "[*] Delete routes via VPN gateway $VpnGw (keep $($VpnRoutes -join ', '))..."
$removed = 0
Get-NetRoute -AddressFamily IPv4 | Where-Object { $_.NextHop -eq $VpnGw } | ForEach-Object {
    if ($VpnRoutes -notcontains $_.DestinationPrefix) {
        Remove-NetRoute -DestinationPrefix $_.DestinationPrefix -NextHop $VpnGw -Confirm:$false
        $removed++
    }
}
Write-Host "[*] Removed $removed VPN routes" -ForegroundColor Green

# ---------- 4. add whitelist routes (persistent route -p) ----------
foreach ($p in $VpnRoutes) {
    $dest = ($p -split '/')[0]
    $len  = [int](($p -split '/')[1])
    $maskUint = [uint32]::MaxValue - ([uint32]([math]::Pow(2, 32 - $len)) - 1)
    $mask = ([System.Net.IPAddress]$maskUint).ToString()
    route delete $dest mask $mask | Out-Null
    route add $dest mask $mask $VpnGw if $vpnIfIndex -p | Out-Null
    Write-Host "[+] $dest/$len -> VPN gw $VpnGw (persistent)" -ForegroundColor Green
}

# ---------- 5. verify ----------
Write-Host "`n[*] Verify routes (only whitelist via VPN, default direct):" -ForegroundColor Cyan
route print -4 | Select-String '192.16.0.0|192.18.0.0|0.0.0.0' | ForEach-Object { $_.Line.Trim() }
Write-Host "`n[*] Verify public connectivity:"
Test-NetConnection -ComputerName open.feishu.cn -Port 443 -InformationLevel Quiet | ForEach-Object { "open.feishu.cn:443 reachable = $_" }
Write-Host "`nDone. Re-run after VPN reconnect if routes are reset." -ForegroundColor Yellow

<#
  Optional: scheduled task every 5 min to auto-restore split routes (run once as admin):
    schtasks /Create /TN "VPN_SplitRoute" /TR "powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Users\Administrator\.qwenpaw\workspaces\ceo\TOOLS\scripts\vpn_split_route.ps1" /SC MINUTE /MO 5 /RL HIGHEST /F
  Remove task:
    schtasks /Delete /TN "VPN_SplitRoute" /F
#>
