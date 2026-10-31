<#
  ATA Control Tower: everything for https://<name> in one go, on the tower
  server. SETUP_HTTPS.bat (in the control-tower folder) runs this as
  Administrator.

  Put the company certificate (.pfx) in C:\Automation\certificate (or C:\),
  double-click SETUP_HTTPS.bat, type the certificate password when asked.

  In order, skipping whatever is already done (safe to run again):
    1. IIS role (stops and asks for a restart if Windows needs one)
    2. URL Rewrite and ARR from Microsoft's download site, installed only
       when the download is validly signed by Microsoft
    3. the certificate: one already in LocalMachine\My that covers the name,
       else the .pfx is imported (password typed here, never stored) and the
       .pfx file is deleted once it is in Windows
    4. firewall: 80 and 443 from company (private) addresses only
    5. this server reaches the name itself (hosts: 127.0.0.1), before and
       after DNS
    6. setup_iis.ps1 with the certificate's thumbprint
    7. restarts the tower's task so it reads its new address, then
       check_iis.ps1

  It never writes a password anywhere and never prints one.
#>
param(
    [string] $HostName = "ata.mantracgroup.com",
    [string] $CertFolder = ""
)

$ErrorActionPreference = "Stop"
function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "   OK   $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   WARN $text" -ForegroundColor Yellow }
function Stop-With($text) { Write-Host "   STOP $text" -ForegroundColor Red; exit 1 }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Stop-With "Run this as Administrator (SETUP_HTTPS.bat asks for it)." }

$Tower = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent          # ...\control-tower
if (-not $CertFolder) { $CertFolder = Join-Path (Split-Path $Tower -Parent) "certificate" }

Step "1. IIS"
if (Get-Command Install-WindowsFeature -ErrorAction SilentlyContinue) {
    $r = Install-WindowsFeature Web-Server, Web-Mgmt-Console, Web-Scripting-Tools
    if ("$($r.RestartNeeded)" -eq "Yes") { Stop-With "IIS was installed and Windows needs a restart. Restart, then run SETUP_HTTPS.bat again." }
} else {
    Stop-With "This is not Windows Server: turn on IIS in 'Windows features', then run this again."
}
Ok "IIS is on"

