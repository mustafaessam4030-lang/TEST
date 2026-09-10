# Shipment Control Tower

Live operations dashboard for `update_eta.py` — the DHL / Qatar Airways
COE ETA and BU ATA updater.

Every number on screen comes from the automation itself. Nothing is generated
to fill space; anything the script does not know shows as `—` or an
indeterminate progress bar.

## Install

Extract `Control_Tower.zip` into `C:\Automation` with **Extract All** (or any
tool that keeps folders). The result must look like this:

```
C:\Automation\
    update_eta.py
    check_dashboard.py
    check_dashboard.bat
    run_dashboard.bat
    README.md
    dashboard\
        __init__.py
        bridge.py
        server.py
        static\
            index.html
            music\
```

`dashboard` is a Python package that also carries the frontend assets — the
server imports `dashboard.bridge` and `dashboard.server`, and serves
`dashboard/static/index.html`. Nothing needs to be moved by hand.

If your extractor drops all the files flat into one folder, it still runs: the
imports fall back to a flat layout automatically. The nested layout above is
just the tidy one.

No `pip install` needed. The server is standard library only.
Optional: `pip install psutil` adds CPU and memory to the Systems page.

## Run

Start the automation exactly as before:

```
python update_eta.py
```

The dashboard opens automatically at <http://127.0.0.1:8787/> and stays live
after the run finishes, so a failed run can still be reviewed.

To inspect a finished run without launching Edge, double-click
`run_dashboard.bat` — it rebuilds the dashboard from `tracking_results.csv`
and the newest file in `logs\`.

Switches at the top of `update_eta.py`:

```python
DASHBOARD_ENABLED = True     # False runs the automation with no dashboard
DASHBOARD_PORT = 8787
DASHBOARD_OPEN_BROWSER = True
```

## Sharing the dashboard with other people

**Sharing is already switched on in this build.** `update_eta.py` ships with:

```python
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_ACCESS_KEY = "mantrac2026"
```

Change the key to whatever you like. To go back to this-machine-only, set
`DASHBOARD_HOST = "127.0.0.1"`.

**Start it with `START_SHARED.bat`.** It asks Windows for permission once,
opens the port, and starts the run. After that the printed link works on any
machine on the network with no further setup.

Running `python update_eta.py` directly also works and will open the port by
itself if the console happens to have Administrator rights; if not, it tells you
to use `START_SHARED.bat`. The console prints the exact links to send:

```
Control Tower running at http://127.0.0.1:8787/?key=pick-something
Share these links with colleagues on the same network:
    http://MANTRAC-PC:8787/?key=pick-something
    http://10.20.30.40:8787/?key=pick-something
```

They open the link once; the key is stored in a cookie so the live stream and
CSV download keep working as they navigate.

### Windows Firewall

The first time you do this, Windows will block the inbound connection. Run this
**once**, in an Administrator PowerShell:

```powershell
New-NetFirewallRule -DisplayName "Mantrac Control Tower" -Direction Inbound `
  -Protocol TCP -LocalPort 8787 -Action Allow -Profile Domain,Private
```

`-Profile Domain,Private` deliberately excludes public networks. To undo it:

```powershell
Remove-NetFirewallRule -DisplayName "Mantrac Control Tower"
```

### What you are exposing

The dashboard is **read-only**. It cannot start, stop or alter the automation,
and it holds no credentials — `credentials.txt` is never read by the server and
secrets are redacted before anything reaches the log or the browser.

What it *does* show is live shipment references, carriers, ETAs and errors, and
the assistant will answer questions about them. That is why the access key
matters once you leave loopback. Treat the link like an internal report.

This is plain HTTP on your own network — appropriate for a LAN, not for
exposing to the internet. If you need it outside the office, put it behind the
company VPN rather than forwarding the port.

### Review mode without a run

To let someone browse the last finished run without starting the automation:

```
python -m dashboard.server --share --key pick-something --replay
```

## Turning the automation on and off from the dashboard

Run it under the supervisor instead of starting `update_eta.py` directly:

```
START_TOWER.bat
```

The dashboard now stays open whether or not a run is going, and the header
gains **Start run**. When a run is going you also get **Pause / Resume** and
**Stop**. Colleagues use the same shared link.

