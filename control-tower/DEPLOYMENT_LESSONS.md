# Deployment lessons — what happened when the tower went live

What we learned putting the ATA Control Tower on a server, behind IIS, with
HTTPS and a company name, and then moving it to a new Azure server
(MAN-ATAAZ-01). ATLAS reads this guide, so each problem below is written the
way someone would ask about it. Server names and addresses are the ones used
at the time; check them before acting.

## Install the tower on a new server, in order

1. Copy the ZIP to the server (Remote Desktop: copy on your PC, paste on the
   server's Desktop). Unzip so the folder is exactly `C:\Automation\control-tower`
   — unzipping in Explorer adds an extra folder; move `control-tower` up one level.
2. Unblock the files once: `Get-ChildItem C:\Automation -Recurse | Unblock-File`
   (otherwise every script asks "Security warning … Run once?" — answer R).
3. Python 3.11 or newer, installed for all users with PATH
   (`/quiet InstallAllUsers=1 PrependPath=1`). Edge must be installed.
4. Packages: `python -m pip install -r requirements.txt psutil`, then the
   browser: `python -m playwright install chromium`.
5. ATLAS's AI: install Ollama, then `ollama pull qwen3.5:4b` (3.3 GB). On a
   strong server the tower also downloads qwen3:30b (19 GB) in the background.
6. Web search: `python -m intelligence.searxng_local install`.
7. The tower: `powershell -ExecutionPolicy Bypass -File .\install_server.ps1`
   (or INSTALL_SERVER.bat as administrator). It creates the scheduled task
   "Mantrac Tower" that starts the tower when the account signs in.
8. `C:\Automation\credentials.txt` — the Hub login, two lines
   `USERNAME=` and `PASSWORD=`. Never in the ZIP, email or chat.
9. Users: CREATE_ADMIN.bat or
   `python -m dashboard.accounts create-admin --email ... --name ...`.
   Then restart the tower.
10. Launch page date: SET_LAUNCH.bat as administrator (Cairo time).

## Problems we hit and the fix for each

### "python is not recognized" right after installing Python

The PowerShell
window was opened before the install and still has the old PATH. Close it and
open a new one, or reload it:
`$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")`.
Check the install itself with `Test-Path "C:\Program Files\Python312\python.exe"`.

### "You do not have adequate user rights" (Install-WindowsFeature)

The window
is not an Administrator window. Start → powershell → right-click → Run as
administrator; the title bar must say "Administrator: Windows PowerShell".

### PowerShell never comes back after installing Ollama

`Start-Process -Wait`
waits for the installer and everything it starts — including Ollama itself,
which keeps running. Ollama is installed; press Ctrl+C.

### Invoke-WebRequest downloads very slowly / prints "Writing request stream"

Windows PowerShell's progress display slows it down. Run
`$ProgressPreference = 'SilentlyContinue'` first.

**Pasting a whole window of output into PowerShell gives red parse errors
("Missing expression after unary operator '--'").** Only the command line must
be pasted, not the output. Press Ctrl+C to clear the `>>` prompt.

### Check says "PyMuPDF (PO PDFs) missing" although it installed

PyMuPDF 2.0
dropped the old `fitz` name. Fixed in the code (it imports `pymupdf` first);
on an older copy, `python -m pip install "pymupdf<2"`.

### "certificate verify failed" in the server check for the Hub or a carrier

That is Python's own test; the automation uses Edge. Open the Hub in Edge on
that server: if it opens without a warning, the automation will work.

### The run stopped with "browser session lost" / "Stopped on error"

We had
restarted the tower (Stop-ScheduledTask) or the server while a run waited for
a person. Nothing was written (Updated 0, Failed 0). Press Start again. Before
any restart, check no run is going. A run killed by a restart does not start
again by itself, on purpose.

### The dashboard page says "This dashboard needs an access key"

Personal
sign-in is not on yet: no account exists on this server. Run CREATE_ADMIN and
restart the tower.

### "An account for … already exists"

It came across with copied data. Use
`python -m dashboard.accounts list` to see everyone, and
`python -m dashboard.accounts reset --email ...` for a new set-password link.
CREATE_ADMIN asks the name first, then the email — swapped, it says
"Enter a valid work email address".

### A set-password link does not open ("can't reach this page")

The link
uses the tower's configured address. Change only the start of the link to an
address that works from that PC (the IIS name, the server name, or
`http://<server IP>:8787`) and keep everything from `/set-password` on,
including `#token=...`. A link works once, for 72 hours; Reset access makes a
new one and the old one stops working.

### Users seemed to disappear from Access levels

There were two copies of the
tower with two user lists. Find them with
`Get-ChildItem C:\ -Recurse -Filter accounts.db`. Keep one install.

### From the server it works, from a PC "can't reach this page"

- On the server, `Test-NetConnection <ip> -Port 8787` always succeeds once the
  tower runs — test from the PC instead.
- An Azure server's network is usually "Public"; the tower's firewall rule was
  for Domain/Private only. Fix:
  `Set-NetFirewallRule -DisplayName "Mantrac Control Tower" -Profile Any -RemoteAddress 10.0.0.0/8`.
- If the PC is still blocked, Azure's Network Security Group needs inbound
  TCP 80, 443 (and 8787) from the company network — ask the Azure admin.
- `ERR_NAME_NOT_RESOLVED`: the PC does not know the name. Use the IP, or ask
  for a DNS record; for one PC only, a hosts-file line.

## IIS, HTTPS and the company name

- IIS role: `Install-WindowsFeature Web-Server, Web-Mgmt-Console, Web-Scripting-Tools`
  (needs a restart), then URL Rewrite and ARR 3.0 from iis.net.
- Test without a certificate first:
  `setup_iis.ps1 -HostName <name> -HttpOnly`, restart the tower,
  `check_iis.ps1 -HostName <name> -HttpOnly`.
- Certificate from the company CA, run on the server:
  `Get-Certificate -Template WebServer -DnsName "ata.mantracgroup.com","<server name>" -CertStoreLocation Cert:\LocalMachine\My`.
  A bought or company certificate must be a .pfx with its password
  (Import-PfxCertificate); a .cer alone has no key.
- Switch to HTTPS: `setup_iis.ps1 -HostName ata.mantracgroup.com -CertThumbprint <thumbprint>`.
  Moving from a test name to the real one: remove the test site first
  (`Remove-Website "ATA Control Tower"`) or use the current setup_iis.ps1,
  which moves the bindings.
- To also answer the server's own name, add an https binding for it with the
  same certificate (the certificate must include that name).
- The WARN "dashboard is locked by the access key only" from an older
  check_iis.ps1 was a mistake in the script, fixed since.
- Restart the tower after setup_iis.ps1 so it reads ATA_PUBLIC_URL; after the
  switch to HTTPS everyone signs in again.

## DNS

The internal zone mantracgroup.com is held by the domain controller
man-dcaz-01.win2k.dom. Adding a record needs a Domain or DNS Admin — an
ordinary account gets "WIN32 5" (access denied):
`Add-DnsServerResourceRecordA -ComputerName man-dcaz-01.win2k.dom -ZoneName "mantracgroup.com" -Name "ata" -IPv4Address "<server IP>"`.
Never create a whole mantracgroup.com zone where none exists: it would hide
the company website and mail inside the network. When the server moves, the
record must point to the new IP.

## Moving to another server

- Stop and disable the task on the old server so two servers never run the
  automation: `Stop-ScheduledTask "Mantrac Tower"; Disable-ScheduledTask "Mantrac Tower"`.
- Copying `C:\Automation` keeps users, settings, logs, the song and carrier
  logins; a fresh ZIP does not (users must be created again).
- Machine settings do not travel: launch date (SET_LAUNCH.bat), IIS, the
  certificate, firewall rules, DNS.
- Updating the code later: stop the task, `Expand-Archive <zip> -DestinationPath C:\Automation -Force`,
  unblock, start the task. Users and settings live in dashboard\.runtime and
  are kept.
