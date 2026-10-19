# Windows Validation Steps — before the 20 Oct demo

**Rule for the demo:** nothing may be presented as live execution or verified unless the matching
step below has **passed on the Windows PC** and its evidence (log, JSON, screenshot) has been
saved. If a step fails, the demo uses the labelled fallback (§6) and says so.

Run everything from `C:\Automation` in a normal Command Prompt, on the PC that will be used.
Write down each result in the table at the end.

## 0. Preserve what's there (5 min)

```bat
cd /d C:\Automation
git status
git log -1 --oneline
robocopy C:\Automation C:\Automation_backup_20261020 /E /XD carrier_profile
```

- Note the commit `git log` prints. If `C:\Automation` isn't a Git checkout, note the date of `update_eta.py`.
- **Keep the backup untouched.** It's the recovery copy.

## 1. Choose the code that runs the demo

| Choice | How | Consequences |
|---|---|---|
| **A. Stable copy as is (`ba390cb`)** | Nothing to change | `START_ATLAS_DEMO.bat` (no arguments), `run_dashboard.bat` and `share_dashboard.bat` **crash** if `logs\` has a run log. Use only `START_TOWER.bat` and `START_ATLAS_DEMO.bat --test-data`. The Overview card can say "written and verified" without a read-back: **don't present it** |
| **B. Review branch with the fixes** (pushed: `claude/atlas-verify-label` @ `2b0b646`) | Into a **separate folder**, never over the stable copy: `git clone --branch claude/atlas-verify-label --single-branch https://github.com/mustafaessam4030-lang/TEST.git C:\ATA_review`, then `cd /d C:\ATA_review` and `git log -1 --oneline` must print `2b0b646`. Without Git: download `https://github.com/mustafaessam4030-lang/TEST/archive/refs/heads/claude/atlas-verify-label.zip` and extract to `C:\ATA_review` | Replay launchers work; card shows real read-back state; failure wording names the Hub for Manage-page errors |

**Important for B:** the clone's code still uses `C:\Automation` for data. `update_eta.py` hard-codes
`BASE_FOLDER = C:\Automation`, so it **reads `C:\Automation\credentials.txt`** and **writes logs,
`tracking_results.csv` and screenshots into `C:\Automation`**. It falls back to another folder only
if that one isn't writable. Don't copy credentials into the clone. Step 0's backup protects the
stable copy's data. The clone keeps its own ATLAS learning store under `C:\ATA_review\control-tower\ml\data\`.

The rest of these steps apply to whichever folder you chose. "the folder" below means
`C:\Automation` (A) or `C:\ATA_review\control-tower` (B).

## 2. Offline checks (10 min, no network to eHub or carriers)

```bat
cd /d <the folder>
run_tests.bat
```

**Pass:** the last line reads `N suites, N passed, 0 failed, …, 0 suite(s) with problems`.
- Expected for B (as measured here on Linux at `2b0b646`: 72 suites, 4,626 passed, 0 failed, 6 skipped): 0 failed. Skips allowed only for Tesseract / PostgreSQL / real eHub / PowerShell. First time on a PC: `pip install -r requirements.txt` and `python -m playwright install chromium`.
- For A, `test_atlas_skills.py` reports 3 failures (the known test-isolation bug, not a product fault).

Save `test_results.json`.

## 3. Dashboard launch (5 min)

```bat
START_TOWER.bat
```

**Pass:** the console prints `Control Tower running at http://127.0.0.1:8787/…`, and the page
opens to the Overview (Skip intro works). Take a screenshot.

Close it (Ctrl+C or close the window) before step 4.

## 4. Read-only eHub check (5 min, reads only)

```bat
verify_ehub.bat
```

**Pass:** the `[VERIFY]` line says **REAL OBSERVED**. It signs in, the BU list renders, and one
Under Clearance shipment's status is read. Save the JSON it writes beside the run logs.

**If REAL VERIFICATION BLOCKED:** stop. No live claims in the demo. Go to §6.

## 5. One representative shipment, end to end

### 5a. Dry run (reads carrier, writes nothing). This is the default for the demo

1. Pick **one** shipment approved by operations, not customer-sensitive, carrier DHL or Qatar
   (not CMA CGM, which is held; not Grimaldi unless a person is ready for the security code).
