# ATLAS Enterprise Logistics Control Tower — audit and plan

Stage A of the unified platform build (29 Oct 2026). This is a review document:
nothing in the product changes until it is agreed. It records what exists
today, what each navigation item in the reference design would need, and how
the new interface is introduced without touching the working automation.

Inputs: the ATLAS mock-up PDF (28 Oct, "Supply chain hub"), the two reference
videos (an Apple-Maps-style 3D map with a bottom sheet; a light isometric
"WareTrack" yard with animated trucks and forklifts under glass panels), and
the written brief. The images are visual references only — never a source of
operational data.

---

## 1. What the repository is today

| Area | Where | Notes |
|---|---|---|
| Branches | `claude/atlas-potato-garden` (working), `claude/maia-m6rhri`, `claude/start-work-d53i2b` | No separate production branch; releases are ZIPs from `make_release.py`, installed on MAN-ATAAZ-01 (Windows Server 2022). |
| Web server | `dashboard/server.py` (stdlib `http.server`, ~2,100 lines) | One process, port 8787, behind IIS at `ata.mantracgroup.com`. SSE live stream at `/api/stream`. gzip + ETag. |
| UI | `dashboard/static/index.html` (~8,200 lines, vanilla JS), `clearance.html` (~1,300), shared `platform.css`, `search.js`, `todo.js`, `trackmap.js` | No framework, no build step, no npm. Every asset is served from the repo. |
| ATA automation | `update_eta.py` (~12,700 lines) | Entry `main()`. Hub sign-in → COE/BU view → per shipment: carrier lookup (DHL, Qatar, AFKL, ocean portals) → ETA/ATA → Hub write → read-back verification. Run limits, carrier pacing, holds (CMA CGM), dry run. |
| Live state | `dashboard/bridge.py` | The automation publishes run, shipment, provider-result, human-action and verification events; the server snapshots them to the UI. The UI never computes automation state itself. |
| Run control | `dashboard/control.py`, `/api/control` | Start / dry run / pause / stop are *requests* the automation honours between shipments. Disabled unless switched on. One tower per port (exclusive bind). |
| Human Action | `human_queue.py`, `dashboard/live_view.py`, `/api/human`, `/api/session/` | Pause inside a shipment, remote live view, "open" and "resume" only; nothing typed into a page by the platform. Security codes never handled. |
| PO automation | `po/` (pipeline, extract, validate, template, mail, ehub, store), `/api/po` | BOE PDF → duty template → validated → email (send/resend permissions). |
| Clearance | `dashboard/clearance.py`, `clearance.html`, `/api/clearance*` | eHub "Shipment Update" export (.xlsx) uploaded or dropped in an inbox; escalations, contacts, draft mails, To Do. |
| ATLAS | `dashboard/assistant.py`, `intelligence/` (router, kb, kb_answer, converse, research, factguard…), `knowledge/` (238 items) | Local model (Ollama, qwen3:30b on the server), SearXNG web research, Snowflake Cortex optional. Evidence-labelled answers, abstains. |
| Accounts | `controlplane/` (users, rbac, audit, oidc) | Roles ADMIN / OPERATOR / VIEWER, 23 permissions, server-side checks, audit log, optional Entra ID. |
| Learning | `ml/` | Shadow mode; no champion model on the server yet. |
| Worker | `worker/` | Verification of eHub writes, carrier access checks. |
| Tests | 93 suites, ~5,600 checks (`run_tests.py`) + 28-step release smoke test | Real browser tests with Playwright/Chromium. |
| Deployment | `deploy/iis`, `INSTALL_SERVER.bat`, scheduled task "Mantrac Tower" | Python 3.12 on Windows; no admin rights needed for daily use. |

## 2. Capability inventory

**1 = existing and verified · 2 = existing but incomplete · 3 = needs new
implementation · 4 = blocked (missing data, credentials, permissions or an
external integration).**

