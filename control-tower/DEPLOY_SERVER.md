# Deploying this build to the Windows server

This build is the release from branch `claude/atlas-potato-garden` (see
`RELEASE_MANIFEST.txt` for the exact commit). It runs everything on one Windows
server: the automation (Edge, carriers, Hub), the PO engine, ATLAS and the
dashboard at `http://<server>:8787`. `SERVER_SETUP.md` is the full reference.
This page is the order to do it in, safely.

**Nothing in this build has been run on Windows by its author.** It was tested
on Linux with the full offline suite. Steps 4–7 below are what prove it on
the server. Don't announce the deployment as live until they pass.

## 0. Before you touch anything (5 min)

1. If an older tower runs as the scheduled task: Task Scheduler → **Mantrac
   Tower** → **End**, and **Disable** it until step 6.
2. Back up the data. It's outside the code folder and every version uses it:
   ```bat
   robocopy C:\Automation C:\Automation_backup_%DATE:~-4%%DATE:~3,2%%DATE:~0,2% /E /XD carrier_profile
   ```
   Keep the old code folder too (rename it, e.g. `C:\MantracTower_old`). That's the rollback.

## 1. Install the code (10 min)

1. Extract the zip with **Extract All** to `C:\MantracTower`. The code is in
   `C:\MantracTower\control-tower`.
2. `C:\Automation\credentials.txt` must exist (`USERNAME=` / `PASSWORD=` lines).
   The code always reads it from `C:\Automation`, and writes logs,
   `tracking_results.csv` and screenshots there. Never copy credentials into the code folder.
3. Signed in as the Windows account the automation will use, double-click
   `C:\MantracTower\control-tower\INSTALL_SERVER.bat` and accept the
   administrator prompt. It checks Python 3.11+ and Edge, installs the packages,
   opens port 8787 to domain and private networks, stops sleep, registers the
   scheduled task **Mantrac Tower** for this folder, and runs `check_server.py`.

## 2. Offline checks (10–25 min, nothing reaches eHub or carriers)

```bat
cd /d C:\MantracTower\control-tower
py -m playwright install chromium
run_tests.bat
```
**Pass:** the last line shows `0 failed … 0 suite(s) with problems`. The
browser download is for the tests only; the automation itself uses Edge.

## 3. Health check

`check_server.bat` → no **FAIL** lines. WARN lines are listed with what to
check. Run it again after step 6, once the task is running.

## 4. Read-only eHub check

`verify_ehub.bat` → the `[VERIFY]` line must say **REAL OBSERVED**. If it says
REAL VERIFICATION BLOCKED, stop and fix what it names before anything else.

## 5. One dry run from the dashboard (nothing saved to the Hub)

1. Start the tower: re-enable the scheduled task and sign out and in again,
   or run `START_TOWER.bat`.
2. Open `http://<server>:8787` (or the link printed in the tower window).
3. Press **Start dry run**, not Start run. The header shows **"Dry run —
   nothing is saved"**. The dry run reads every Under Clearance shipment's
   carrier and saves nothing; the PO automatic run does not start beside it.
4. **Pass:**
   - shipments move through Carrier selected → Data extracted;
   - `C:\Automation\logs\run_*.log` shows the carrier results;
   - the Overview card does **not** say "verified";
   - ATLAS answers "where is <ref>?" from that run.
5. Stop it any time with **Stop after this shipment**.

## 6. Go live (only with the operations owner's go-ahead)

- **Start run** is the live run: it saves ETA/ATA to eHub and reads each one
  back (`VERIFY_AFTER_SAVE`, on by default). A shipment counts as written and
  verified only when the read-back matched.
- Each live Start also starts the PO automatic run beside it (set the
  `PO_AUTO=0` user environment variable and restart the task to turn that off).
- Optional single-shipment proof before the first full run, with written approval:
  `verify_eta.bat <REFERENCE>` → **REAL VERIFIED**.

## 7. Personal sign-in (before sharing the link)

Double-click `CREATE_ADMIN.bat`, open the link it prints and set your
password. Add the team in **Settings → Access levels**:

| Level | Can |
|---|---|
| Viewer | see, ask ATLAS, export |
| Operator | also start/stop runs and do Human Actions |
| Admin | also manage users and read the audit log |

Restart the task. From then on the access key no longer opens the dashboard.

## Every day

- Leaving Remote Desktop: use `disconnect_keep_running.bat`, never just close the window.
- A carrier needing a person (Grimaldi's code, a "verify you are human"
  check): the shipment parks in **Human Action**, and **Open & Continue** lets you do it from your browser.
- CMA CGM is on hold in this build (carrier restricts access).

## 8. Publish at a company domain (e.g. https://ata.mantrac.com)

The dashboard stays on this server. IIS on the same server answers
`https://ata.mantrac.com` and forwards to the tower at `http://127.0.0.1:8787`.
Nothing is uploaded anywhere. **The IIS settings below are what the code needs;
they were written from the code and not tried on an IIS server. IT should check them.**

**Order matters:** do step 7 (personal sign-in) first. The domain is for
signed-in people only.

**Ready-made package:** `deploy/iis/` does all of the below. IT reads
`deploy/iis/README_FOR_IT.md`, then runs `setup_iis.ps1` (sets everything in
"What IT does" steps 3–5 and "What you set on the server") and `check_iis.ps1`
(PASS/WARN/FAIL for each point in "Check it"). The manual steps stay here as
the reference.

### What IT does (once)

1. **DNS:** an internal A record `ata.mantrac.com` → this server's internal IP.
   Company network / VPN only; don't publish it to the internet.
2. **Certificate:** a certificate for `ata.mantrac.com` (company CA or public),
   installed on this server.
