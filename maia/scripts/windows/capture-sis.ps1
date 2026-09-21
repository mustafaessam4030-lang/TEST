<#
    Capture the real SIS selectors automatically.

    Signs in with $env:SIS_USERNAME / $env:SIS_PASSWORD, then discovers each
    selector and PROVES it by using it: a serial that exists must produce a
    result, one that does not must produce the empty state. Nothing is guessed.

    The browser is visible. If SIS asks for MFA the script pauses and you
    complete it in that window; it then carries on in the same session.

        .\scripts\windows\capture-sis.ps1 -Serial CAT0336LKBW00123
#>
param(
    [Parameter(Mandatory = $true)][string]$Serial,
    [string]$MissingSerial = "ZZZ00000",
    [switch]$Manual                         # click each element yourself instead
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
& .\.venv\Scripts\Activate.ps1

if (-not $env:SIS_USERNAME -or -not $env:SIS_PASSWORD) {
    throw "SIS_USERNAME / SIS_PASSWORD are not set in this session. See setup.ps1."
}
Write-Host "Signing in as $env:SIS_USERNAME (password not shown)" -ForegroundColor Cyan

$mode = if ($Manual) { "--serial" } else { "--auto" }
if ($Manual) {
    python scripts\capture\capture_selectors.py --serial $Serial --missing-serial $MissingSerial
} else {
    python scripts\capture\capture_selectors.py --auto --serial $Serial --missing-serial $MissingSerial
}

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nSelectors captured → config\sis_selectors.json" -ForegroundColor Green
    Write-Host "Now start everything:  .\scripts\windows\start-maia.ps1"
} else {
    Write-Host "`nCapture did not complete (exit $LASTEXITCODE)." -ForegroundColor Red
    Write-Host "Read artifacts\capture\<run>\report.json — every step and screenshot is there."
    Write-Host "Anything it could not prove is marked TODO_CAPTURE rather than guessed."
    Write-Host "Try the manual picker:  .\scripts\windows\capture-sis.ps1 -Serial $Serial -Manual"
}
