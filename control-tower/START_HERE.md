# START HERE

Mantrac Logistics — Shipment ETA Automation and Control Tower.

---

## 1. Install

Extract this ZIP into `C:\Automation`, using **Extract All** so the folders are
kept. Overwrite when asked.

```
C:\Automation\
    START_TOWER.bat          <- start here
    update_eta.py            the automation
    dashboard\               the Control Tower
    ...
```

Nothing of yours is overwritten. `credentials.txt`, `logs\`, `screenshots\` and
`tracking_results.csv` are not in this ZIP.

**Requirements:** Python 3.8+ and Playwright, both already on the machine.
Nothing else — no npm, no build step, no new services.

---

## 2. Run it

**Double-click `START_TOWER.bat`** and click Yes on the Windows prompt.

The dashboard opens and stays open whether or not a run is going. Press
**Start run** in the header when you want the automation to work.

The console prints two links:

```
  ON THIS MACHINE:
    http://127.0.0.1:8787/?key=mantrac2026

  SEND THIS TO COLLEAGUES:
    http://MANTRAC-PC:8787/?key=mantrac2026
    http://10.20.30.40:8787/?key=mantrac2026
```

Send colleagues one of the **bottom two**. `127.0.0.1` means "this computer" on
whichever machine opens it, so it will not work for them.

### Other ways to start

| Command | Dashboard | Start button |
|---|---|---|
| `START_TOWER.bat` | stays up always | **yes** |
| `START_SHARED.bat` | lives inside the run | no |
| `python update_eta.py` | lives inside the run | no |
| `share_dashboard.bat` | review a finished run | no |

---

## 3. What is in the dashboard

**Overview** — live status, counters, timeline, carrier health
**Live operation** — the shipment being processed right now, step by step
**Shipments** — every shipment this run, searchable and filterable
**Analysis** — filter by result, carrier and date, group the outcome, **download CSV**
**Systems** — DHL, Qatar Airways, the Hub and the browser
**Exceptions** — every failure with its shipment, carrier, step and reason
**Activity log** — the full run log, searchable by level
**Intro film** — the scroll-driven story of the project

### The assistant

Bottom-right, **Ask the Tower**. It answers only from this run's data.

```
hi                                  how is it going?
where is 33 2323 9905?              compare the carriers
why did 8842001173 fail?            what was the slowest shipment?
which shipments failed?             download the data
re-run 157-49568713                 how long has it been running?
```

It has no language model behind it, deliberately: it cannot invent an ETA, a
carrier or a status. If the automation did not collect something, it says so.

---

## 4. Settings you may want to change

All near the top of `update_eta.py`:

| Setting | Default | What it does |
|---|---|---|
| `DRY_RUN` | `False` | `True` fills the dates but saves nothing — safe for testing |
| `DASHBOARD_HOST` | `"0.0.0.0"` | `"127.0.0.1"` makes the dashboard this-machine-only |
| `DASHBOARD_ACCESS_KEY` | `"mantrac2026"` | the key required in the link |
| `DASHBOARD_ALLOW_CONTROL` | `False` | Pause/Stop when the dashboard runs inside the automation |
| `MAX_RECORDS_PER_RUN` | `200` | shipments per run |
| `TARGET_STATUS` | `"Under Clearance"` | the hub filter |

`START_TOWER.bat` enables control regardless — that is its purpose.

**Anyone with the link can Start and Stop the run.** There is no viewer-only
role. Bear that in mind before sharing widely.

---

## 5. If something is wrong

**Run `check_dashboard.py`** — it names the cause rather than guessing.

| Symptom | Cause |
|---|---|
| Colleague sees "refused to connect" | They used the `127.0.0.1` link, or the firewall is blocking. `START_TOWER.bat` opens the port. |
| Film shows no photographs | The `dashboard\static\film\` folder did not come across. Re-extract the whole ZIP. |
| Analysis looks empty | Set **Result** to *All results* — it may be filtered to a state this run has none of. |
| `PermissionError` on the log | The chosen folder is not writable. The console names the folder it fell back to. |

Every run writes a log to `C:\Automation\logs\`. It is the first thing to read,
and the first thing to send if you need help.

---

## 6. Adding your own photographs to the film

Drop images into `dashboard\static\film\`, named so they start with the scene
number: `04-vessel.jpg`, `05-arrival.jpg`. They appear with no code change.

Drop an `.mp4` in the same folder and the film scrubs the video with the scroll
instead of crossfading the photographs.

---

## 7. Tests

```
python test_automation.py      the DHL state machine, cookies, retries
python test_ata_field.py       the ATA field and Manage tabs
python test_hub_waits.py       hub readiness and the stale-table race
python test_hub_nav.py         navigation reuse safety
python test_coe_fallback.py    COE/BU view handling
python test_dhl_data.py        DHL date extraction
python test_logging.py         log paths, rotation, secret redaction
python test_assistant.py       the assistant, including anti-fabrication
```

374 tests. They run without a browser or credentials, so they are safe to run
on any machine at any time.

---

## 9. Checking the install

Before a real run, from `C:\Automation`:

```bat
run_tests.bat
```

603 tests, no browser and no network needed. It should end with
`603 passed, 0 failed`. If Python or Playwright is missing it will say so.

If this is a fresh machine:

```bat
pip install -r requirements.txt
python -m playwright install chromium
```

---

## 10. The learning layer

`ml\` is an optional layer that can, once trained, reorder the field-lookup
candidates and shorten waits based on what has actually worked. **It is off.**
With `ML_ENABLED` unset the automation behaves exactly as it did before the
layer existed.

Telemetry is collected from the first run — it changes nothing, it only
records what was tried and what happened, so a model can eventually be built
from real evidence rather than guesses.

```bat
REM after some runs, see how much data there is
python -c "from ml import telemetry; print(telemetry.stats())"

REM train (refuses, and says why, until there is enough)
python -m ml.trainer --show

REM prove it beats the current order before trusting it
python -m ml.evaluator
```

Only switch it on if the evaluator prints `VERDICT: BETTER`.

Full detail, including every setting and what the model is never allowed to
decide, is in `ML.md`. The map of how the existing automation works is in
`ARCHITECTURE.md`.

---

## 11. Proving a write really landed

`VERIFY_AFTER_SAVE` reopens each shipment after saving and reads the field back
from the Hub, failing the shipment if the value is not there. It is off by
default because it adds a reload to every write.

```bat
set VERIFY_AFTER_SAVE=1
python update_eta.py
```

Use it for a first run on a new environment, or whenever you want proof rather
than a completed postback.