3. **IIS** with the **URL Rewrite** and **Application Request Routing (ARR)**
   modules. A site bound to **https / 443** with that certificate (and http/80
   only to redirect to https).
4. **ARR proxy settings.** All four matter:
   ```bat
   %windir%\system32\inetsrv\appcmd.exe set config -section:system.webServer/proxy /enabled:"True" /preserveHostHeader:"True" /reverseRewriteHostInResponseHeaders:"False" /timeout:"00:30:00" /responseBufferLimit:"0" /commit:apphost
   ```
   - `preserveHostHeader` **True**: the tower checks the browser's Origin
     against the Host it receives. Without the original Host, every button
     press is refused ("This request did not come from the Control Tower page").
   - `responseBufferLimit` **0**: the live updates are a server-sent event
     stream (`/api/stream`). Buffered, the page stops updating.
   - `timeout` **30 min**: the stream is long-lived; the page reconnects by
     itself if it's cut.
   - ARR adds `X-Forwarded-For` by default. Keep it: the tower uses it for
     sign-in rate limits and the audit trail.
5. **web.config** in the site's folder:
   ```xml
   <configuration>
     <system.webServer>
       <rewrite>
         <rules>
           <rule name="ATA Control Tower" stopProcessing="true">
             <match url="(.*)" />
             <action type="Rewrite" url="http://127.0.0.1:8787/{R:1}" />
           </rule>
         </rules>
       </rewrite>
     </system.webServer>
   </configuration>
   ```

### What you set on the server (once)

In an Administrator Command Prompt, then sign out and in (the scheduled task
restarts the tower with them):
```bat
setx ATA_PUBLIC_URL https://ata.mantrac.com /M
setx ATA_TRUST_PROXY 1 /M
```
- `ATA_PUBLIC_URL` makes the sign-in cookie **Secure** and makes the links
  the tower prints and emails (set-password links) use the domain.
- `ATA_TRUST_PROXY` makes the tower take each person's address from the proxy's
  `X-Forwarded-For`. It's believed only from this machine. Without it, all
  sign-ins share the proxy's one address and its rate limit (20 attempts in 10 minutes).
- With either set, the tower always requires its access key or a sign-in,
  even on 127.0.0.1, so the domain can never serve the dashboard to someone
  who hasn't signed in.

### Check it

1. `https://ata.mantrac.com` → the sign-in page, with a valid certificate (padlock).
2. Sign in → the Overview, and its counters update live during a dry run.
3. Press **Start dry run** → accepted. A refusal mentioning "did not come from
   the Control Tower page" means preserveHostHeader is off.
4. **Settings → Access** audit log → your sign-in shows **your PC's** address,
   not 127.0.0.1. If it shows 127.0.0.1, `ATA_TRUST_PROXY` isn't set.

Once the domain works, colleagues should use only the domain. To stop direct
`http://<server>:8787` access, remove the Windows Firewall rule "Mantrac
Control Tower". Note that `START_TOWER.bat` and `INSTALL_SERVER.bat` add it
again when they run.

## 9. Launch page (optional): countdown before ATLAS opens

`/launch` is a public page: ATLAS greeting visitors ("We'll work together
soon", "We have a lot to do together"), a countdown to a fixed moment and,
if the company has a licensed soundtrack, music. It shows no
data and opens nothing. Sign-in, the three access levels and every protected
page and API are exactly as before, **before and after** the launch moment.
The only change while a launch moment is still ahead: a signed-out visitor to
`https://ata.mantrac.com/` is sent to `/launch` instead of `/login` (with
personal sign-in on; in access-key mode nothing changes). The
sign-in page itself always works (there is a small "Administrator sign-in"
link on the launch page). When the countdown reaches zero the page shows
**Continue to sign in**, which goes to the real sign-in page.

**Easiest:** right-click `SET_LAUNCH.bat` → Run as administrator, type the
date and time in Cairo time (e.g. `2026-11-01 09:00`), then sign out and in.
A ZIP built with `--soundtrack` already carries the song, which plays with no
setting. Or by hand, in an Administrator Command Prompt, then sign out and in (the
scheduled task restarts the tower with the new values):
```bat
setx ATA_LAUNCH_AT 2026-11-01T09:00:00+02:00 /M
setx ATA_LAUNCH_TZ Africa/Cairo /M
```
- `ATA_LAUNCH_AT`: the moment, **with its UTC offset**. Without one it is
  refused (the page then shows no countdown, only the sign-in action). Change
  it the same way; to stop using the launch page: `reg delete
  "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v ATA_LAUNCH_AT /f`.
- `ATA_LAUNCH_TZ`: the IANA zone the page names (default UTC). If Python on
  the server knows the zone, the offset must match it at that moment
  (summer time included), or the date is refused rather than guessed.
- Soundtrack (optional): put a file the company is **licensed** to host in
  `dashboard\static\launch\audio\` and `setx ATA_LAUNCH_AUDIO lilith.mp3 /M`
  (or an `https://` address of a licensed stream). See the README.txt in that
  folder. Nothing about the music shows on screen: the
  music starts by itself where the browser allows it, otherwise on the
  first click or key press. For no click at all, add the site to the Edge /
  Chrome Group Policy **AutoplayAllowlist** (e.g. `[*.]mantrac.com`). With
  no file, or a file that will not play, the page is simply silent.

Check: `https://ata.mantrac.com/launch` shows the countdown in the right zone;
`check_iis.ps1` includes it. IIS needs no change: it forwards every path.

## Rollback (2 min)

End the task, rename `C:\MantracTower` → `C:\MantracTower_failed`, rename
`C:\MantracTower_old` → `C:\MantracTower`, sign out and in. Data in
`C:\Automation` is shared by both versions; restore the step-0 backup only if
something there must be undone.