| Navigation item (brief) | Class | What exists / what is missing |
|---|---|---|
| Overview | **2** | KPIs, Tracking map, To Do, On the way, ATLAS. Missing: the stage strip (in transit / arrived-under-clearance / cleared-awaiting-delivery) and a live shipment list with drill-down on the same page. |
| Shipment Tracking | **2** | Run records (carrier ETA/ATA, places, milestones, evidence) and the eHub export (step, ETA, ATA, on-time, agent, forwarder) both exist, but as two pages with two vocabularies. Missing: one shipment view joining them by AWB/BOL, a timeline, source attribution per field. |
| ATA Tracking / Automation | **1** (engine) / **2** (workspace) | Start/stop/dry run, live progress, human actions, read-back verification, evidence, logs, run history — all real. Spread across Overview, Shipments, History tabs; needs one workspace page. |
| Clearance Management | **1** | eHub export, filters, drill-down tables, escalations, contacts, mails, headline. |
| Documents | **2** | PO documents (BOE PDFs, generated templates) and run evidence (screenshots, page text) exist. **No clearance-document list** (certificate of origin, packing list…): the eHub export does not contain one, so "documents missing" cannot be claimed (class 4 for that part). |
| To Do & Approvals | **2** | To Do (run failures, past due, corrections, missing contacts), Human Action queue, ATLAS knowledge proposals, PO send/resend. Missing: one page listing approvals with priority, owner and status. |
| Rate Management | **4** | No freight-rate, quote or tender data anywhere in the platform. Needs a data source (OTM export, forwarder rate sheets, or a rates table someone owns) before anything is shown. |
| Carrier Management | **2** | Real carrier data: holds, pacing, access checks, failures by carrier, timings (`intelligence/carrier_health.py`, telemetry). Missing: a carrier page; service KPIs need more history. |
| Analytics & KPIs | **2** | Run analysis, clearance KPIs (on-time, cycle, past due, stage targets), knowledge KPI definitions (OTIF, dwell…). Missing: one KPI catalogue page with definitions, periods and denominators. |
| ATLAS AI Assistant | **1** | Evidence-grounded, page-aware, abstains, local model, permission-checked. |
| Reports | **2** | Run PDF, shipment PDF, CSV exports. Missing: a reports page and scheduled reports. |
| Master Data | **2** | Clearance contacts (agents, BUs, forwarders), carrier/AWB prefixes, place resolver (airports, ports, cities). Missing: one page to view and correct them. |
| Settings | **1** | Users and access levels, theme, preferences, audit log. |
| Spot Tender (mock-up) | **4** | No tender data or process in the platform. |
| Optimization (mock-up) | **4** | Needs rates + lanes + volumes; none available. |

Booked shipments (mock-up "Booked 12"): **4** — neither the Hub run nor the
eHub export carries bookings.

## 3. Data definitions for the new stage strip

All counted from the loaded eHub export (one row per shipment), never typed in:

| KPI | Definition | Source field |
|---|---|---|
| Shipments | rows in the export within the filters | all |
| In transit | open, no ATA, ETA on or after the data date | `ata`, `eta`, `step` |
| Arrived, under clearance | open, ATA present (or ETA passed), current step in Submit to BU … Customs Release | `ata`, `step` |
| Cleared, awaiting delivery | current step "Awaiting Receipt" | `step` |
| On time / late | the export's own On Time flag | `ontime` |
| Booked | **not shown** — no source | — |

The run's own shipments (Hub + carriers) keep their own states (written,
partial, skipped, failed, waiting for a person) — they are a different
population and are never added to the export's counts.

## 4. Architecture of the change

```
Browser ──▶ IIS (ata.mantracgroup.com) ──▶ dashboard/server.py :8787
                                          ├─ existing APIs (unchanged)
                                          ├─ /api/hub/*  new read-only views (join run + export)
                                          └─ static: hub.html + design system (new), old pages kept
update_eta.py ──events──▶ bridge.py ──snapshot/SSE──▶ every page
update_eta.py ◀──requests── control.py ◀── /api/control (unchanged)
```

Rules:

1. **The automation is not modified for the UI.** The new workspace reads the
   same bridge snapshot and SSE stream and sends the same `/api/control`
   requests the current dashboard does. No new write path to the Hub.
