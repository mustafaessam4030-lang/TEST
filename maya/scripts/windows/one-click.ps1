<#
    Maya — one-click start.

    Installs only what is missing, reuses anything already on this PC, reads the
    SIS sign-in details from login.txt if you made one, and starts everything.

    Safe to run again; the slow parts happen once.
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo

function Say([string]$t, [string]$c = "White") { Write-Host $t -ForegroundColor $c }
function Step([int]$n, [string]$t) { Write-Host "`n[$n/5] $t" -ForegroundColor Cyan }
function Fail([string]$t) { Say "`n$t" Red; Read-Host "`nPress Enter to close"; exit 1 }

Say "`n===============================================================" Yellow
Say "  MAYA - Equipment data from Caterpillar SIS" Yellow
Say "===============================================================" Yellow

# ── 1. Find a Python that has prebuilt packages ─────────────────────────────
# Brand-new Python releases have no wheels yet, so pip tries to COMPILE things
# like pyyaml and fails on a PC without a C++ toolchain. Prefer a settled one.
Step 1 "Finding a suitable Python"
$candidates = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.13", "3.12", "3.11", "3.10")) { $candidates += ,@("py", @("-$v")) }
}
foreach ($exe in @("python", "python3")) {
    if (Get-Command $exe -ErrorAction SilentlyContinue) { $candidates += ,@($exe, @()) }
}

$py = $null; $pyArgs = @(); $pyVer = $null; $fallback = $null
foreach ($c in $candidates) {
    $exe = $c[0]; $a = $c[1]
    try { $v = & $exe @a -c "import sys;print('.'.join(map(str,sys.version_info[:2])))" 2>$null }
    catch { continue }
    if (-not $v) { continue }
    $ver = [version]$v
    if ($ver -ge [version]"3.10" -and $ver -lt [version]"3.14") {
        $py = $exe; $pyArgs = $a; $pyVer = $v; break
    }
    if (-not $fallback -and $ver -ge [version]"3.10") { $fallback = @($exe, $a, $v) }
}

if (-not $py -and $fallback) {
    $py = $fallback[0]; $pyArgs = $fallback[1]; $pyVer = $fallback[2]
    Say "      Only Python $pyVer found. It is newer than the packages have" Yellow
    Say "      builds for, so the install may fail. If it does, install Python" Yellow
    Say "      3.12 from python.org and run this again." Yellow
}
if (-not $py) {
    Fail @"
No suitable Python found.

Install Python 3.12 from https://www.python.org/downloads/release/python-3128/
and tick 'Add python.exe to PATH' during the install. Then run this file again.
"@
}
Say "      Using Python $pyVer ($py $pyArgs)" Green

# ── 2. Environment ──────────────────────────────────────────────────────────
Step 2 "Preparing the environment"
$venvPy = Join-Path $repo ".venv\Scripts\python.exe"
$marker = Join-Path $repo ".venv\.maya-ready"

if (-not (Test-Path $venvPy)) {
    Say "      Creating a workspace that reuses what you already have installed..."
    # --system-site-packages: your existing Playwright and its browsers are used
    # instead of being downloaded again.
    & $py @pyArgs -m venv --system-site-packages .venv
    if (-not (Test-Path $venvPy)) { Fail "Could not create the .venv folder." }
}

$needed = & $venvPy -c @"
import importlib.util as u
mods = {'fastapi':'fastapi','uvicorn':'uvicorn[standard]','yaml':'pyyaml>=6.0.2',
        'pydantic':'pydantic','pydantic_settings':'pydantic-settings','httpx':'httpx',
        'playwright':'playwright'}
print(' '.join(pkg for mod, pkg in mods.items() if u.find_spec(mod) is None))
"@ 2>$null

