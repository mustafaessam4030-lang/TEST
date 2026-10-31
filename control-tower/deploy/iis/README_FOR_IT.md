# ATA Control Tower on IIS — one page for IT

**Goal:** colleagues open `https://ata.mantracgroup.com` (your choice of name)
instead of `http://<server>:8787`. IIS on the tower server handles HTTPS and
forwards everything to the tower on `http://127.0.0.1:8787`. The tower stays
where it is; nothing is uploaded anywhere; the name is for the company network
or VPN only.

## What IT provides

1. **DNS:** an internal A record, e.g. `ata.mantracgroup.com` → the tower server's IP.
2. **Certificate** for that name (company CA or public), installed in the
   server's **Local Computer → Personal** store. Note its thumbprint.
3. **Firewall:** inbound TCP 443 (and 80, which only redirects) from the
   company network.
4. **IIS add-ons:** [URL Rewrite](https://www.iis.net/downloads/microsoft/url-rewrite)
   and [Application Request Routing](https://www.iis.net/downloads/microsoft/application-request-routing).

## Then, on the server (Administrator PowerShell)

```powershell
cd C:\MantracTower\control-tower\deploy\iis
powershell -ExecutionPolicy Bypass -File .\setup_iis.ps1 -HostName ata.mantracgroup.com -CertThumbprint <thumbprint>
```

**Testing before the certificate?** Use plain http first (company network,
test accounts only — passwords are not encrypted on the wire):

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_iis.ps1 -HostName ata.mantracgroup.com -HttpOnly
# sign out and in, then
powershell -ExecutionPolicy Bypass -File .\check_iis.ps1 -HostName ata.mantracgroup.com -HttpOnly
```

Open `http://ata.mantracgroup.com`. When the certificate is ready, run the first
command above (with `-CertThumbprint`, without `-HttpOnly`); it switches the
same site to https in place. Then sign out and in, and check without `-HttpOnly`.

Sign out of Windows and in again, then:

```powershell
powershell -ExecutionPolicy Bypass -File .\check_iis.ps1 -HostName ata.mantracgroup.com
```

Every line should be PASS. Then sign in at `https://ata.mantracgroup.com`, start a
dry run, and watch the counters move.

## The settings that matter (setup_iis.ps1 sets them)

| Setting | Why |
|---|---|
| ARR `preserveHostHeader = True` | The tower checks each button press came from its own page by its address. Without the original Host, every press is refused. |
| ARR `responseBufferLimit = 0`, dynamic compression off | The live dashboard is a server-sent event stream; buffering or compressing it stops the updates. |
| ARR `timeout = 30 min` | The live stream stays open; the page reconnects by itself if cut. |
| `X-Forwarded-For` (ARR default) | The tower records each person's address for sign-in limits and the audit log. |
| `ATA_PUBLIC_URL=https://…`, `ATA_TRUST_PROXY=1` | The sign-in cookie becomes Secure; links in invitations use the domain; the client address is read from IIS. |

**Server-wide:** the ARR proxy settings apply to the whole IIS server. If
another site here already uses ARR, check it still works afterwards.

**Windows Server, not Windows 10/11:** client Windows IIS serves about 10
requests at once and each open dashboard tab holds one live connection.

**Order:** turn on personal sign-in first (`CREATE_ADMIN.bat`). With these
settings the tower always requires a sign-in, so the domain never shows the
dashboard to anyone who hasn't signed in.

**Launch page (optional):** `/launch` (countdown, greeting, licensed
soundtrack) comes through the same rule; nothing to add in IIS. Its settings
are in DEPLOY_SERVER.md section 9.

**Rollback:** stop or delete the IIS site "ATA Control Tower"; remove the two
machine environment variables; sign out and in. People use
`http://<server>:8787` again.

**One click instead:** put the certificate (.pfx) in `C:\Automation\certificate`
and double-click `SETUP_HTTPS.bat` in the control-tower folder. It installs IIS,
URL Rewrite and ARR (Microsoft-signed downloads only), imports the certificate
(its password is typed there and never saved; the .pfx is deleted once
imported), opens 80/443 to company addresses, runs `setup_iis.ps1`, restarts
the tower's task and runs `check_iis.ps1`.

First run on Windows Server 2022 (Azure, MAN-ATAAZ-01) in October 2026 with
the company wildcard certificate: every check passed. Please still read the
scripts before running them on another server.
