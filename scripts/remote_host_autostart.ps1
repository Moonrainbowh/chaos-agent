[CmdletBinding()]
param(
    [string]$ChaosAgentExecutable = "chaos-agent.exe",
    [string]$CloudflaredExecutable = "cloudflared.exe",
    [string]$CloudflaredConfig,
    [string]$TaskPrefix = "ChaosAgentRemote",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$hostTask = "$TaskPrefix-Host"
$tunnelTask = "$TaskPrefix-Cloudflared"

function Remove-RemoteTask([string]$Name) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
}

if ($Remove) {
    Remove-RemoteTask $hostTask
    Remove-RemoteTask $tunnelTask
    Write-Host "Removed scheduled tasks: $hostTask, $tunnelTask"
    exit 0
}

if (-not $Remove -and -not $CloudflaredConfig) {
    throw "CloudflaredConfig is required when installing scheduled tasks."
}

if (-not $Remove -and -not (Test-Path -LiteralPath $CloudflaredConfig -PathType Leaf)) {
    throw "Cloudflared config does not exist: $CloudflaredConfig"
}

$chaosAgent = (Get-Command $ChaosAgentExecutable -ErrorAction Stop).Path
$cloudflared = (Get-Command $CloudflaredExecutable -ErrorAction Stop).Path
$config = (Resolve-Path -LiteralPath $CloudflaredConfig).Path
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -AtLogOn

$hostAction = New-ScheduledTaskAction -Execute $chaosAgent -Argument "host"
$tunnelAction = New-ScheduledTaskAction -Execute $cloudflared -Argument "tunnel --config `"$config`" run"

Register-ScheduledTask -TaskName $hostTask -Action $hostAction -Trigger $trigger -Principal $principal -Description "Start the local Chaos Agent Remote Host" -Force | Out-Null
Register-ScheduledTask -TaskName $tunnelTask -Action $tunnelAction -Trigger $trigger -Principal $principal -Description "Start the Cloudflare Tunnel for Chaos Agent" -Force | Out-Null

Write-Host "Registered scheduled tasks: $hostTask, $tunnelTask"
Write-Host "The Host remains localhost-only; Cloudflare Tunnel connects to http://127.0.0.1:8787."
