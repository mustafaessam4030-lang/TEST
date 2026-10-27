<#
  ATA Control Tower: publish the dashboard at a company address through IIS.

  Run once, on the tower server, in an ADMINISTRATOR PowerShell:

      cd C:\MantracTower\control-tower\deploy\iis
      powershell -ExecutionPolicy Bypass -File .\setup_iis.ps1 -HostName ata.mantrac.com -CertThumbprint <thumbprint>

  To test before there is a certificate (plain http, company network only):

      powershell -ExecutionPolicy Bypass -File .\setup_iis.ps1 -HostName ata.mantrac.com -HttpOnly

  Later, the first command (with the certificate) switches the same site to https.

  What it does (and nothing else):
    1. turns on the IIS web server role (if it is off)
    2. checks that URL Rewrite and Application Request Routing (ARR) are
       installed; if not, it stops and says where to download them
    3. sets the ARR proxy options the tower needs (keep the Host header, no
       response buffering, 30-minute timeout for the live stream)
    4. creates the site "ATA Control Tower" with an https binding for
       -HostName using the certificate -CertThumbprint (and http, which only
       redirects to https)
    5. puts web.config in the site folder
    6. sets ATA_PUBLIC_URL and ATA_TRUST_PROXY for the tower

  Before it: DNS name -> this server, certificate installed in
  LocalMachine\My, and personal sign-in already on (CREATE_ADMIN.bat).
  After it: sign out and in (or restart) so the tower reads the two settings,
  then run check_iis.ps1.

  First run on Windows Server (Azure) in October 2026; IT should still read it first.
#>
param(
    [Parameter(Mandatory = $true)] [string] $HostName,
    [string] $CertThumbprint = "",
    [switch] $HttpOnly,
    [string] $SitePath = "C:\inetpub\ata-control-tower",
    [string] $SiteName = "ATA Control Tower",
    [int] $TowerPort = 8787
)

$ErrorActionPreference = "Stop"
function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "   OK   $text" -ForegroundColor Green }
function Stop-With($text) { Write-Host "   STOP $text" -ForegroundColor Red; exit 1 }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Stop-With "Run this in an Administrator PowerShell." }

$CertThumbprint = ($CertThumbprint -replace '[^0-9A-Fa-f]', '').ToUpper()
if (-not $HttpOnly -and $CertThumbprint.Length -ne 40) {
    Stop-With "Give -CertThumbprint (40 hex characters), or -HttpOnly to test without a certificate first."
}
$scheme = if ($HttpOnly) { "http" } else { "https" }
$client = (Get-CimInstance Win32_OperatingSystem).ProductType -eq 1
if ($client) {
    Write-Host "   WARN this is client Windows (10/11): its IIS serves about 10 requests at once, and each open" -ForegroundColor Yellow
    Write-Host "        dashboard tab holds one. Fine for a trial; use Windows Server for the team." -ForegroundColor Yellow
}

