<#
    Start the whole stack on this PC:
      Maia chat (browser)  →  tool gateway  →  Playwright worker  →  CAT SIS

    The worker runs HERE, reads the credentials from this session's environment,
    and opens a visible browser so you can see it work and complete MFA if asked.

        .\scripts\windows\start-maia.ps1
#>
param(
    [int]$ApiPort = 8080,
    [int]$UiPort  = 5173,
    [switch]$Headless                        # hide the worker browser
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
& .\.venv\Scripts\Activate.ps1

if (-not $env:SIS_USERNAME -or -not $env:SIS_PASSWORD) {
    Write-Host "WARNING: SIS_USERNAME / SIS_PASSWORD are not set — SIS lookups will fail with LOGIN_FAILED." -ForegroundColor Yellow
}
if (-not (Test-Path "config\sis_selectors.json")) {
    Write-Host "WARNING: config\sis_selectors.json is missing — SIS lookups will stop at WEBSITE_CHANGED." -ForegroundColor Yellow
    Write-Host "         Run .\scripts\windows\capture-sis.ps1 -Serial <real serial> first."
}

# The worker resolves credentials from env://SIS -> SIS_USERNAME / SIS_PASSWORD.
$env:MAIA_SIS_SECRET_REF = "env://SIS"

$args = @("scripts\e2e\run_e2e.py", "--api-port", $ApiPort, "--ui-port", $UiPort, "--source", "cat_sis")
if ($Headless) { $args += "--headless" }

Write-Host "`nStarting the gateway, the worker and the Maia UI..." -ForegroundColor Cyan
Start-Process "http://127.0.0.1:$UiPort/maia.html"
python @args
