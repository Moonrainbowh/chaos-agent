# Run only after approval of the Windows SSH deployment plan.
[CmdletBinding()]
param([switch]$Apply, [string]$InterfaceAlias = 'WLAN')

$ErrorActionPreference = 'Stop'
$taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$taskPrincipal = [Security.Principal.WindowsPrincipal]::new($taskIdentity)
$taskElevated = $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$taskService = Get-Service sshd -ErrorAction SilentlyContinue
$taskProfile = Get-NetConnectionProfile -InterfaceAlias $InterfaceAlias
[pscustomobject]@{
    Apply = [bool]$Apply
    Elevated = $taskElevated
    ExistingSshd = [bool]$taskService
    InterfaceAlias = $InterfaceAlias
    NetworkCategory = [string]$taskProfile.NetworkCategory
    PlannedPort = 22
    PlannedRemoteAddress = 'LocalSubnet'
} | Format-List
if (-not $Apply) { return }
if (-not $taskElevated) { throw 'An elevated administrator process is required.' }
if ($taskService) { throw 'sshd already exists; inspect its configuration before applying this new-install plan.' }
if (Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 22 is already in use.'
}

$taskCapability = Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
if ($taskCapability.RestartNeeded) { throw 'Windows requires a restart; setup stopped before starting sshd.' }
# Installation can create a default rule. Restrict it before starting the service.
$taskRule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if ($taskRule) {
    Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Enabled True -Direction Inbound `
        -Action Allow -Protocol TCP -LocalPort 22 -Profile Any `
        -InterfaceAlias $InterfaceAlias -RemoteAddress LocalSubnet
} else {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (LAN)' `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 `
        -Profile Any -InterfaceAlias $InterfaceAlias -RemoteAddress LocalSubnet | Out-Null
}
Set-Service -Name sshd -StartupType Manual
Start-Service sshd
Get-Service sshd | Select-Object Name,Status,StartType
Get-NetTCPConnection -LocalPort 22 -State Listen | Select-Object LocalAddress,LocalPort
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' | Get-NetFirewallAddressFilter |
    Select-Object RemoteAddress
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' | Get-NetFirewallPortFilter |
    Select-Object Protocol,LocalPort
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' | Get-NetFirewallInterfaceFilter |
    Select-Object InterfaceAlias
