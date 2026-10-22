<#
  ATA Control Tower: check the IIS front door. Changes nothing.

      powershell -ExecutionPolicy Bypass -File .\check_iis.ps1 -HostName ata.mantrac.com
      (before the certificate, after setup_iis.ps1 -HttpOnly: add -HttpOnly)

  Each line is PASS, WARN or FAIL. Run it on the server, then once from
  a colleague's PC on the company network.
#>
param(
    [Parameter(Mandatory = $true)] [string] $HostName,
    [int] $TowerPort = 8787,
    [switch] $HttpOnly
)
$scheme = if ($HttpOnly) { "http" } else { "https" }
$base = "${scheme}://$HostName"

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$results = @()
function Line($state, $text) {
    $color = @{ PASS = "Green"; WARN = "Yellow"; FAIL = "Red" }[$state]
    Write-Host ("  {0,-4}  {1}" -f $state, $text) -ForegroundColor $color
    $script:results += $state
}
function Get-Url($url, $redirect = $true) {
    try {
        $req = @{ Uri = $url; UseBasicParsing = $true; TimeoutSec = 15 }
        if (-not $redirect) {
            # PowerShell 5.1 reports a stopped redirect as an error; keep the response.
            $req.MaximumRedirection = 0
            return Invoke-WebRequest @req -ErrorAction SilentlyContinue
        }
        return Invoke-WebRequest @req -ErrorAction Stop
    } catch {
        if ($_.Exception.Response) { return $_.Exception.Response }
        return $null
    }
}

Write-Host ""
Write-Host "ATA Control Tower - IIS check for $base" -ForegroundColor Cyan

# 1. The tower itself, behind IIS (on the server only).
$local = Get-Url "http://127.0.0.1:$TowerPort/login"
if ($local) { Line PASS "the tower answers on 127.0.0.1:$TowerPort" }
else { Line WARN "the tower does not answer on 127.0.0.1:$TowerPort (fine on a colleague's PC; on the server: is it running?)" }

# 2. DNS.
try {
    $ip = (Resolve-DnsName $HostName -ErrorAction Stop | Where-Object { $_.IPAddress } | Select-Object -First 1).IPAddress
    if ($ip) { Line PASS "$HostName resolves to $ip" } else { Line FAIL "$HostName has no address record" }
} catch { Line FAIL "$HostName does not resolve - ask IT for the DNS record" }

# 3. https, certificate and the sign-in page.
$login = Get-Url "$base/login"
if ($login -and [int]$login.StatusCode -eq 200 -and "$($login.Content)" -match "Sign in") {
    Line PASS "$base/login shows the sign-in page$(if (-not $HttpOnly) { ' (certificate accepted)' })"
} elseif ($login) {
    Line FAIL "$base/login answered $([int]$login.StatusCode), not the sign-in page"
} else {
    Line FAIL "$base did not answer - binding, $(if ($HttpOnly) { 'firewall (80)' } else { 'certificate or firewall (443)' })"
}

# 3b. The launch page and its files come through the same front door.
$lp = Get-Url "$base/launch"
$la = Get-Url "$base/static/launch/launch.js"
if ($lp -and [int]$lp.StatusCode -eq 200 -and "$($lp.Content)" -match "ATLAS" -and $la -and [int]$la.StatusCode -eq 200) {
    Line PASS "$base/launch and its files load"
} else { Line FAIL "$base/launch did not load (is the tower up to date?)" }

# 4. http redirects to https.
$plain = Get-Url "http://$HostName/login" $false
if ($HttpOnly) { Line WARN "http test: no https yet - use test accounts; rerun setup_iis.ps1 with -CertThumbprint when the certificate is ready" }
elseif ($plain -and [int]$plain.StatusCode -in 301, 302, 307, 308) { Line PASS "http redirects to https" }
else { Line WARN "http://$HostName does not redirect to https" }

# 5. Personal sign-in is on: the dashboard asks to sign in.
$api = Get-Url "$base/api/state"
$body = ""
if ($api -is [System.Net.HttpWebResponse]) {
    try { $body = (New-Object IO.StreamReader($api.GetResponseStream())).ReadToEnd() } catch {}
}
if ($api -and [int]$api.StatusCode -eq 401 -and $body -match "not_signed_in|session_expired") { Line PASS "the dashboard requires a personal sign-in" }
elseif ($api -and [int]$api.StatusCode -eq 401) { Line WARN "the dashboard is locked by the access key only - turn on personal sign-in (CREATE_ADMIN.bat)" }
elseif ($api -and [int]$api.StatusCode -eq 200) { Line FAIL "the dashboard opened without sign-in - run CREATE_ADMIN.bat first" }
else { Line WARN "could not tell whether sign-in is required" }

# 6. The tower knows its public address.
$public = [Environment]::GetEnvironmentVariable("ATA_PUBLIC_URL", "Machine")
if ($public -eq $base) { Line PASS "ATA_PUBLIC_URL is $base (the tower reads it after sign-out/in or restart)" }
elseif ($local) { Line FAIL "ATA_PUBLIC_URL is '$public' - run setup_iis.ps1, then sign out and in" }

# 7. ARR settings the live page needs (server only).
$appcmd = Join-Path $env:windir "system32\inetsrv\appcmd.exe"
if (Test-Path $appcmd) {
    $proxy = & $appcmd list config -section:system.webServer/proxy 2>$null | Out-String
    if ($proxy -match 'preserveHostHeader="true"') { Line PASS "ARR keeps the Host header (buttons work)" }
    else { Line FAIL "ARR preserveHostHeader is off - button presses will be refused" }
    if ($proxy -match 'responseBufferLimit="0"') { Line PASS "ARR does not buffer (live updates work)" }
    else { Line FAIL "ARR responseBufferLimit is not 0 - the page will stop updating" }
}

Write-Host ""
$fails = @($results | Where-Object { $_ -eq "FAIL" }).Count
if ($fails -eq 0) {
    Write-Host "No FAIL. Last check by hand: sign in at $base, start a dry run," -ForegroundColor Green
    Write-Host "and watch the counters move; Settings -> Access audit log should show your PC's address." -ForegroundColor Green
} else {
    Write-Host "$fails FAIL line(s): fix them, then run this again." -ForegroundColor Red
}
exit $fails
