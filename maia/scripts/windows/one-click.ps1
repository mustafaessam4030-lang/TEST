<#
    Maia — one-click start.

    Installs only what is missing, reuses anything already on this PC, reads the
    SIS sign-in details from login.txt if you made one, and starts everything.

    Safe to run again; the slow parts happen once.
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo

# The project's files are UTF-8. Without this, Python on Windows reads them in
# the locale code page (cp1252 on an English install), which cannot decode the
# Arabic in the chat page.
$env:PYTHONUTF8 = "1"

# Record everything to a file. If this window ever disappears, the reason is
# in logs\maia-start.log rather than lost with the window.
$logDir = Join-Path $repo "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logFile = Join-Path $logDir "maia-start.log"
try { Start-Transcript -Path $logFile -Force | Out-Null } catch { }

# Any unhandled PowerShell error must be shown, not swallowed by a closing window.
trap {
    Write-Host "`nUNEXPECTED ERROR" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    Write-Host "`nFull log: $logFile" -ForegroundColor Yellow
    try { Stop-Transcript | Out-Null } catch { }
    Read-Host "`nPress Enter to close"
    exit 1
}

function Say([string]$t, [string]$c = "White") { Write-Host $t -ForegroundColor $c }
function Step([int]$n, [string]$t) { Write-Host "`n[$n/5] $t" -ForegroundColor Cyan }
function Fail([string]$t) { Say "`n$t" Red; Read-Host "`nPress Enter to close"; exit 1 }

Say "`n===============================================================" Yellow
Say "  MAIA - Equipment data from Caterpillar SIS" Yellow
Say "===============================================================" Yellow

# ── 1. Find a Python that has prebuilt packages ─────────────────────────────
# Brand-new Python releases have no published wheels, so pip tries to COMPILE
# pyyaml, pydantic-core and friends and fails on a PC without a C++ toolchain.
# We need a settled version; if there is none, we fetch one.
Step 1 "Finding a suitable Python"

function Find-Python {
    $tried = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @("3.13", "3.12", "3.11", "3.10")) { $tried += ,@("py", @("-$v")) }
    }
    foreach ($exe in @("python", "python3")) {
        if (Get-Command $exe -ErrorAction SilentlyContinue) { $tried += ,@($exe, @()) }
    }
    # A Python installed by this script in a previous run.
    $local = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $local) { $tried = ,@($local, @()) + $tried }

    foreach ($c in $tried) {
        try { $v = & $c[0] @($c[1]) -c "import sys;print('.'.join(map(str,sys.version_info[:2])))" 2>$null }
        catch { continue }
        if (-not $v) { continue }
        $ver = [version]$v
        if ($ver -ge [version]"3.10" -and $ver -lt [version]"3.14") {
            return @{ Exe = $c[0]; Args = $c[1]; Version = $v }
        }
    }
    return $null
}

$found = Find-Python
if (-not $found) {
    Say "      No settled Python found (3.10-3.13)." Yellow
    Say "      Installing Python 3.12 for your user account - no admin needed." Yellow
    $installed = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install -e --id Python.Python.3.12 --scope user --silent `
                   --accept-package-agreements --accept-source-agreements
            $installed = ($LASTEXITCODE -eq 0)
        } catch { $installed = $false }
    }
    if (-not $installed) {
        Say "      winget could not do it; downloading the installer instead..." Yellow
        $url = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
        $exe = Join-Path $env:TEMP "python-3.12.8-amd64.exe"
        try {
            Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
            Say "      Running the installer (user-level, adds itself to PATH)..." Yellow
            Start-Process -FilePath $exe -Wait -ArgumentList @(
                "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1", "Include_pip=1")
            $installed = $true
        } catch {
            Fail @"
Could not install Python automatically: $($_.Exception.Message)

Install Python 3.12 yourself from
  https://www.python.org/downloads/release/python-3128/
tick 'Add python.exe to PATH', then run this file again.
"@
        }
    }
    # Pick up the new PATH without needing a new terminal.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
    $found = Find-Python
    if (-not $found) {
        Fail @"
Python 3.12 was installed but this window cannot see it yet.

Close this window and double-click START-MAIA.bat again - that is all it needs.
"@
    }
    Say "      Installed Python $($found.Version)." Green
}

$py = $found.Exe; $pyArgs = $found.Args; $pyVer = $found.Version
Say "      Using Python $pyVer" Green

# ── 2. Environment ──────────────────────────────────────────────────────────
Step 2 "Preparing the environment"
$venvPy = Join-Path $repo ".venv\Scripts\python.exe"
$marker = Join-Path $repo ".venv\.maia-ready"

if (Test-Path $venvPy) {
    # If an earlier run left a venv built on an unusable Python, start over.
    $venvVer = & $venvPy -c "import sys;print('.'.join(map(str,sys.version_info[:2])))" 2>$null
    if (-not $venvVer -or [version]$venvVer -ge [version]"3.14") {
        Say "      Removing a workspace built on Python $venvVer (no packages exist for it)." Yellow
        Remove-Item -Recurse -Force (Join-Path $repo ".venv")
    }
}
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

# Windows hides known extensions, so a file the user renamed to "login.txt" is
# very often really "login.txt.txt". Accept any login* file rather than making
# them fight Explorer.
$loginFile = Get-ChildItem -Path $repo -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^login(\.|$)' -and $_.Name -notlike 'login.example*' } |
    Select-Object -First 1

# They may also have filled in the example file without renaming it.
if (-not $loginFile) {
    $example = Join-Path $repo "login.example.txt"
    if (Test-Path $example) {
        $body = Get-Content $example -Raw
        if ($body -notmatch 'YOUR\.SIS\.USERNAME') {
            $loginFile = Get-Item $example
            Say "      Using login.example.txt - it has been filled in." Yellow
        }
    }
}

if ($loginFile) {
    $content = Get-Content $loginFile.FullName -Raw
    if ($content -match 'YOUR\.SIS\.USERNAME' -or $content -match 'YOUR SIS PASSWORD') {
        Say "      $($loginFile.Name) still has the example text in it." Red
        Say "      Open it and replace both lines with your real details." Red
        Fail "Edit $($loginFile.FullName) and run this again."
    }
    $env:MAIA_SIS_SECRET_REF = "file://$($loginFile.Name)"
    Say "      Reading them from $($loginFile.Name) - nothing to type." Green
} elseif ($env:SIS_USERNAME -and $env:SIS_PASSWORD) {
    $env:MAIA_SIS_SECRET_REF = "env://SIS"
    Say "      Using the ones already set in this window." Green
} else {
    Say "      No login file found in:" Yellow
    Say "        $repo" Yellow
    $present = (Get-ChildItem -Path $repo -File -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty Name) -join ", "
    Say "      That folder contains: $present" Yellow
    Say ""
    Say "      Tip: Windows hides file extensions, so a file you renamed to" Yellow
    Say "      'login.txt' may really be 'login.txt.txt'. Any name starting" Yellow
    Say "      with 'login' works - the list above shows the real names." Yellow
    Say "`n      Or type them now, for this window only:" Yellow
    $env:SIS_USERNAME = Read-Host "`n      SIS username"
    $secure = Read-Host "      SIS password (typing is hidden)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:SIS_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if (-not $env:SIS_USERNAME -or -not $env:SIS_PASSWORD) { Fail "Both a username and a password are needed." }
    $env:MAIA_SIS_SECRET_REF = "env://SIS"
}

