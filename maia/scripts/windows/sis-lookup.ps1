<#
    One REAL Caterpillar SIS lookup, saved to logs\sis-results\.

        .\scripts\windows\sis-lookup.ps1 -Serial JAZ01865

    It signs in with the details in login.txt (or SIS_USERNAME / SIS_PASSWORD),
    runs the same automation the gateway runs, saves the JSON, the TXT and the
    three screenshots, then prints where everything landed and which fields
    were actually captured.

    Use -Headed if sign-in asks for MFA: the browser stays visible so you can
    complete it in that window.

    Nothing here prints or stores your username or password.
#>
param(
    [Parameter(Mandatory = $true)][string]$Serial,
    [switch]$Headed
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

$env:PYTHONUTF8 = "1"
$args = @("scripts\e2e\sis_lookup.py", "--serial", $Serial)
if ($Headed) { $args += "--headed" }

python @args
$code = $LASTEXITCODE
Write-Host ""
if ($code -eq 0) {
    Write-Host "Saved under $repo\logs\sis-results\" -ForegroundColor Green
} else {
    Write-Host "The lookup did not complete (exit $code). Nothing was saved." -ForegroundColor Red
    Write-Host "Run it again with -Headed to watch the browser." -ForegroundColor Yellow
}
Read-Host "`nPress Enter to close"
exit $code
