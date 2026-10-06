# START HERE

Mantrac Logistics — Shipment ETA Automation and Control Tower.

---

## What changed in this build

**New · Top navigation, Tracking map, On the way.**
- **Navigation:** pages are a pill bar in the header (the sidebar is now only
  the phone and tablet menu). Settings is the gear on the right; Export CSV
  sits beside the run status.
- **Tracking:** the selected shipment drawn as a journey on a map (Hub → Carrier →
  Data → Hub write → Verified). It is not GPS; the vehicle is the real mode:
  plane, ship or van.
- **On the way:** shipments with an ETA and no ATA yet. The van drives only
  while there is at least one.

**New · The remote Control Tower (PLATFORM.md).** The dashboard can now run
as a multi-user web app at a stable address (e.g. https://ata.mantrac.com):
each person signs in with their own account (or Microsoft), sees only the
controls their role allows (Admin, Operator, Viewer — enforced on the server),
and every sign-in, run, Human Action, approval and access change is in the
audit log. Start automation creates the run on the server and a Windows
worker runs the same update_eta.py. Open & Continue shows the run's own
carrier tab in your browser: you complete the verification there and the run
carries on by itself — no RDP. Light, Dark and System themes, kept per user.
The local dashboard is unchanged. Setup, Azure deployment and limitations:
PLATFORM.md.

**Fixed · Transport mode is how a shipment moves, not who tracks it.** DHL
tracks the K-references, but an airline flies them — K223259 is Brussels
Airlines, K179801 is KLM — and they showed as Road. The mode now comes from the
carrier the Hub names first (an airline is Air, a shipping line is Ocean), and
from the tracking provider only when the name says nothing. DHL Express is Air;
DHL road freight, named so, is Road. The shipment card also says **Tracked by
DHL** when the tracker is not the carrier.

**New · ATLAS minds its manners — and yours.** Swear at ATLAS ("fuck you",
"you're useless", "احا", "a7a ya atlas") and it asks, calmly, for respectful
language and offers to help; a second and third time it is firmer. Swearing
about something ("this shit is broken") gets a request to keep it clean and an
offer to check the run. A real question inside a rude message ("what the fuck
failed?") is still answered, after a short note. The reply shows in amber with
a small head-shake. English, Egyptian Arabic and Franco-Arab are recognised;
the words are never stored, only the label.

**New · The ATLAS film is the introduction.** The dashboard opens with the
30-second ATLAS film from the design handoff: ATLAS watches the network, a
shipment fails, it diagnoses, plans, recovers, asks a person for one step,
takes over and learns, ending on the MANTRAC lockup with **Open dashboard**.
Once per browser session; **Skip intro** or Escape leave it at any point;
replay it from the menu (Introduction) or Settings, or open `/intro`. It is
drawn live from one local file (`dashboard/static/intro/film.js`) — no video,
no audio, nothing fetched from the internet — and removed completely when it
ends. With reduced motion turned on, it shows the closing frame only.

**New · ATLAS learns from real runs — and earns its stars.** Every real run
now feeds ATLAS's learning store (`ml/data/intelligence/`): how each shipment
ended, which navigation and recovery strategies were tried, closed Human
Action tasks, what operators ask, and real screenshots.

- A strategy is credited **only** when it worked and the shipment then ended
  with a read-back-verified Hub write. Anything else is shown as unverified
  and never counted.
- ATLAS page → **What ATLAS has learned**: recurring issues, the best verified
  strategy with its sample and confidence, Human Action patterns, proposals
  (recorded for a person to approve; nothing changes the automation by
  itself) and the **monthly review**.
- **Review and decide:** `atlas_review.bat` (or `python -m intelligence.review`)
  — `status`, `review`, `learned`, `proposals`, `approve ID --by NAME`,
  `reject ID --by NAME`. Approving records who decided and when; the change
  itself still goes through a tested deployment.
- **Evidence:** real screenshots are kept for failures, recoveries, the result
  page after a human step, and each read-back-verified Hub write (viewport
  only). Never of a verification screen.
- **Stars** are earned at a monthly evaluation, once a month has ended, and
  only when every criterion of the next level is met (one level at most per
  month). Time alone never adds a star.
- Ask ATLAS: "What have we learned?", "What keeps failing?", "Which recovery
  strategy works best for AFKL?", "Create a recovery plan", "Why do you
  recommend this?", "What changed compared with last month?", "What is
  ATLAS's star level?", "Show the screenshot from the last AFKL failure".
- **Screenshots:** attach one with the paperclip (or paste it). ATLAS lists
  what it can read as visual facts, what it can't read reliably, and any
  inference with its reason. Reading uploaded images needs Tesseract OCR
  (optional): on Windows install it from the UB Mannheim build, or set
  `TESSERACT_CMD`. Captured screenshots are read from the page text saved with
  them, so they need nothing extra. Security-code screens are never captured,
  read or kept.

**New · Human Action Queue and ATLAS as your copilot.** A carrier page that
needs a person no longer holds up the whole run.

1. If nobody acts within `HUMAN_QUEUE_GRACE_MS` (default 30 s), the shipment
   is **parked** in the Human Action queue and the run carries on with the
   next one. Nothing is written for it.
2. The **HUMAN ACTIONS · N** card shows every parked shipment with how long
   it has waited. Press **Open & Continue** on one (or ask ATLAS: "resume
   Grimaldi").
3. Between shipments, the run looks that shipment up again in its own
   browser and brings the carrier tab to the front of its Edge window, at the
   verification step.
4. You do **only the verification** (for GNET: type the code, press Search).
   No Resume press is needed: the run reads the page twice to confirm the
   right shipment is there, then extracts, validates, writes and reads back
   by itself. ATLAS reports each step only when the state confirms it.

- Task states: `WAITING_FOR_HUMAN`, `OPERATOR_OPENED`, `VERIFICATION_PENDING`,
  `HUMAN_COMPLETED`, `POST_VERIFICATION_CHECK`, `RESUMING`, then `SUCCESS`,
  `TIMEOUT`, `HUMAN_SESSION_LOST`, `VERIFICATION_NOT_CONFIRMED` or `FAILED`.
  Kept in `logs/human_queue.json`.
- If the window runs out after you open a task, it goes back to the queue
  (up to 3 tries). Before the run ends it holds up to `HUMAN_QUEUE_HOLD_S`
  (default 600 s) for parked tasks; what is left then times out.
- `HUMAN_QUEUE=0` restores the single in-place wait described below. A wait of
  0 (unattended) never queues.
- ATLAS answers from the run only ("What needs me?", "Why did AFKL fail?",
  "Open it", "How long has it been waiting?", "Show recovery attempts",
  "What should I do next?"). It navigates the dashboard by itself, but the
  only thing it can ask the run to do is Open & Continue, and only when you
  asked for it and named which shipment. It never reads, solves or types a
  security code.

**New · ATLAS has a face.** A small companion character for the intelligence
layer, in six states that follow the run state: Ready, Monitoring,
Analyzing, Recovering, Verified and Human action required. It appears on the
Overview's ATLAS card, in the human-action card, at the top of a shipment's
details, and as a panel on the ATLAS page. It speaks in short operational
lines ("Grimaldi Lines requires human verification.", "ATA validated. Hub
read-back confirmed."). Final designed renders can replace the drawn
character without code changes: see `dashboard/static/assets/atlas/README.txt`.

**New · Dashboard redesign (UI only).** The Control Tower now looks like a
Mantrac product: a MANTRAC · ATA Control Tower brand bar, a compact sidebar
(Overview, Live Runs, Shipments, Carriers, ATLAS, Human Action, History,
Settings), five overview metrics, and a Live Operations table.

- **Pipeline bar:** each table row shows how far the shipment has got.
  Select a row to open its details on the right: status, step, ATA and where
  it was read, read-back result, run ID, and the full timeline.
- **Human verification:** shown as a notification card with **Open Session**
  and **Resume** (same behaviour as before).
- **Logo:** the official Mantrac | CAT logo is in
  `dashboard/static/brand/mantrac-logo.png` (from the ATLAS film handoff). A
  sharper `.svg` of the same name, if you have one, takes precedence.

**New · Human in the loop (Grimaldi first).** When a carrier page needs a
person, the run no longer skips the shipment. It pauses that shipment in the
same run and keeps the browser tab open where it stopped:

1. The dashboard shows **HUMAN ACTION REQUIRED**: carrier, shipment, run ID,
   reason and how long it has been waiting.
2. **Open Session** brings that exact tab to the front of the
   automation's Edge window on the automation server. Work in it there, or
   over Remote Desktop to that machine.
3. Do the step (for GNET: type the security code and press Search), then
   press **Resume**. If the result isn't on the page yet, the run says so and
   keeps waiting.
4. The run reads the result, checks it is this shipment, validates the
   dates, writes them and reads them back. Only then is it SUCCESS.

- If nobody finishes within `HUMAN_WAIT_MS` (default 3 minutes; the old
  `CAPTCHA_WAIT_MS` still works), the shipment becomes **HUMAN TIMEOUT**.
  Nothing is written, and it is looked up again next run.
- If the tab or the run process is gone, Resume says the session is no
  longer available, and the shipment is **FAILED (HUMAN SESSION LOST)**.
- `HUMAN_AUTO_RESUME=0` makes Resume the only way to continue. By default,
  the run also continues by itself once the page shows the step done.
- The first dashboard tab to press Open or Resume holds that session.
  Another operator's Resume is refused.
- Every event (`HUMAN_VERIFICATION_DETECTED`, `WAITING_FOR_HUMAN`,
  `HUMAN_SESSION_OPENED`, `HUMAN_RESUMED`, `HUMAN_RESUME_FAILED`,
  `HUMAN_TIMEOUT`, `ATA_EXTRACTION_AFTER_HUMAN`, `SUCCESS`) goes to the run
  log and to `logs/human_actions.jsonl`, with the run ID. The open action is
  kept in `logs/human_action.json`.

**New · DHL K-references and seven ocean carriers.** A reference like
K179801 goes to DHL. CMA CGM, MSC, Grimaldi, COSCO, Maersk, ONE and
Hapag-Lloyd are picked by the Hub's Carrier Name column.

- **Ocean results are written to the Hub (since 6 Oct).** The carrier page
  must carry the shipment's reference letter for letter, the dates must be
  real dates, and after Save the shipment is reopened and the field read
  back: it is **Success only when the Hub reads back the date written**. A
  read-back that cannot be done is *not* a success. To stop ocean writing on
  a machine, set `OCEAN_WRITE=0`.
- **A carrier restricts access (CMA CGM, 6 Oct)?** A completed human
  verification is *not* access: the run records HUMAN_VERIFICATION_COMPLETED,
  and CARRIER_ACCESS_CONFIRMED only when the shipment page itself is read. If
  the carrier shows its restriction page — e.g. "Access is temporarily
  restricted", even right after you completed the verification — the lookup
  stops at once: nothing is extracted or written, the shipment ends FAILED /
  CARRIER ACCESS RESTRICTED (parked, recovery required), and the page's URL,
  title, text and a screenshot are kept, with the run's checklist:
  verification_completed, carrier_access, extraction, hub_write, final_result.
  A verification that is followed by neither the shipment page nor a
  recognised restriction ends CARRIER ACCESS NOT CONFIRMED. Neither is
  retried, nothing tries to get past it, and the cleared verification is not
  learned as a success. Ask ATLAS "Why did the error happen?" and "What
  should I do next?" — it answers from that evidence with a diagnose-first
  plan. To find out why, on the worker:
  `verify_carrier.bat CMA_CGM <reference>` — records VPN, proxy, public IP,
  network (domain / hotspot indicators) and Edge, opens the carrier in your
  normal Edge (you say what it shows) and in the automation's browser, and
  states what the restriction follows only as far as that evidence shows.
- **Prove the real eHub, on the worker** (see PLATFORM.md, *Real eHub
  verification*):
  - `verify_ehub.bat` — read-only: eHub loads, the shipment list renders, a
    shipment Under Clearance and its status are read. REAL OBSERVED, or
    REAL VERIFICATION BLOCKED with the exact reason.
  - `verify_eta.bat MEDUAHP69377` — the normal automation for that one
    shipment: carrier ETA → write to eHub → read back → compare. REAL
    VERIFIED only when eHub reads back the date written. It writes to eHub
    like a normal run.
- **Hapag-Lloyd:** a container number (HLCU1234567) uses *Tracing by
  Container*. A bill of lading (HLCUTA12609EPQF2) uses tracing by booking.
- **Grimaldi (GNET) needs a person for every search.** The run fills in
  *Shipment #* (or *Equipment #* for a container) and then waits for you to
  type the security code and press **Search**. It never reads or types the
  code. If nobody does it within `CAPTCHA_WAIT_MS` (3 minutes by default),
  the shipment shows HUMAN VERIFICATION REQUIRED and is left for the next run.
  With `CAPTCHA_WAIT_MS=0` (unattended), Grimaldi rows are skipped without
  opening GNET.
- **CMA CGM's "slide right" check** is detected, and the run waits for a
  person to do it. It is never solved automatically.

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
**Intro film** — the 30-second ATLAS film (also at `/intro`)

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

## 10b. ATLAS research — shipments, vessels, ports, errors

ATLAS always answers run facts from the run. When a question needs more —
"where is it?", "why is it late?", "check the vessel / the port", "is CMA CGM
having issues today?", "why did this fail and how do I fix it?" — it can
research the public web itself (Claude, with web search and web fetch), and
answer like a colleague: what the run says, what it found, what it could not
verify, and what it recommends, with the sources it actually checked.

Off until you give it a key (on the control plane, and on the local dashboard
PC if you use that):

```bat
set ATLAS_RESEARCH_API_KEY=sk-ant-...
```

What leaves the building: the question, and a short brief of what the run
holds about that shipment or failure (reference, carrier, dates, the recorded
error) — URLs without their query strings, nothing named like a secret. No
credentials, security codes or CAPTCHA values are ever in that brief.
`ATLAS_RESEARCH=0` turns it off again.

Rules it works under: run facts win over the web (a disagreement is stated,
never silently replaced); a source is shown only if a real search or fetch
returned it in that request; it never suggests bypassing CAPTCHA, carrier
restrictions or authentication; and nothing it researches is learned as fact —
only a recovery that was applied and then verified by the run is.

Without a key, ATLAS still explains failures (causes ranked CONFIRMED / LIKELY /
POSSIBLE / UNKNOWN against the run's evidence), answers general logistics
questions, and says plainly when something would need research.

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
