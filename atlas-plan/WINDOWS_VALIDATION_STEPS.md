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
| **B. Review branch with the fixes** | Into a **separate folder**, not over the stable copy: `git clone -b claude/atlas-verify-label https://github.com/mustafaessam4030-lang/TEST C:\ATA_demo_fix`, then copy `credentials.txt` into `C:\ATA_demo_fix\control-tower\` (never commit it) | Replay launchers work; card shows real read-back state. **Requires the branch to be pushed, which needs your approval. Today only `claude/atlas-demo-stabilize` (`c59e89d`, replay fix only) is on GitHub** |

The rest of these steps apply to whichever folder you chose. "the folder" below means
`C:\Automation` (A) or `C:\ATA_demo_fix\control-tower` (B).

## 2. Offline checks (10 min, no network to eHub or carriers)

```bat
cd /d <the folder>
run_tests.bat
```

**Pass:** the last line reads `N suites, N passed, 0 failed, …, 0 suite(s) with problems`.
- Expected on this PC for B: 72 suites, 0 failed. Skips allowed only for Tesseract / PostgreSQL / real eHub.
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
2. In `update_eta.py` set `DRY_RUN = True` and `MAX_RECORDS_PER_RUN = 1`. Or start a dry run from the web if you use the platform.
3. `START_TOWER.bat` → **Start run**.
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