Step "1. IIS web server role"
if (Get-Command Install-WindowsFeature -ErrorAction SilentlyContinue) {
    Install-WindowsFeature Web-Server, Web-Mgmt-Console, Web-Scripting-Tools | Out-Null          # Windows Server
} else {
    Enable-WindowsOptionalFeature -Online -All -NoRestart `
        -FeatureName IIS-WebServerRole, IIS-WebServer, IIS-ManagementConsole, IIS-ManagementScriptingTools | Out-Null  # Windows 10/11
}
Ok "IIS is on"

Step "2. URL Rewrite and Application Request Routing"
$rewrite = Join-Path $env:windir "system32\inetsrv\rewrite.dll"
$arr = Join-Path $env:ProgramFiles "IIS\Application Request Routing\requestRouter.dll"
if (-not (Test-Path $rewrite)) {
    Stop-With "URL Rewrite is not installed. Download it from https://www.iis.net/downloads/microsoft/url-rewrite , install, then run this again."
}
if (-not (Test-Path $arr)) {
    Stop-With "Application Request Routing is not installed. Download it from https://www.iis.net/downloads/microsoft/application-request-routing , install, then run this again."
}
Ok "both installed"

Step "3. ARR proxy settings"
$appcmd = Join-Path $env:windir "system32\inetsrv\appcmd.exe"
# These are server-wide: any other ARR site on this server gets them too.
& $appcmd set config -section:system.webServer/proxy /enabled:"True" /preserveHostHeader:"True" `
    /reverseRewriteHostInResponseHeaders:"False" /timeout:"00:30:00" /responseBufferLimit:"0" /includePortInXForwardedFor:"False" /commit:apphost | Out-Null
if ($LASTEXITCODE -ne 0) { Stop-With "appcmd could not set the proxy settings." }
Ok "proxy on, Host header kept, no buffering, 30 min timeout"

Step "4. Certificate"
if ($HttpOnly) {
    Write-Host "   SKIP http test: no certificate yet (sign-ins are not encrypted on the network - use test accounts only)" -ForegroundColor Yellow
} else {
$cert = Get-Item "Cert:\LocalMachine\My\$CertThumbprint" -ErrorAction SilentlyContinue
if (-not $cert) { Stop-With "No certificate $CertThumbprint in LocalMachine\My. Install it first." }
if (-not $cert.HasPrivateKey) { Stop-With "The certificate has no private key on this server. Import it as a .pfx with its key." }
$names = @($cert.DnsNameList | ForEach-Object { $_.Unicode })
if ($names -notcontains $HostName -and -not ($names | Where-Object { $_ -like "*.*" -and $HostName -like $_ })) {
    Write-Host "   WARN the certificate names: $($names -join ', ') - check it covers $HostName" -ForegroundColor Yellow
}
if ($cert.NotAfter -lt (Get-Date).AddDays(14)) {
    Write-Host "   WARN the certificate expires on $($cert.NotAfter)" -ForegroundColor Yellow
}
Ok "certificate found (expires $($cert.NotAfter.ToString('dd/MM/yyyy')))"
}

Step "5. Site '$SiteName'"
Import-Module WebAdministration
New-Item -ItemType Directory -Force -Path $SitePath | Out-Null
$source = if ($HttpOnly) { "web.http-test.config" } else { "web.config" }
$config = Get-Content (Join-Path $PSScriptRoot $source) -Raw
$config = $config -replace "127\.0\.0\.1:8787", "127.0.0.1:$TowerPort"
Set-Content -Path (Join-Path $SitePath "web.config") -Value $config -Encoding UTF8
if (Test-Path "IIS:\Sites\$SiteName") {
    Set-ItemProperty "IIS:\Sites\$SiteName" -Name physicalPath -Value $SitePath
    # A new name (e.g. from the http test on the server's name to the real
    # one): bindings for any other name are removed, an http one added.
    Get-WebBinding -Name $SiteName | Where-Object { ($_.bindingInformation -split ":")[-1] -ne $HostName } |
        ForEach-Object { Remove-WebBinding -Name $SiteName -BindingInformation $_.bindingInformation -Protocol $_.protocol }
    if (-not (Get-WebBinding -Name $SiteName -Protocol http -HostHeader $HostName -ErrorAction SilentlyContinue)) {
        New-WebBinding -Name $SiteName -Protocol http -Port 80 -HostHeader $HostName
    }
    Ok "site exists; it now answers $HostName, uses $SitePath and the new web.config"
} else {
    New-Website -Name $SiteName -PhysicalPath $SitePath -HostHeader $HostName -Port 80 | Out-Null
    Ok "site created"
}
if (-not $HttpOnly) {
    if (-not (Get-WebBinding -Name $SiteName -Protocol https -HostHeader $HostName -ErrorAction SilentlyContinue)) {
        New-WebBinding -Name $SiteName -Protocol https -Port 443 -HostHeader $HostName -SslFlags 1
    }
    $binding = Get-WebBinding -Name $SiteName -Protocol https -HostHeader $HostName
    $ssl = Get-Item "IIS:\SslBindings\!443!$HostName" -ErrorAction SilentlyContinue
    if ($ssl -and $ssl.Thumbprint -ne $CertThumbprint) { $binding.RemoveSslCertificate(); $ssl = $null }
    if (-not $ssl) { $binding.AddSslCertificate($CertThumbprint, "My") }
}
Start-Website -Name $SiteName
if ((Get-Website -Name $SiteName).State -ne "Started") {
    Stop-With "The site did not start. Is port 80 or 443 used by another site?"
}
Ok "${scheme}://$HostName -> http://127.0.0.1:$TowerPort"

Step "6. Tower settings"
[Environment]::SetEnvironmentVariable("ATA_PUBLIC_URL", "${scheme}://$HostName", "Machine")
[Environment]::SetEnvironmentVariable("ATA_TRUST_PROXY", "1", "Machine")
Ok "ATA_PUBLIC_URL=${scheme}://$HostName, ATA_TRUST_PROXY=1"

Write-Host ""
Write-Host "Done. Now:" -ForegroundColor Cyan
Write-Host "  1. Sign out of Windows and in again (or restart) so the tower reads the new settings."
$flag = if ($HttpOnly) { " -HttpOnly" } else { "" }
Write-Host "  2. Run: powershell -ExecutionPolicy Bypass -File .\check_iis.ps1 -HostName $HostName$flag"
Write-Host "  3. Optional: remove the firewall rule 'Mantrac Control Tower' so people use only https."