2. **New endpoints are read-only views** over existing stores (bridge
   snapshot, clearance export, escalations, PO store, telemetry), behind the
   existing permission checks. Each is checked against existing endpoints
   first; nothing is duplicated.
3. **Same domain, same sign-in.** New pages live on the same server and
   session as today (`/hub`, then `/` once accepted).
4. **Incremental switch.** The new shell ships next to the current pages.
   Every module links to the current page until its new view is equal or
   better; then the old view is retired. No big-bang replacement.
5. **No new runtime dependencies.** No framework, no npm, no external map
   tiles. Maps and animations are drawn from data the server already serves
   (Natural Earth outlines in `static/maps`, the place resolver).

## 5. Design system ("Apple" direction)

* **Light by default** (as in both reference videos), with a dark mode in
  midnight navy for control-room screens. One set of tokens in a single
  stylesheet; every page uses it.
* **Type:** the system UI stack (`-apple-system, "SF Pro Text", "Segoe UI
  Variable", Inter…`) — SF on Apple devices, Segoe on the Windows server.
  Tabular figures for numbers.
* **Surfaces:** frosted glass panels (`backdrop-filter`) over the map and the
  operations scene; solid cards elsewhere; 14–20 px radii; soft, low shadows;
  hairline separators.
* **Colour:** neutral greys, one system blue for action and selection, green
  / amber / red only for status.
* **Components:** sidebar, KPI chip, stage strip, sheet (Apple Maps bottom
  sheet), segmented control, pill button, drawer, table, timeline, empty /
  stale / no-permission states.
* **Motion:** short, purposeful (sheet slides, pin drops, route draw-on);
  everything respects "reduce motion".

## 6. Maps "next level"

* **3D tilted map** (pitch, rotate, zoom) drawn on canvas with extruded,
  soft-shaded land in the clay style of the Apple Maps reference; water,
  coastlines, country borders; no external tiles.
* **Pins** only at places a carrier or the eHub export actually named (place
  resolver: airports, ports, cities). Each pin opens an **Apple-Maps-style
  sheet**: place, counts by stage, shipments there, last update, a small
  "by hour/day" bar chart from real timestamps, and actions (open list,
  ask ATLAS).
* **Routes** drawn origin → last reported place → destination, with the
  driven part solid and the remaining part dashed; never a guessed position
  between reported places.
* **Freshness** on every pin and sheet ("reported 11 Oct 08:38, 18 days ago").

## 7. Daily operations scene

The isometric scene from the WareTrack reference, drawn in SVG, shows the
day's flow as **an illustration whose quantities are real**: inbound lane
(in transit), quay/apron (arrived, under clearance), customs gate (at a
customs step), bonded yard (awaiting receipt), out-gate (completed today).
Each zone's vehicle count is the real count from the export (capped, with the
number on a label), and clicking a zone opens its shipment list. It is
labelled "illustration — counts from the eHub export as of <date>".

## 8. Stages and what each delivers

| Stage | Delivers | Exit check |
|---|---|---|
| **A** | This audit and plan | Agreed |
| **B** | Design tokens + app shell (`/hub`): sidebar, top bar, search, To Do, profile, theme; all modules listed with status (live / partial / not connected) | Old pages unchanged; shell tests; screenshots light/dark/desktop/tablet |
| **C** | Overview + Shipment Tracking: stage strip (§3), 3D map + sheets, live shipment list and cards, one shipment view joining run + export | Counts equal the Clearance page's; no position without a source |
| **D** | ATA workspace: run status, queue, human actions, verification, evidence, errors, run history — on the existing APIs | Existing automation tests + Human Action e2e pass unchanged |
| **E** | Clearance, Documents (what exists), To Do & Approvals, Carrier, Master Data views | Each view's numbers traced to a source |
| **F** | ATLAS in the shell, KPI catalogue, Reports page, security review, full suite, smoke | Full suite + smoke + review |

Rate Management, Spot Tender and Optimization stay visible as **"not
connected — needs a rates source"** until a data owner and source exist.

## 9. Not done without explicit approval

Deploying to MAN-ATAAZ-01, live Hub writes, production runs, new credentials
or external services.
