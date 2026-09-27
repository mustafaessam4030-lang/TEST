<#
    REAL end-to-end check on Caterpillar SIS: Parts + Troubleshooting + 3D Model.

        .\scripts\windows\investigate.ps1 -Serial JAZ01865

    Uses the existing automation, session and store. Signs in with login.txt
    (or SIS_USERNAME / SIS_PASSWORD). Prints a PASS/FAIL table at the end.
    Nothing here prints or stores your username or password.
#>
param(
    [string]$Serial = "JAZ01865",
    [switch]$Headless
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") { & .\.venv\Scripts\Activate.ps1 }

# Same credential discovery as the launcher: a login* file first, then the
# environment. Windows hides known extensions, so "login.txt" is often really
# "login.txt.txt" — any name starting with "login" is accepted.
if (-not $env:MAIA_SIS_SECRET_REF) {
    $loginFile = Get-ChildItem -Path $repo -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^login(\.|$)' -and $_.Name -notlike 'login.example*' } |
        Select-Object -First 1
    if ($loginFile) {
        $env:MAIA_SIS_SECRET_REF = "file://$($loginFile.Name)"
        Write-Host "Using $($loginFile.Name) for sign-in (contents not shown)." -ForegroundColor Cyan
    } elseif ($env:SIS_USERNAME -and $env:SIS_PASSWORD) {
        $env:MAIA_SIS_SECRET_REF = "env://SIS"
    } else {
        throw "No login.txt in $repo and SIS_USERNAME / SIS_PASSWORD are not set."
    }
}

# Snowflake: if snowflake.txt is here, the lookup is saved there too (and read
# back to prove it). Without it, the local folder only, as before.
if (-not $env:MAIA_REPOSITORY) {
    $sfFile = Get-ChildItem -Path $repo -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^snowflake\.' -and $_.Name -notlike 'snowflake.example*' } |
        Select-Object -First 1
    if ($sfFile) {
        $env:MAIA_REPOSITORY = "snowflake"
        $env:MAIA_SNOWFLAKE_CONFIG_FILE = $sfFile.Name
        Write-Host "Saving to Snowflake as described in $($sfFile.Name) (contents not shown)." -ForegroundColor Cyan
    }
}

$env:PYTHONUTF8 = "1"
$args = @("scripts\e2e\investigate_real.py", "--serial", $Serial)
if ($Headless) { $args += "--headless" }

python @args
$code = $LASTEXITCODE
Write-Host ""
if ($code -eq 0) {
    Write-Host "Real SIS check finished. Results under $repo\logs\sis-results\" -ForegroundColor Green
} else {
    Write-Host "The real SIS check did not pass (exit $code) - the reason is above." -ForegroundColor Red
}
Read-Host "`nPress Enter to close"
exit $code
