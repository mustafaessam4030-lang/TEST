# ATA Control Tower — why does the gateway's health probe time out?
# Run in PowerShell AS ADMINISTRATOR on the tower server. Read-only, except
# that it switches on Windows Firewall's "log dropped packets" for 2 minutes
# and puts the setting back. Writes a report to the Desktop.

$Port = 8787
$WatchSeconds = 120
$out = New-Object System.Collections.ArrayList
function Say($text) { [void]$out.Add($text); Write-Host $text }
function Head($text) { Say ""; Say ("== " + $text) }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Please run PowerShell as Administrator (right-click > Run as administrator)." -ForegroundColor Red
    return
}

$myIps = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne "127.0.0.1" } | ForEach-Object { $_.IPAddress })
$verdict = New-Object System.Collections.ArrayList

Head "1. Server addresses"
Say ("This server: " + ($myIps -join ", "))

Head "2. Is exactly one tower listening on port $Port?"
$listen = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
foreach ($l in $listen) {
    $p = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $l.OwningProcess)
    Say ("  {0}:{1}  PID {2}  started {3}  {4}" -f $l.LocalAddress, $l.LocalPort, $l.OwningProcess, $p.CreationDate, $p.CommandLine)
}
if ($listen.Count -eq 0) { [void]$verdict.Add("The tower is NOT running: nothing listens on port $Port. Run: schtasks /run /tn ""Mantrac Tower""") }
elseif (@($listen | Select-Object -ExpandProperty OwningProcess -Unique).Count -gt 1) { [void]$verdict.Add("MORE THAN ONE tower is running on port $Port. Stop all (taskkill /PID <pid> /F) and start one.") }
elseif (-not ($listen | Where-Object { $_.LocalAddress -eq "0.0.0.0" })) { [void]$verdict.Add("The tower listens only on " + $listen[0].LocalAddress + ", not on the network. Start it with START_TOWER.bat or the scheduled task (--share).") }

Head "3. Does the tower answer, and how fast?"
foreach ($ip in @("127.0.0.1") + $myIps) {
    try {
        $t = Measure-Command { $r = Invoke-WebRequest ("http://{0}:{1}/login" -f $ip, $Port) -UseBasicParsing -TimeoutSec 20 -MaximumRedirection 0 -ErrorAction Stop }
        Say ("  http://{0}:{1}/login -> HTTP {2} in {3:N2} s" -f $ip, $Port, $r.StatusCode, $t.TotalSeconds)
        if ($t.TotalSeconds -gt 5) { [void]$verdict.Add("The tower is SLOW on $ip ($([math]::Round($t.TotalSeconds,1)) s). Send this report.") }
    } catch {
        Say ("  http://{0}:{1}/login -> FAILED: {2}" -f $ip, $Port, $_.Exception.Message)
        [void]$verdict.Add("The tower does not answer on $ip. Send this report.")
    }
}

Head "4. Windows Firewall rules for port $Port"
$rules = Get-NetFirewallRule -Direction Inbound -Enabled True -ErrorAction SilentlyContinue | Where-Object {
    ($_ | Get-NetFirewallPortFilter).LocalPort -contains "$Port" }
foreach ($r in $rules) {
    $a = $r | Get-NetFirewallAddressFilter
    Say ("  '{0}'  action={1}  profiles={2}  remote={3}" -f $r.DisplayName, $r.Action, $r.Profile, ($a.RemoteAddress -join ","))
}
if (-not $rules) { [void]$verdict.Add("No inbound firewall rule opens port $Port. Run INSTALL_SERVER.bat again.") }
Get-NetConnectionProfile | ForEach-Object { Say ("  Network '{0}' is {1}" -f $_.Name, $_.NetworkCategory) }

Head "5. Anything on ports 80 / 443 (if the gateway probes those instead)?"
foreach ($pp in 80, 443) {
    $x = @(Get-NetTCPConnection -State Listen -LocalPort $pp -ErrorAction SilentlyContinue)
    Say ("  port {0}: {1}" -f $pp, $(if ($x.Count) { "in use by PID " + ($x[0].OwningProcess) } else { "nothing listening" }))
}

