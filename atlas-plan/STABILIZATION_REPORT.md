# Stabilization Report — fixes #1–#3

| | |
|---|---|
| Base (stable demo copy, unchanged) | `claude/atlas-potato-garden` @ `ba390cbc74` |
| Fix branch | `claude/atlas-demo-stabilize` @ **`c59e89d133e6478e7bac280d455f0795ed6ba4c5`**, **local only, not pushed** |
| Environment | Linux container, Python 3.13.16, Playwright 1.64 with the preinstalled Chromium, worktree at `/home/user/ata-wt` (readable by `nobody`) |
| Push | **Not pushed.** The demo plan says no push without explicit approval |

## Files changed (4 files, +54 / −1)

| File | Change |
|---|---|
| `control-tower/dashboard/server.py` | Restored `LOG_LINE = re.compile(r"^\[(?P<ts>[\d\-: ]+)\]\s(?P<msg>.*)$")` and its section header, byte-for-byte as before `681b273` deleted them. The pattern still matches `update_eta.write_log`'s `[%Y-%m-%d %H:%M:%S] message` format |
| `control-tower/test_ui.py` | New REPLAY section, 4 checks: replays a finished run with a run log and a results row **in a child process** (leaves the suite's module-level bridge untouched) |
| `control-tower/test_atlas_skills.py` | `os.environ.pop("ATLAS_SEARCH", None)` next to the existing `ATLAS_SEARCH_URL` pop. No assertion changed, all 80 checks still run |
| `control-tower/test_flight_status.py` | `\d` → `\\d` (3×) in the JavaScript regex inside the stand-in page string |

## Evidence per fix

**#1 LOG_LINE / replay**
- Reproduced on `ba390cb`: `server.replay(base)` with `logs/run_*.log` → `NameError: name 'LOG_LINE' is not defined`.
- Regression test **before** the fix: `python3 run_tests.py test_ui.py` → `248 passed, 3 failed` (failing check: "Replay of a finished run with a run log does not crash (NameError: name 'LOG_LINE' is not defined)").
- **After** the fix: `251 passed, 0 failed`.
- Real launcher: `atlas_demo.py --replay --base <run folder>` crashes on `ba390cb` (screenshot `01`) and serves HTTP 200 with the replayed shipment and log on `c59e89d` (screenshots `02`, `03`).

**#2 test_atlas_skills isolation**
| Command | Before | After |
|---|---|---|
| `python3 test_atlas_skills.py` | 80 passed, 0 failed | 80 passed, 0 failed |
| `ATLAS_SEARCH=0 python3 test_atlas_skills.py` | 77 passed, 3 failed | 80 passed, 0 failed |
| `python3 run_tests.py test_atlas_skills.py` | 77 passed, 3 failed | 80 passed, 0 failed |

**#3 escape warning**
- Before: `python3 -W error -m py_compile test_flight_status.py` → `SyntaxError: invalid escape sequence '\d'`.
- After: compiles with warnings as errors.
- The runtime value of the string is **identical**: same SHA-256 (`1c2dd0e1…dfce9a1`) of the parsed string before and after.
- `python3 run_tests.py test_flight_status.py` → `87 passed, 0 failed`.

## Full suite

| Run | Command | Result |
|---|---|---|
| Baseline `ba390cb` (scratch folder) | `python3 run_tests.py` | 71 suites, **4,594 passed, 8 failed, 7 skipped** |
| Fix branch `c59e89d` (readable worktree) | `python3 run_tests.py` | 71 suites, **4,613 passed, 0 failed, 6 skipped**, 0 suites with problems, 1,553 s |

Why the counts differ: +7 new or now-passing checks from the fixes (4 replay checks; 3 skills checks).
`test_release.py` ran its Git checks this time (39 passed vs 32 + 1 skipped), because the worktree
is a Git checkout. `test_logging.py` went from 24/5 to 29/0.

**The 5 test_logging failures:** the suite drops to user `nobody`. In the baseline run the project
lived under `/tmp/claude-0` (mode `700`, root-only), and `su nobody -s /bin/sh -c "ls …/carrier_pacing.py"`
→ `Permission denied`. The child failed with `ModuleNotFoundError: No module named 'carrier_pacing'`.
In the readable worktree the same unchanged suite passes 29/29. No code change was involved.

**Skips (6), each with the suite's own stated reason:** Tesseract not installed (4,
`test_intelligence`), PostgreSQL not configured (1, `test_platform`), real eHub only on the
Windows worker with `ATA_REAL_EHUB_TESTS=1` (1, `test_po`).

## Diff review

- Only the 4 files above. Ignored test by-products (`C:\Automation/`, `__pycache__/`,
  `dashboard/.runtime/`) aren't committed.
- No secret-like additions, no behaviour change outside `replay()`. Assertions weren't weakened or
  skipped.

## Found during this work, not fixed (outside the approved scope)

1. `dashboard/static/index.html:5020-5023`: the finished-run ATLAS card says "written and verified"
   from the counters alone (see `EXECUTIVE_DEMO_PLAN.md` §4).
2. `update_eta.py:12106-12116`: a re-run for a shipment on another Hub page is dropped while the log
   says it "will be picked up".
3. ATLAS failure wording names the carrier site for a Hub Manage-page error (screenshot `05b`).
4. `README.md` / `START_HERE.md` §3–4 are stale and quoted by ATLAS's handbook search.