if ($needed) {
    Say "      Installing: $needed" Yellow
    & $venvPy -m pip install --upgrade pip --quiet
    & $venvPy -m pip install --only-binary=:all: $needed.Split(" ")
    if ($LASTEXITCODE -ne 0) {
        Say "      Prebuilt packages were not available; trying a normal install..." Yellow
        & $venvPy -m pip install $needed.Split(" ")
    }
    $still = & $venvPy -c @"
import importlib.util as u
print(' '.join(m for m in ['fastapi','uvicorn','yaml','pydantic','pydantic_settings','httpx','playwright']
                if u.find_spec(m) is None))
"@ 2>$null
    if ($still) {
        Fail @"
These are still missing: $still

That almost always means this Python version has no prebuilt packages yet.
Install Python 3.12 from python.org, delete the .venv folder in this project,
and run this file again.
"@
    }
} else {
    Say "      Everything needed is already installed." Green
}

# Chromium: a no-op if you already have it.
$hasBrowser = & $venvPy -c @"
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        import os
        print('yes' if os.path.exists(p.chromium.executable_path) else 'no')
except Exception:
    print('no')
"@ 2>$null
if ($hasBrowser -ne "yes") {
    Say "      Installing the browser Playwright drives (once)..." Yellow
    & $venvPy -m playwright install chromium
} else {
    Say "      Chromium already present." Green
}
New-Item -ItemType File -Path $marker -Force | Out-Null

# ── 3. Sign-in details ──────────────────────────────────────────────────────
Step 3 "SIS sign-in details"
$loginFile = if (Test-Path (Join-Path $repo "login.txt")) { "login.txt" }
             elseif (Test-Path (Join-Path $repo "login")) { "login" } else { $null }

if ($loginFile) {
    $env:MAYA_SIS_SECRET_REF = "file://$loginFile"
    Say "      Reading them from $loginFile - nothing to type." Green
} elseif ($env:SIS_USERNAME -and $env:SIS_PASSWORD) {
    $env:MAYA_SIS_SECRET_REF = "env://SIS"
    Say "      Using the ones already set in this window." Green
} else {
    Say "      No login.txt found. Create one next to START-MAYA.bat with:" Yellow
    Say "          username=YOUR.SIS.USERNAME" Yellow
    Say "          password=YOUR SIS PASSWORD" Yellow
    Say "      (copy login.example.txt and rename it to login.txt)" Yellow
    Say "`n      Or type them now, for this window only:" Yellow
    $env:SIS_USERNAME = Read-Host "`n      SIS username"
    $secure = Read-Host "      SIS password (typing is hidden)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:SIS_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if (-not $env:SIS_USERNAME -or -not $env:SIS_PASSWORD) { Fail "Both a username and a password are needed." }
    $env:MAYA_SIS_SECRET_REF = "env://SIS"
}

# ── 4. Readiness ────────────────────────────────────────────────────────────
Step 4 "Checking everything is ready"
& $venvPy scripts\e2e\doctor.py
if ($LASTEXITCODE -ne 0) {
    Say "`nNot everything is ready - see the lines marked x above." Yellow
    Say "'SIS selector contract' missing is NORMAL on the first run: Maya learns" Yellow
    Say "the SIS pages during your first search." Yellow
    $go = Read-Host "`nStart anyway? (Y/n)"
    if ($go -and $go.ToLower() -ne "y") { exit 1 }
}

# ── 5. Go ───────────────────────────────────────────────────────────────────
Step 5 "Starting Maya"
Say @"

      Your browser will open with the Maya chat.
      Click the yellow bubble at the bottom right and type:

          Maya, find equipment data for serial number <your serial>

      Use a serial you KNOW exists in SIS for the first search: Maya uses it
      to learn the SIS pages, which takes a few minutes. Later searches are
      seconds.

      A second browser window opens on its own - that is the automation.
      Leave it alone unless SIS asks for a verification code; type the code
      in that window and Maya continues.

      Close this black window to stop everything.

"@ White

Start-Sleep -Seconds 2
Start-Process "http://127.0.0.1:5173/maya.html"
& $venvPy scripts\e2e\run_e2e.py --source cat_sis