Head "6. Settings"
foreach ($n in "ATA_PUBLIC_URL", "ATA_TRUST_PROXY") {
    Say ("  {0} = {1}" -f $n, [Environment]::GetEnvironmentVariable($n, "Machine"))
}

Head "7. Watching $WatchSeconds s for the gateway's health checks (they come every ~30 s)..."
$fw = Get-NetFirewallProfile
$logPath = "$env:SystemRoot\System32\LogFiles\Firewall\ata_probe_check.log"
Set-NetFirewallProfile -All -LogBlocked True -LogFileName $logPath -LogMaxSizeKilobytes 4096
$seen = @{}
$start = Get-Date
while (((Get-Date) - $start).TotalSeconds -lt $WatchSeconds) {
    Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Where-Object {
        $_.RemoteAddress -notin @("0.0.0.0", "::", "127.0.0.1", "::1") -and $_.RemoteAddress -notin $myIps } | ForEach-Object {
        $key = $_.RemoteAddress
        if (-not $seen.ContainsKey($key)) { $seen[$key] = $_.State }
    }
    Start-Sleep -Milliseconds 500
}
foreach ($p in $fw) { Set-NetFirewallProfile -Name $p.Name -LogBlocked $p.LogBlocked -LogFileName $p.LogFileName }
if ($seen.Count) {
    foreach ($k in $seen.Keys) { Say ("  Connection to port $Port from {0} (state {1})" -f $k, $seen[$k]) }
} else { Say "  No connection to port $Port came from another machine." }
$drops = @()
if (Test-Path $logPath) {
    $drops = @(Get-Content $logPath | Where-Object { $_ -match "^\d{4}-\d\d-\d\d .* DROP TCP " } | ForEach-Object {
        $f = $_ -split " "
        [pscustomobject]@{ When = $f[0] + " " + $f[1]; From = $f[4]; ToPort = $f[7] } } |
        Where-Object { $_.ToPort -in @("$Port", "80", "443") })
}
if ($drops.Count) {
    Say "  Windows Firewall BLOCKED:"
    $drops | Group-Object From, ToPort | ForEach-Object { Say ("    from {0} to port {1}  ({2} times)" -f $_.Group[0].From, $_.Group[0].ToPort, $_.Count) }
} else { Say "  Windows Firewall blocked nothing on ports $Port / 80 / 443." }

Head "RESULT"
if (-not $verdict.Count) {
    $blocked = @($drops | Where-Object { $_.ToPort -eq "$Port" })
    $wrongPort = @($drops | Where-Object { $_.ToPort -in @("80", "443") })
    if ($blocked.Count) {
        $src = ($blocked | Select-Object -ExpandProperty From -Unique) -join ","
        [void]$verdict.Add("Windows Firewall is blocking the gateway ($src). Fix: netsh advfirewall firewall add rule name=""ATA App Gateway"" dir=in action=allow protocol=TCP localport=$Port remoteip=$src")
    } elseif ($wrongPort.Count) {
        [void]$verdict.Add("The gateway is probing port " + (($wrongPort | Select-Object -ExpandProperty ToPort -Unique) -join "/") + ", not $Port. IT: set Backend settings port = $Port, protocol = HTTP.")
    } elseif ($seen.Count) {
        [void]$verdict.Add("The gateway DOES reach the tower (" + (($seen.Keys) -join ",") + "). If it still times out, its Backend settings protocol is probably HTTPS: IT must set it to HTTP, port $Port, probe path /login.")
    } else {
        [void]$verdict.Add("Nothing from the gateway reached this server in $WatchSeconds s. The block is in Azure (NSG, route or backend pool target). IT: allow TCP $Port from the gateway subnet to " + ($myIps -join ",") + ", check the backend pool target, and run Network Watcher > Connection troubleshoot.")
    }
}
foreach ($v in $verdict) { Say ("  * " + $v) }

$report = Join-Path ([Environment]::GetFolderPath("Desktop")) ("ata_gateway_check_{0:yyyyMMdd_HHmm}.txt" -f (Get-Date))
$out | Set-Content -Path $report -Encoding UTF8
Write-Host ""
Write-Host "Report saved: $report  (send it to IT / ATLAS support)" -ForegroundColor Green
