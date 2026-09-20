<#
    Maya — one-time setup on a Windows PC that has SIS access.
    Installs the Python dependencies and the Chromium build Playwright needs.

    Run in PowerShell from the repo root:
        .\scripts\windows\setup.ps1
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo

Write-Host "`n=== Maya setup ===" -ForegroundColor Yellow

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "Python not found. Install Python 3.11+ from https://python.org and re-run." }
$version = (python -c "import sys;print('.'.join(map(str,sys.version_info[:2])))")
Write-Host "Python $version"
if ([version]$version -lt [version]"3.10") { throw "Python 3.10+ is required (found $version)." }

Write-Host "`n[1/3] Creating the virtual environment..." -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1

Write-Host "[2/3] Installing Python packages..." -ForegroundColor Cyan
python -m pip install --upgrade pip --quiet
python -m pip install -r services\automation\requirements-dev.txt

Write-Host "[3/3] Installing the Chromium build Playwright expects..." -ForegroundColor Cyan
python -m playwright install chromium

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host @"

Next, set your SIS credentials for THIS PowerShell session only
(they never touch the repo, the gateway log, or the model):

    `$env:SIS_USERNAME = "your.sis.username"
    `$env:SIS_PASSWORD = Read-Host "SIS password" -AsSecureString | ConvertFrom-SecureString -AsPlainText

To persist them for your Windows user instead (stored by Windows, not by us):

    setx SIS_USERNAME "your.sis.username"
    setx SIS_PASSWORD "your-password"        # then open a NEW terminal

Then capture the SIS selectors once:

    .\scripts\windows\capture-sis.ps1 -Serial <a serial that exists in SIS>

"@