Step "2. URL Rewrite and Application Request Routing (Microsoft)"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"
$dl = Join-Path $env:TEMP "ata-iis-setup"
New-Item -ItemType Directory $dl -Force | Out-Null
$parts = @(
    @{ n = "URL Rewrite"; f = "$env:windir\system32\inetsrv\rewrite.dll"; m = "$dl\rewrite.msi"
       u = "https://download.microsoft.com/download/1/2/8/128E2E22-C1B9-44A4-BE2A-5859ED1D4592/rewrite_amd64_en-US.msi" },
    @{ n = "Application Request Routing"; f = "$env:ProgramFiles\IIS\Application Request Routing\requestRouter.dll"; m = "$dl\arr.msi"
       u = "https://download.microsoft.com/download/E/9/8/E9849D6A-020E-47E4-9FD0-A023E99B54EB/requestRouter_amd64.msi" }
)
foreach ($p in $parts) {
    if (-not (Test-Path $p.f)) {
        Invoke-WebRequest $p.u -OutFile $p.m -UseBasicParsing
        $sig = Get-AuthenticodeSignature $p.m
        if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "(^|,\s*)O=Microsoft Corporation(,|$)") {
            Remove-Item $p.m -Force
            Stop-With "$($p.n): the download is not validly signed by Microsoft - not installed."
        }
        $x = Start-Process msiexec.exe -ArgumentList "/i `"$($p.m)`" /qn /norestart" -Wait -PassThru
        Remove-Item $p.m -Force
        if (($x.ExitCode -notin 0, 3010) -or -not (Test-Path $p.f)) { Stop-With "$($p.n) did not install (msiexec code $($x.ExitCode))." }
    }
    Ok "$($p.n)"
}

Step "3. Certificate for $HostName"
function Find-Cert {
    Get-ChildItem Cert:\LocalMachine\My | Where-Object {
        $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) -and
        (@($_.DnsNameList | ForEach-Object { $_.Unicode }) | Where-Object { $HostName -like $_ })
    } | Sort-Object NotAfter -Descending | Select-Object -First 1
}
$cert = Find-Cert
if (-not $cert) {
    New-Item -ItemType Directory $CertFolder -Force | Out-Null
    $pfx = @(Get-ChildItem (Join-Path $CertFolder "*.pfx"), "C:\*.pfx" -ErrorAction SilentlyContinue)
    if ($pfx.Count -eq 0) { Stop-With "No certificate yet. Put the company .pfx file in $CertFolder and run this again." }
    if ($pfx.Count -gt 1) { Stop-With "More than one .pfx: $($pfx.FullName -join ', '). Leave only the right one." }
    for ($i = 1; $i -le 3 -and -not $cert; $i++) {
        $pw = Read-Host "   Password for $($pfx[0].Name) (right-click pastes; nothing shows)" -AsSecureString
        try {
            Import-PfxCertificate -FilePath $pfx[0].FullName -CertStoreLocation Cert:\LocalMachine\My -Password $pw | Out-Null
            $cert = Find-Cert
            if (-not $cert) { Stop-With "The certificate was imported but does not cover $HostName (or has expired)." }
        } catch { Warn "wrong password or unreadable file ($i of 3)" }
    }
    if (-not $cert) { Stop-With "The certificate was not imported. Check the password with whoever sent the file." }
    Remove-Item $pfx[0].FullName -Force
    Ok "imported into Windows; the .pfx file is deleted (delete any note of its password too)"
}
$names = @($cert.DnsNameList | ForEach-Object { $_.Unicode }) -join ", "
Ok "$($cert.Thumbprint) - $names - expires $($cert.NotAfter.ToString('dd MMM yyyy'))"

Step "4. Firewall: 80 and 443 from company addresses"
$rule = "ATA Control Tower HTTPS"
Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort 80, 443 -Action Allow `
    -Profile Any -RemoteAddress LocalSubnet, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 | Out-Null
Ok "allowed from 10.x, 172.16-31.x, 192.168.x and this subnet; never the internet"

Step "5. This server reaches $HostName itself"
$hosts = "$env:windir\System32\drivers\etc\hosts"
$pattern = "^\s*[0-9.]+\s+" + [regex]::Escape($HostName) + "\s*$"
$written = $false
for ($i = 0; $i -lt 5 -and -not $written; $i++) {
    try {
        $lines = @([IO.File]::ReadAllLines($hosts) | Where-Object { $_ -notmatch $pattern }) + "127.0.0.1   $HostName"
        [IO.File]::WriteAllLines($hosts, $lines); $written = $true
    } catch { Start-Sleep 3 }
}
ipconfig /flushdns | Out-Null
if ($written) { Ok "hosts: 127.0.0.1 $HostName (this server only; colleagues need the DNS record)" }
else { Warn "the hosts file was busy; the check below may not reach $HostName until the DNS record exists" }

Step "6. IIS site"
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "setup_iis.ps1") -HostName $HostName -CertThumbprint $cert.Thumbprint
if ($LASTEXITCODE -ne 0) { Stop-With "setup_iis.ps1 stopped (see above)." }

Step "7. The tower reads its new address"
$task = Get-ScheduledTask -TaskName "Mantrac Tower" -ErrorAction SilentlyContinue
if ($task) {
    Warn "restarting the task 'Mantrac Tower' - a run in progress would stop; press Start again if so"
    Stop-ScheduledTask -TaskName "Mantrac Tower"; Start-Sleep 5; Start-ScheduledTask -TaskName "Mantrac Tower"
    for ($i = 0; $i -lt 12 -and -not (Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep 5 }
} else {
    Warn "no task 'Mantrac Tower' on this server: start the tower (START_TOWER.bat or INSTALL_SERVER.bat)"
}
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "check_iis.ps1") -HostName $HostName
Write-Host ""
Write-Host "Colleagues: ask the Domain Admin for the DNS record  $HostName -> this server's IP." -ForegroundColor Cyan
Write-Host "If links in the dashboard still show the old address, restart the server once." -ForegroundColor Cyan
