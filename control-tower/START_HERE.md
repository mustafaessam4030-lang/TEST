# START HERE

Mantrac Logistics — Shipment ETA Automation and Control Tower.

---

## What changed in this build

**1 · The AFKL lookup was leaking a browser context per shipment.** Each one
stayed open for the rest of the run holding a page on the carrier, and behind
it a live connection. That is the "Edge cannot open AFKL while the automation
runs, and can again the moment it stops" you saw on the server. Measured over
8 lookups: 9 contexts and 9 pages before, 1 and 1 after.

**2 · myCargo has two forms, and the automation could not tell them apart.**
When the air waybill box had not rendered yet, it typed the AWB into the
flight number box and pressed Enter — which is the "Field is required" and
"Please select a valid date" in your screenshot. It cannot reach that form by
accident any more.

**3 · The flight status card is now used on purpose**, and the Hub row decides
which question the carrier gets asked:

| The Hub row carries | What is asked |
| --- | --- |
| an air waybill | Track a shipment, as always |
| a flight and a date | Check flight status |
| both | both — air waybill first |

A flight arrival is filed as an **estimate**, never as an actual arrival: a
flight that landed proves the aircraft landed, and a shipment can be offloaded
while its air waybill still names that flight. It never overwrites a date the
shipment page gave. `AFKL_FLIGHT_STATUS=0` turns the whole path off.

### Two things to run once, and send me the output

```
diagnose_flight_status.bat AF0877 04/09/2026
```
Opens the real myCargo page and prints every input on it. My selectors for the
flight card are read off your screenshot, not the live page — this is what
confirms them.

```
diagnose_server.bat A        with the automation OFF
diagnose_server.bat B        running, but no AFKL lookup yet
diagnose_server.bat C        running, after one AFKL lookup
diagnose_server.bat D        stopped
diagnose_server.bat report
```
Measures the server itself — sockets, ephemeral ports, connections to the
carrier, browser processes and handles. **B is the one that decides it.** If
the fix above is the whole story, B passes and C fails on the old build.

There is also a line in every run log now saying which Hub columns were read
and which were not:

```
Hub table: no flight column recognised.
Hub table: columns present but not used — flt no, origin.
```

If your Hub prints the flight under a name I did not predict, that line is all
I need to add it.

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

## 10. ATLAS — the strategy engine

`ml\` is **ATLAS, the Adaptive Logistics Strategy Engine**. Once trained it can
reorder the field-lookup candidates and shorten waits based on what has
actually worked.

**It is in SHADOW mode, which means it changes nothing.** It watches, it
records what it *would* have chosen, and the automation runs its own hand-tuned
order exactly as it always has. There is also no trained model yet, so at the
moment it has no opinion to discard either.

Every run tells you which it is, in its own words:

```
[ATLAS] Adaptive Logistics Strategy Engine
[ATLAS] Mode: SHADOW
[ATLAS] Status: FALLBACK
ATLAS → Deterministic fallback: no trained model; the automation uses its
                                own hand-tuned order
```

On the dashboard, the ATLAS card lists what it did this run, and every shipment
carries a small tag: **ATLAS** if the engine steered that write, **Deterministic**
if the automation did it alone. `ATLAS → Action completed` appears only after
the date has been read back out of the Hub and confirmed — it is never written
for a save nobody checked.

Telemetry is collected from the first run. It changes nothing; it records what
was tried and what happened, so a model can eventually be built from real
evidence rather than guesses.

```bat
REM 1 - collect, WITH verification on. Without this nothing is trainable:
REM     a locator only counts as having worked if the date it wrote was
REM     still in the Hub when the automation looked again.
set VERIFY_AFTER_SAVE=1
python update_eta.py

REM 2 - see how much usable data there is
python -c "from ml import episodes; print(episodes.join()[1])"

REM 3 - train a challenger (refuses, and says exactly why, until there
REM     is enough). This does NOT go live.
python -m ml.trainer --show

REM 4 - prove it beats the current order
python -m ml.evaluator

REM 5 - promote. Only happens if step 4 said BETTER.
python -m ml.trainer --promote
```

Only after all of that, and only when you want ATLAS to actually steer:

```bat
set ML_MODE=active
```

To see the state of ATLAS on **this** machine — what it is allowed to claim,
what it cannot override, how much real data exists — run:

```bat
verify_atlas.bat
```

It is read-only: it trains nothing, writes no model, and does not touch the
telemetry file. If it prints anything other than `0 failed`, do not set
`ML_MODE=active`; send the output back instead.

Full detail — every setting, the exact truth condition behind each `ATLAS →`
line, and what ATLAS is never allowed to decide — is in `ML.md`. The map of how
the existing automation works is in `ARCHITECTURE.md`.

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