The supervisor never touches shipments — it launches `update_eta.py`, reads the
state that run publishes, and relays requests back. A stop is still honoured
between shipments, never mid-write, and everything already saved to the Hub is
kept. With no run in progress the dashboard shows genuinely empty counters
rather than a stale picture of the last one.

`START_SHARED.bat` still works if you prefer the dashboard to live inside the
run and disappear when it ends.

## Controlling the run from within a run

Off by default — the dashboard is read-only unless you say otherwise:

```python
DASHBOARD_ALLOW_CONTROL = True      # in update_eta.py
```

With it on, the dashboard header gains **Pause / Resume** and **Stop**, and the
assistant accepts `Re-run 157-49568713`.

**Requests are never applied mid-shipment.** A click appends to a queue; the
automation reads that queue *between* shipments. A pause or stop can never
split a write to the Hub, and everything already written is kept. A re-run
jumps the queue for the current hub page so you see the answer quickly.

Note that anyone with the dashboard link gets these controls — there is no
separate viewer role. Leave it off if you are sharing the link widely.

## Partly updated shipments

A shipment where the COE ETA saved but the BU ATA failed is now recorded as
**PARTIAL**, not FAILED. It appears as *Partly updated* on the dashboard, counts
towards the success rate, and both the written date and the failure reason go
into `tracking_results.csv`. Previously the whole shipment read as a failure,
which understated work the automation had genuinely done.

## The DHL wait

DHL sometimes answers in under a second and sometimes takes half a minute. The
old code could not tell the difference — it did a blind `wait_for_timeout(34s)`
and, separately, had no idea what an unknown tracking number looked like, so a
number DHL rejected instantly still cost about two minutes.

`update_eta.py` now runs a state machine over the tracking page. Each poll takes
one text snapshot and classifies it:

| State | Meaning | What the automation does |
|---|---|---|
| `LOADING` | shell not painted | keep waiting |
| `PROCESSING` | Akamai interstitial | keep waiting, exempt from stuck detection |
| `COOKIE` | consent banner | accept it, carry on, no budget spent |
| `READY_RESULT` | Event Log rendered | **continue immediately** |
| `READY_NO_RESULT` | DHL says it has nothing | **stop immediately**, skip the shipment |
| `ERROR` | 5xx / blocked | **stop immediately**, screenshot, skip |
| `STUCK` | no progress for 25s | stop, classify as temporary |

Ceilings: `DHL_READY_MAX_SECONDS = 75`, `DHL_STUCK_AFTER_SECONDS = 25`. The 90s
processing watcher is untouched. Nothing was made faster by lowering a timeout —
the ceilings went *up*; what changed is that the automation now leaves the
moment DHL is actually done.

## The chatbot

Bottom-right of the dashboard. It has no language model, deliberately: every
sentence is assembled from fields in the live state, so it has no mechanism for
producing a value the automation did not collect. Ask it for a destination and
it tells you the automation never reads destinations. Ask about a shipment this
run has not touched and it says it has no record rather than guessing.

Read-only by construction — `dashboard/assistant.py` receives a state snapshot
and nothing else. No bridge handle, no browser, no credentials.

## What is real

| Shown | Source in the script |
|---|---|
| BOL / AWB, carrier, hub ETA, list page | `collect_supported_shipments` |
| Carrier status, carrier ETA, carrier ATA | `get_dhl_result` / `get_qatar_result` |
| COE ETA and BU ATA writes | `update_one_view` |
| Updated / skipped / failed counts | the `successful` `failed` `skipped` counters in `main` |
| Exceptions | `SkipShipment` and the general `except` branches |
| Log lines and levels | every `write_log` call |
| Systems status | hub login, carrier page results, Playwright session |
| Run config | `DRY_RUN`, `TARGET_STATUS`, `MAX_RECORDS_PER_RUN`, `MAX_TABLE_PAGES` |

Deliberately absent, because the script never sees them: origin, destination,
route, order ID, delivery counts. Total shipment count stays unknown until
pagination ends, so the progress bar runs indeterminate until then.

## Music

Put the track and artwork in `dashboard\static\music\`. See the README there.
Autoplay is attempted on load; if the browser blocks it a **Play music**
button appears and playback starts on the first click anywhere.

## Safety

`dashboard/bridge.py` wraps every hook in a `try/except` that swallows errors,
and `update_eta.py` falls back to a no-op object if the dashboard folder is
missing. A dashboard problem can never stop a shipment run.
