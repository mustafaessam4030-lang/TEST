<#
  ATA worker — set up the Windows machine that runs the automation.

  Run in an elevated PowerShell on the worker VM, from the folder that holds
  the control-tower code (the same code the local Control Tower runs):

      .\deploy\azure\install_worker.ps1 -ControlPlaneUrl https://ata.mantrac.com `
          -WorkerToken "w_xxxxxxxx.yyyyyyyy"

  What it does:
    1. checks Python 3.11+ and Microsoft Edge are present
    2. installs Playwright (the automation's only dependency)
    3. stores the control plane address and the worker token as environment
       variables of the worker account only (the token is a credential: it is
       not written to any file by this script)
    4. registers a scheduled task that starts the worker agent when the
       worker account signs in, and restarts it if it stops

  The agent only makes outbound HTTPS calls; open no inbound port.
  The worker account must sign in to a desktop session (auto-logon) because
  the automation drives a real Edge window. See PLATFORM.md, step 7.
#>
param(
  [Parameter(Mandatory = $true)][string]$ControlPlaneUrl,
  [Parameter(Mandatory = $true)][string]$WorkerToken,
  [string]$WorkerAccount = $env:USERNAME,
  [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")

if (-not $ControlPlaneUrl.StartsWith("https://")) { throw "The control plane address must be https://" }
if ($WorkerToken -notmatch '^w_[0-9a-f]{12}\.[A-Za-z0-9_-]{20,}$') { throw "That does not look like a worker token." }

$version = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$version -lt [version]"3.11") { throw "Python 3.11 or newer is required (found $version)." }
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
          "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ }
if (-not $edge) { throw "Microsoft Edge was not found." }

& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $Root "requirements.txt")

[Environment]::SetEnvironmentVariable("ATA_CONTROL_PLANE_URL", $ControlPlaneUrl, "User")
[Environment]::SetEnvironmentVariable("ATA_WORKER_TOKEN", $WorkerToken, "User")
[Environment]::SetEnvironmentVariable("ATLAS_DATA_ORIGIN", "production", "User")

$action = New-ScheduledTaskAction -Execute $Python -Argument "-m worker" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $WorkerAccount
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $WorkerAccount -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "ATA Worker Agent" -Action $action -Trigger $trigger `
  -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "Installed. Sign in as $WorkerAccount (or enable auto-logon) and the agent starts."
Write-Host "Check it from the dashboard: Access > Workers should show ONLINE within 15 seconds."
