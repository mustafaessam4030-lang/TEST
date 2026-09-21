<#
    Maya — one-click start.

    Does the whole thing: installs what is missing the first time, asks for the
    SIS credentials if they are not already in this session, checks readiness,
    then starts the gateway, the worker and the chat.

    Safe to run again. The slow parts only happen once.
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo

function Say([string]$text, [string]$colour = "White") { Write-Host $text -ForegroundColor $colour }
function Step([int]$n, [string]$text) { Write-Host "`n[$n/5] $text" -ForegroundColor Cyan }

Say "`n===============================================================" Yellow
Say "  MAYA - Equipment data from Caterpillar SIS" Yellow
Say "===============================================================" Yellow

# ── 1. Python ───────────────────────────────────────────────────────────────
Step 1 "Checking Python"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Say "`nPython is not installed." Red
    Say "Install it from https://www.python.org/downloads/ - tick 'Add python.exe to PATH'"
    Say "during the install, then run this file again."
    Read-Host "`nPress Enter to close"
    exit 1
}
$pyVersion = (python -c "import sys;print('.'.join(map(str,sys.version_info[:2])))" 2>$null)
if (-not $pyVersion -or [version]$pyVersion -lt [version]"3.10") {
    Say "`nPython $pyVersion found, but 3.10 or newer is needed." Red
    Say "Install a newer one from https://www.python.org/downloads/ and run this file again."
    Read-Host "`nPress Enter to close"
    exit 1
}
Say "      Python $pyVersion - OK" Green

# ── 2. First-time install ───────────────────────────────────────────────────
Step 2 "Preparing the environment"
$marker = Join-Path $repo ".venv\.maya-ready"
if (-not (Test-Path $marker)) {
    Say "      First run - installing. This takes a few minutes, once." Yellow
    if (-not (Test-Path ".venv")) { python -m venv .venv }
    & .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip --quiet
    python -m pip install -r services\automation\requirements-dev.txt --quiet
    Say "      Installing the browser Playwright drives..." Yellow
    python -m playwright install chromium
    New-Item -ItemType File -Path $marker -Force | Out-Null
    Say "      Installed." Green
} else {
    & .\.venv\Scripts\Activate.ps1
    Say "      Already installed - skipping." Green
}

# ── 3. Credentials ──────────────────────────────────────────────────────────
Step 3 "SIS sign-in details"
if ($env:SIS_USERNAME -and $env:SIS_PASSWORD) {
    Say "      Already set for this session ($env:SIS_USERNAME)." Green
} else {
    Say "      Used only by the automation on this PC. Never saved to disk," Yellow
    Say "      never sent anywhere except Caterpillar's own sign-in page." Yellow
    $env:SIS_USERNAME = Read-Host "`n      SIS username"
    $secure = Read-Host "      SIS password (typing is hidden)" -AsSecureString
    # Works on both Windows PowerShell 5.1 and PowerShell 7.
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $env:SIS_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    if (-not $env:SIS_USERNAME -or -not $env:SIS_PASSWORD) {
        Say "`nBoth a username and a password are needed." Red
        Read-Host "Press Enter to close"; exit 1
    }
    Say "      Set for this window only." Green
}
$env:MAYA_SIS_SECRET_REF = "env://SIS"

# ── 4. Readiness ────────────────────────────────────────────────────────────
Step 4 "Checking everything is ready"
python scripts\e2e\doctor.py
if ($LASTEXITCODE -ne 0) {
    Say "`nNot everything is ready - see the lines marked with an x above." Yellow
    Say "Missing SIS selectors is normal on the very first run: Maya learns them" Yellow
    Say "during your first search." Yellow
    $go = Read-Host "`nStart anyway? (Y/n)"
    if ($go -and $go.ToLower() -ne "y") { exit 1 }
}

# ── 5. Go ───────────────────────────────────────────────────────────────────
Step 5 "Starting Maya"
Say @"

      A browser window will open with the Maya chat.
      Click the yellow chat bubble at the bottom right and type:

          Maya, find equipment data for serial number <your serial>

      Use a serial you KNOW exists in SIS for the first search.
      The first one takes a few minutes while Maya learns the SIS pages;
      after that they take seconds.

      A second browser window will open on its own - that is the
      automation working. Leave it alone unless SIS asks for a
      verification code, in which case type it there.

      Close this black window to stop everything.

"@ White

Start-Sleep -Seconds 2
Start-Process "http://127.0.0.1:5173/maya.html"
python scripts\e2e\run_e2e.py --source cat_sis
