<#
  Install the Mantrac tower on the Remote Desktop server, so it runs on its own.

  Run INSTALL_SERVER.bat (it asks Windows for administrator rights and runs
  this), signed in as the Windows account the automation will use.

  What it does, in order:
    1. checks Python 3.11+ and Microsoft Edge are installed
    2. installs the Python packages the automation needs
    3. opens port 8787 to the company network only (domain and private
       networks — never public), so colleagues reach the dashboard
    4. keeps the machine awake: no sleep, no hibernate on mains power
    5. registers the scheduled task "Mantrac Tower": starts the tower when
       this account signs in, restarts it within a minute if it stops
    6. runs check_server.py and prints what still needs doing

  It never asks for, stores or prints a password. Automatic sign-in is set
  with Microsoft's Autologon tool, which keeps the password encrypted (see
  the guide printed at the end).
#>
param(
  [string]$Account = $env:USERNAME,
  [int]$Port = 8787
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

function Step($text) { Write-Host ""; Write-Host "  $text" -ForegroundColor Cyan }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
          ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Run INSTALL_SERVER.bat (it asks for administrator rights)." }

Step "1. Python and Microsoft Edge"
$Python = $null
foreach ($candidate in @("python", "py")) {
  if (Get-Command $candidate -ErrorAction SilentlyContinue) { $Python = $candidate; break }
}
if (-not $Python) { throw "Python was not found. Install Python 3.11+ from python.org (tick 'Add to PATH')." }
$version = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$version -lt [version]"3.11") { throw "Python 3.11 or newer is required (found $version)." }
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
          "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ }
if (-not $edge) { throw "Microsoft Edge was not found. Install it, then run this again." }
Write-Host "     Python $version, Edge found."

Step "2. Python packages"
& $Python -m pip install --upgrade pip | Out-Null
& $Python -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip could not install the packages (see above)." }

Step "3. Dashboard port $Port, company network only"
netsh advfirewall firewall delete rule name="Mantrac Control Tower" | Out-Null
netsh advfirewall firewall add rule name="Mantrac Control Tower" dir=in action=allow `
  protocol=TCP localport=$Port profile=domain,private | Out-Null
Write-Host "     Allowed on domain and private networks; not on public networks."

Step "4. Never sleep on mains power"
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
Write-Host "     Sleep and hibernate: never (on mains power)."

Step "5. Scheduled task 'Mantrac Tower' for $Account"
$pythonPath = (Get-Command $Python).Source
$action = New-ScheduledTaskAction -Execute $pythonPath `
  -Argument "-m dashboard.supervisor --share --port $Port" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $Account
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew
# Interactive: the automation drives a real Edge window, so it needs the
# account's desktop. Limited: it needs no administrator rights to run.
$principal = New-ScheduledTaskPrincipal -UserId $Account -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "Mantrac Tower" -Action $action -Trigger $trigger `
  -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "     Starts when $Account signs in; restarts within a minute if it stops."

Step "6. Health check"
& $Python (Join-Path $Root "check_server.py")

Write-Host ""
Write-Host "  Still to do by hand (once):" -ForegroundColor Yellow
Write-Host "   - Automatic sign-in for ${Account}: download Autologon from Microsoft"
Write-Host "     (learn.microsoft.com/sysinternals/downloads/autologon), run it, enter the"
Write-Host "     account and password. It stores the password encrypted, not in a file."
Write-Host "   - Put C:\Automation\credentials.txt on this machine, as today."
Write-Host "   - Leaving Remote Desktop: double-click disconnect_keep_running.bat instead of"
Write-Host "     closing the window, so Edge keeps working while nobody is connected."
Write-Host "   - Colleagues open http://$env:COMPUTERNAME`:$Port with the access key."