2. Force a dry run and no PO activity, **without editing code**, in a Command Prompt opened **as Administrator** (Start → type cmd → right-click → Run as administrator). START_TOWER.bat re-launches itself through the Windows permission prompt when the console is not elevated, and that new window does NOT keep variables set before it, so the dry run would be silently lost:
   ```bat
   cd /d <the folder>
   set CT_DRY_RUN=1
   set PO_AUTO=0
   START_TOWER.bat
   ```
   - `CT_DRY_RUN=1` can only turn dry run on.
   - Do **not** set `MAX_HUB_WRITES=0`: the run treats a cap of 0 as already reached and starts no shipment at all (`carrier_pacing.RunLimits.exhausted`).
   - `PO_AUTO=0` stops the supervisor's automatic PO sweep (its own eHub browser and PO jobs) from starting beside the run.
   - To limit the run to one shipment, set `MAX_RECORDS_PER_RUN = 1` in `update_eta.py`. Do that only in the **clone (B)**, never in the stable copy.
3. Confirm the header shows **"Dry run — nothing is saved"** before you press **Start run**.
4. Watch on the dashboard: Started → Carrier selected → Tracking opened → Data extracted → ATA
   validated. In a dry run the Hub write shows as DRY RUN and **no read-back happens**.
5. **Pass:** the shipment ends with the carrier's ETA/ATA shown. `logs\run_*.log` has the carrier
   lines and the source label (`eta_source` / `ata_source`). The Overview card does **not** say
   verified (on B it says "Hub read-back not confirmed").
6. Ask ATLAS: "where is <ref>?" and "why did <ref> fail?" (if it failed). The answers must match the
   log. Screenshot both.
7. Set `DRY_RUN = False` again afterwards **only if** 5b is approved. Otherwise leave it `True` for the demo.

### 5b. Real write with read-back. **Only with written approval for that one shipment**

```bat
verify_eta.bat <REFERENCE>
```

**Pass:** `[VERIFY]` says **REAL VERIFIED**. The Hub reopened the shipment and read back the exact
date written. Save the JSON and the run log.

**Anything else** (MISMATCH, UNVERIFIED, BLOCKED): don't claim a verified write in the demo.

### 5c. Human-in-the-loop (only if it's part of the demo)

Use a Grimaldi shipment with a person present.
- **Pass:** the shipment parks in HUMAN ACTIONS after about 30 s, and **Open & Continue** brings the
  carrier tab forward.
- The person types the security code and presses Search. The run resumes by itself, reads the
  page twice, and finishes.
- Without a Grimaldi shipment available: **describe** the feature, don't demo it live.

## 6. The labelled fallback (always prepare it)

```bat
START_ATLAS_DEMO.bat --test-data
```

**Pass:** every ATLAS answer starts with **TEST DATA**, the header says "Dry run — nothing is
saved", and the run ID is `TEST-DATA-DEMO`. It starts no automation, signs into nothing and
sends nothing. Present it as **a labelled test run**, never as live data.

On the fix branch, "why did 8842001173 fail?" says "writing to the hub" and quotes the Manage-page
reason. In that test record the timeline still shows the error under "Tracking opened" (seeded
data has no carrier values), so don't open that timeline in the demo.

## 7. Optional: ATLAS AI (only if shown)

```bat
python -m intelligence.autoconfig --test
```

**Pass:** `RESULT: ATLAS AI is working on this PC` and an answer time you can live with. Without it:
present ATLAS as rule-based, and make **no claims about Arabic or natural-language answers**.

## Results to record

| Step | Result (PASS / FAIL / NOT RUN) | Evidence file | Time |
|---|---|---|---|
| 0 Backup | | | |
| 2 run_tests | | test_results.json | |
| 3 START_TOWER | | screenshot | |
| 4 verify_ehub | | verify JSON | |
| 5a dry run | | run log + screenshots | |
| 5b verify_eta (approved only) | | verify JSON | |
| 5c human step | | screenshot | |
| 6 fallback | | screenshot | |
| 7 ATLAS AI | | autoconfig output | |

**GO for live claims** only with 2, 3, 4 and 5a PASS. Verified-write claims also need 5b PASS.
Otherwise present §6, labelled.