# ── 3b. Snowflake (only if snowflake.txt exists) ────────────────────────────
# No snowflake.txt: results stay in logs\sis-results\ exactly as before.
# With one: every lookup is saved to Snowflake, and the local folder is kept
# alongside as evidence (JSON, TXT, screenshots).
$sfFile = Get-ChildItem -Path $repo -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^snowflake\.' -and $_.Name -notlike 'snowflake.example*' } |
    Select-Object -First 1
if ($sfFile -and -not $env:MAIA_REPOSITORY) {
    Say "      Snowflake: using $($sfFile.Name) (contents not shown)." Cyan
    $hasDriver = & $venvPy -c "import importlib.util as u;print('yes' if u.find_spec('snowflake.connector') else 'no')" 2>$null
    if ($hasDriver -ne "yes") {
        Say "      Installing the Snowflake driver (once)..." Yellow
        & $venvPy -m pip install --only-binary=:all: "snowflake-connector-python[secure-local-storage]" --quiet
        if ($LASTEXITCODE -ne 0) { & $venvPy -m pip install "snowflake-connector-python[secure-local-storage]" --quiet }
    }
    $env:MAIA_SNOWFLAKE_CONFIG_FILE = $sfFile.Name
    & $venvPy scripts\snowflake\snowflake_setup.py check
    if ($LASTEXITCODE -eq 0) {
        $env:MAIA_REPOSITORY = "snowflake"
        Say "      Snowflake is ready - every lookup will be saved there." Green
    } else {
        Say "      Snowflake is NOT ready (reason above)." Red
        Say "      Double-click SNOWFLAKE-SETUP.bat to create the tables," Yellow
        Say "      or fix snowflake.txt." Yellow
        $go = Read-Host "`n      Continue with the local folder only for now? (Y/n)"
        if ($go -and $go.ToLower() -ne "y") { exit 1 }
        $env:MAIA_REPOSITORY = "local_json"
    }
}

# ── 4. Readiness ────────────────────────────────────────────────────────────
Step 4 "Checking everything is ready"
& $venvPy scripts\e2e\doctor.py
if ($LASTEXITCODE -ne 0) {
    Say "`nNot everything is ready - see the lines marked x above." Yellow
    Say "'SIS selector contract' missing is NORMAL on the first run: Maia learns" Yellow
    Say "the SIS pages during your first search." Yellow
    $go = Read-Host "`nStart anyway? (Y/n)"
    if ($go -and $go.ToLower() -ne "y") { exit 1 }
}

# ── 5. Go ───────────────────────────────────────────────────────────────────
Step 5 "Starting Maia"
Say @"

      Your browser will open with the Maia chat.
      Click the yellow bubble at the bottom right and type:

          Maia, find equipment data for serial number <your serial>

      Use a serial you KNOW exists in SIS for the first search: Maia uses it
      to learn the SIS pages, which takes a few minutes. Later searches are
      seconds.

      A second browser window opens on its own - that is the automation.
      Leave it alone unless SIS asks for a verification code; type the code
      in that window and Maia continues.

      Close this black window to stop everything.

"@ White

# run_e2e opens the page itself, once the server is actually listening.
# It runs in the foreground: while it is alive, the UI is on 5173 and the tool
# gateway on 8080. Closing this window stops both, so we hold it open even when
# the process dies, and say exactly why.
& $venvPy scripts\e2e\run_e2e.py --source cat_sis --open-browser
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Say "Maia stopped normally (exit code 0)." Yellow
} else {
    Say "Maia exited with code $code - it did NOT stay running." Red
    Say "The error is immediately above, and in:" Yellow
    Say "  $logFile" Yellow
    Say ""
    Say "To see it again without this window closing, run:" Yellow
    Say "  cd `"$repo`"" White
    Say "  `$env:PYTHONUTF8 = `"1`"" White
    Say "  .\.venv\Scripts\python.exe scripts\e2e\run_e2e.py --source cat_sis" White
}
try { Stop-Transcript | Out-Null } catch { }
Read-Host "`nPress Enter to close"
exit $code
