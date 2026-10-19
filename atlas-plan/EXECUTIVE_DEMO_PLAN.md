# ATLAS — Executive Demo Plan and Evidence (20 Oct 2026)

Prepared 19 Oct 2026 in a **Linux cloud container**. I had **no access to the Windows demo PC,
eHub, carrier websites, Ollama or SearXNG.** Everything marked VERIFIED was run here. Everything
that needs the Windows PC is marked **NOT VERIFIED — Windows needed** and must be checked on the
PC before the demo.

## 1. Baseline (Phase 1)

| Item | Value |
|---|---|
| Stable demo copy | `claude/atlas-potato-garden` @ `ba390cbc74` (PR #2, open, not merged). **Unchanged.** Preserved as a detached worktree for checks |
| Release zip you sent | Identical to `ba390cbc74` (only `RELEASE_MANIFEST.txt` extra) |
| Windows working tree | **Not inspected — not reachable from here.** Before the demo, run `git status` and `git log -1` on the PC and keep a copy of `C:\Automation` (see §5) |
| Fix branch | `claude/atlas-demo-stabilize` (local only, **not pushed**) |
| Baseline tests @ `ba390cb` | 71 suites: 4,594 passed, 8 failed, 7 skipped. All 8 failures explained (3 test-isolation bug, 5 sandbox permissions) |

## 2. Stabilization (Phase 2)

See `STABILIZATION_REPORT.md` for exact commands and counts.

## 3. Critical finding for the demo

**On the stable copy `ba390cb`, `START_ATLAS_DEMO.bat` crashes on start** whenever
`C:\Automation\logs\` has a run log. The default mode is `--replay`, which calls `server.replay`,
which hits `NameError: LOG_LINE`. The dashboard never opens. `run_dashboard.bat` and
`share_dashboard.bat` crash the same way. Screenshot `01_stable_replay_crash.png`.

- **On the fix branch the same launch works:** the dashboard loads and the run is replayed.
  Screenshots `02`, `03`.
- **`START_ATLAS_DEMO.bat --test-data` works on the unchanged stable copy** (it doesn't use replay).
  Screenshots `04`, `05a–d`.
- `START_TOWER.bat` and `update_eta.py` don't call replay, so they're **not** affected by this bug.

## 4. Second finding: the Overview can overstate verification

`dashboard/static/index.html:5020-5023`: when a run has **finished** with ≥1 success and 0 failures,
the ATLAS card says **"Run complete. N Hub record(s) written and verified."** It checks only the
counters, not each shipment's read-back. After a replay (the CSV carries no read-back) or a run
where a read-back couldn't be done, that claim is unsupported. Screenshot `02` shows it next to a
grey (not done) "Read-back verification" step.

**Not fixed** (outside the approved scope). For the demo: **don't point at that sentence as proof
of verification.** Use a shipment's own "Read-back verification" step, or the run log's
`verify_saved_date` lines, instead. A fix needs your approval.

## 5. Launch and recovery steps (Windows, to be confirmed on the PC)

Before anything:
1. `cd C:\Automation` → `git status` / `git log -1` (if it's a checkout), and note the commit.
2. **Back up**: copy `C:\Automation` to `C:\Automation_demo_backup_20261020` (keeps `logs\`,
   `tracking_results.csv`, `credentials.txt`, `ml\data\`).
3. `run_tests.bat` → expect the result in §2 for the copy you're using.

Option A — **live dashboard with the real automation** (needs eHub access and an approved safe shipment):
1. `START_TOWER.bat` → dashboard at `http://127.0.0.1:8787/`.
2. Set `DRY_RUN = True` in `update_eta.py` (or start a dry run from the web) so **nothing is
   written** unless writing was explicitly approved.
3. Start run → watch one shipment through carrier → validation → (dry run: no save).
4. Recovery: Stop in the header → close the console → `START_TOWER.bat` again.

Option B — **replay of the last real run** (needs the fix branch, or it crashes):
1. `START_ATLAS_DEMO.bat` → dashboard with the last real run from `C:\Automation`.

Option C — **truthful fallback, works on the stable copy as is**:
1. `START_ATLAS_DEMO.bat --test-data` → every ATLAS answer is prefixed **TEST DATA**, the header
   says "Dry run — nothing is saved", run ID `TEST-DATA-DEMO`. Nothing is started, signed into or
   sent.

If a launch fails: close the console, run `check_dashboard.bat`, and switch to Option C.

## 6. 5–7 minute executive sequence (suggested)

| Min | Show | Say (only what's verified) |
|---|---|---|
| 0:00 | Intro film (or Skip) → Overview | "This is the ATA Control Tower: it reads the Hub, asks the carrier, writes ETA/ATA back." |
| 1:00 | Overview counters + Tracking card | "Every number comes from the run's records. Unknown values show as a dash." |
| 2:00 | Shipments page, filter Failed | "Failures are kept with their reason; the run never stops for one." |
| 3:00 | A shipment's timeline: Hub write → **Read-back verification** | "A date counts only when the Hub reads it back." **Only if the record shows the read-back step done.** |
| 4:00 | Ask ATLAS: "where is <ref>?", "why did <ref> fail?" | "ATLAS answers from the run's records." |
| 5:00 | Ask ATLAS: "what is the destination of <ref>?", "where is 9999999999?" | "It says what it doesn't know rather than guessing." (VERIFIED, `05c`, `05d`) |
| 6:00 | Human Action page (empty or parked task) | "When a carrier needs a person, the shipment is parked and the run carries on." **Live only if one happens; otherwise describe.** |

Avoid: the Overview "written and verified" sentence (§4); claims about Arabic or natural-language
answers unless Ollama is shown running on the PC; claims about web search unless SearXNG is running.

## 7. Verified claims vs limitations

| Claim | Status |
|---|---|
| Dashboard loads (replay, fix branch) | VERIFIED here (`02`, `03`) |
| Dashboard loads (test-data, stable) | VERIFIED here (`04`) |
| ATLAS answers from records, labels TEST DATA, refuses unknown shipment and destination | VERIFIED here, rules only (`05a–d`) |
| ATLAS failure wording | **Limitation**: says "carrier site" for a Hub Manage-page error (`05b`) |
| Overview "written and verified" | **Limitation**: not backed by read-back (§4) |
| Real eHub sign-in, carrier lookup, Hub write, read-back | **NOT VERIFIED — Windows needed** (`verify_ehub.bat`, `verify_eta.bat <ref>`, the latter writes) |
| Human-in-the-loop pause/resume live | **NOT VERIFIED — Windows needed** (tested offline only) |
| Local model (Ollama) answers / Arabic | **NOT VERIFIED** (stand-ins only; docs claim 24–91 s per answer on CPU) |
| Web search | **NOT VERIFIED** (SearXNG not installed here) |

## 8. GO / NO-GO

**Recommendation: CONDITIONAL GO with Option C (labelled test data) as the guaranteed path.
NO-GO for any live-execution claim** until these are done on the Windows PC and recorded:

1. Pick the copy: stable `ba390cb` (then **don't use** `START_ATLAS_DEMO.bat` without
   `--test-data`, or `run_dashboard.bat`), or the fix branch after you approve pushing it.
2. `START_TOWER.bat` opens the dashboard on the PC.
3. `verify_ehub.bat` → REAL OBSERVED (read-only).
4. One dry-run shipment end-to-end, watched on the dashboard, with its run log saved.
5. If a real write is wanted in the demo: written approval, one safe shipment, `verify_eta.bat <ref>`
   → REAL VERIFIED with read-back.

If 2–4 fail on the day: present Option C and say plainly it's a labelled test run.
