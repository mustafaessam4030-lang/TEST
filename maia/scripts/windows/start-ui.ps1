<#
    Restart ONLY the local chat + gateway.

    Starts no automation and touches no browser session: any SIS window you
    already have open is left exactly as it is. Use this when the chat page
    says ERR_CONNECTION_REFUSED but you do not want to disturb a sign-in.

        .\scripts\windows\start-ui.ps1
#>
param([int]$ApiPort = 8080, [int]$UiPort = 5173)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
$venvPy = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = "python" }

Write-Host "`nStarting the chat and gateway only - no automation, no browser touched." -ForegroundColor Cyan
& $venvPy scripts\e2e\run_e2e.py --api-port $ApiPort --ui-port $UiPort --open-browser
