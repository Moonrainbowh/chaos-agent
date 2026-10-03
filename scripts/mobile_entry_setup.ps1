# Configure only the supplied phone key to open the reviewed mobile project entry.
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = 'D:\soft\miniconda3\python.exe',
    [string]$PhoneKeyBlob = 'AAAAC3NzaC1lZDI1NTE5AAAAIGD4Bs98RqjvD2xs/+E87pF/cZR+bi5ne5bf3sWQs0Ay',
    [string]$ResultPath
)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$taskPython = (Resolve-Path -LiteralPath $Python).Path
$taskEntry = Join-Path $env:LOCALAPPDATA 'chaos-agent\mobile-launch.py'
$taskShortcut = Join-Path $env:USERPROFILE 'chaos-mobile.cmd'
$taskKeys = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
$taskPythonCommand = $taskPython.Replace('\', '/')
$taskEntryCommand = $taskEntry.Replace('\', '/')
$taskCommand = '"' + $taskPythonCommand + '" "' + $taskEntryCommand + '"'
[pscustomobject]@{ Apply=[bool]$Apply; SourceRoot=$taskRoot; Entry=$taskEntry; Shortcut=$taskShortcut; PhoneCommand=$taskCommand } | Format-List
if (-not $Apply) { return }
$taskPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'An elevated process is required to update the existing phone key.'
}
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'chaos_agent\mobile_cli.py'))) { throw 'Mobile entry source is missing.' }
$taskOriginal = [IO.File]::ReadAllText($taskKeys)
$taskAcl = Get-Acl -LiteralPath $taskKeys
$taskPattern = '^\s*ssh-ed25519\s+' + [regex]::Escape($PhoneKeyBlob) + '(\s|$)'
$taskLines = @($taskOriginal -split '\r?\n')
$taskMatches = @($taskLines | Where-Object { $_ -match $taskPattern })
if ($taskMatches.Count -ne 1) { throw 'Expected exactly one existing phone key; no change applied.' }
$taskBackup = $taskKeys + '.mobile-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
[IO.File]::WriteAllText($taskBackup, $taskOriginal, [Text.UTF8Encoding]::new($false))
Set-Acl -LiteralPath $taskBackup -AclObject $taskAcl
$taskRootLiteral = ConvertTo-Json -InputObject $taskRoot -Compress
$taskLauncher = @"
import sys
from pathlib import Path
ROOT = Path($taskRootLiteral)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from chaos_agent.mobile_cli import main
sys.argv[1:1] = ['--seed-project', str(ROOT)]
raise SystemExit(main())
"@
[IO.Directory]::CreateDirectory((Split-Path -Parent $taskEntry)) | Out-Null
[IO.File]::WriteAllText($taskEntry, $taskLauncher, [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($taskShortcut, '@"' + $taskPython + '" "' + $taskEntry + '" %*' + "`r`n", [Text.ASCIIEncoding]::new())
$taskOption = $taskCommand.Replace('\', '\\').Replace('"', '\"')
$taskReplacement = 'command="' + $taskOption + '" ssh-ed25519 ' + $PhoneKeyBlob + ' android-phone'
$taskUpdated = ($taskLines | ForEach-Object { if ($_ -match $taskPattern) { $taskReplacement } else { $_ } }) -join "`n"
try {
    & $taskPython $taskEntry --check
    if ($LASTEXITCODE -ne 0) { throw 'Mobile launcher diagnostic failed.' }
    [IO.File]::WriteAllText($taskKeys, $taskUpdated, [Text.UTF8Encoding]::new($false))
    Set-Acl -LiteralPath $taskKeys -AclObject $taskAcl
    & "$env:WINDIR\System32\OpenSSH\sshd.exe" -t
    if ($LASTEXITCODE -ne 0) { throw 'sshd configuration validation failed.' }
    $taskVerified = [IO.File]::ReadAllText($taskKeys)
    if (-not $taskVerified.Contains($taskReplacement)) { throw 'Phone key readback mismatch.' }
    $taskOtherBefore = ($taskLines | Where-Object { $_ -notmatch $taskPattern }) -join "`n"
    $taskOtherAfter = (@($taskVerified -split '\r?\n') | Where-Object { $_ -cne $taskReplacement }) -join "`n"
    if ($taskOtherBefore -cne $taskOtherAfter) { throw 'Other authorized key records changed; restoring original.' }
    if ($ResultPath) {
        @{ success=$true; entry=$taskEntry; shortcut=$taskShortcut; backup=$taskBackup; command=$taskCommand;
           keyLine=$taskReplacement; otherLinesPreserved=$true; aclProtected=(Get-Acl -LiteralPath $taskKeys).AreAccessRulesProtected } |
            ConvertTo-Json | Set-Content -LiteralPath $ResultPath -Encoding UTF8
    }
} catch {
    [IO.File]::WriteAllText($taskKeys, $taskOriginal, [Text.UTF8Encoding]::new($false))
    Set-Acl -LiteralPath $taskKeys -AclObject $taskAcl
    throw
}
