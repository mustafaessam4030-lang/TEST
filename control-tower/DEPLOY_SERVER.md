# Deploying this build to the Windows server

This build is the release from branch `claude/atlas-server-deploy` (see
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

## Rollback (2 min)

End the task, rename `C:\MantracTower` → `C:\MantracTower_failed`, rename
`C:\MantracTower_old` → `C:\MantracTower`, sign out and in. Data in
`C:\Automation` is shared by both versions; restore the step-0 backup only if
something there must be undone.
