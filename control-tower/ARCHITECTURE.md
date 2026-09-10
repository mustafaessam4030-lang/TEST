# Control Tower — architecture map

Written before any ML code was added, from reading the code as it stands at
commit `b63daa3`. This is the map the ML layer had to fit into without
disturbing it.

## Entry points

| Entry point | What it starts |
| --- | --- |
| `update_eta.py` → `main()` | The run. Owns the browser, the shipment loop, the counters. |
| `dashboard/supervisor.py` | Starts the dashboard server and launches `update_eta.py` as a child, passing `CT_STATE_FILE` / `CT_CONTROL_FILE`. |
| `dashboard/server.py` | Serves the dashboard and the assistant. Read-only over a bridge snapshot. |
| `START_TOWER.bat`, `run_dashboard.bat`, `START_SHARED.bat` | Windows launchers. |

Only external dependency is **Playwright**. Everything else is standard
library — which is why the ML layer below is standard library too.

## Execution flow

```
main()
 └─ login_internal()
     └─ for table_page in 1..MAX_TABLE_PAGES
         ├─ click_centralized_shipments_tracking()
         ├─ select_shipments_view(SOURCE_VIEW)      ← BU view, falls back to the default table
         ├─ select_under_clearance_filter()          ← Status = Under Clearance
         ├─ collect_supported_shipments()            ← reads the table, routes by AWB prefix
         └─ for shipment in shipments
             ├─ get_provider_result()                ← carrier site
             │   ├─ DHL    → get_dhl_result()
             │   ├─ QATAR  → get_qatar_result()
             │   └─ PORTALS→ get_portal_result()     ← AFKL, Astral
             ├─ update_internal_shipment()
             │   ├─ COE view → update_one_view(ETA)
             │   └─ BU view  → update_one_view(ATA)
             │       ├─ click_manage_in_view()
             │       ├─ select_shipment_info_tab()
             │       ├─ fill_date_field()            ← candidate list → first_visible()
             │       └─ save_manage_page()
             ├─ save_result()                        ← CSV row
             └─ wait_between_shipments()
```

## Retry flow

Two independent layers.

- **`run_with_retry(...)`** — wraps an operation. `classify_failure(error)`
  maps the exception onto one of six outcomes; only `TIMEOUT`,
  `TEMPORARY WEBSITE ISSUE` and `UNEXPECTED PAGE STATE` are in `RETRYABLE`.
  Backoff is exponential from 2s.
- **Per-site attempt loops** — `get_portal_result` runs `config["attempts"]`
  passes; `open_portal` retries a navigation up to 3 times on transport errors
  (`ERR_HTTP2_PROTOCOL_ERROR` and friends); `update_one_view` reopens Manage
  once when the tab panel does not render.

The shipment loop itself never retries: a failure is recorded as SKIPPED,
FAILED or PARTIAL and the run moves on. PARTIAL exists because a COE ETA can
be saved before a BU ATA fails, and `error.actions` carries the completed work
out of the exception.

## Wait logic

There is a deliberate hierarchy, and almost no fixed sleeps left:

| Primitive | Used for |
| --- | --- |
| `wait_for_any(page, checks, max_ms)` | First of several readiness signals wins. |
| `wait_until_settled(page, ready_check, max_seconds)` | Poll one predicate. |
| `wait_for_table_change(page, before, max_ms)` | Table signature changed after a postback. |
| `wait_until_enabled(page, locator, max_ms)` | A disabled control becoming usable. |
| `click_postback(locator, description)` | Click plus the ASP.NET navigation it triggers. |

Budgets are constants: `HUB_TABLE_REFRESH_MAX_MS` 12000, `HUB_FORM_READY_MAX_MS`
15000, `HUB_SAVE_MAX_MS` 8000, `HUB_POLL_MS` 120, `DHL_READY_MAX_SECONDS` 75,
`PAGE_SETTLE_MAX_SECONDS` 6, `PROBE_TIMEOUT_MS` 800.

The surviving fixed sleeps are intentional: `wait_between_shipments()` (a
politeness delay), the navigation backoff in `open_portal`, and one 1500ms
grace period in `save_manage_page` used **only** when the save could not be
positively confirmed.

## Field detection

`fill_date_field(page, field_name, date_value)` builds an ordered candidate
list — seven for ETA, seven for ATA — and hands it to
`first_visible(candidates, 1500)`, which returns the first candidate that
becomes visible. If none does, `find_field_ignoring_visibility()` retries
without the `:visible` gate, then `describe_manage_fields()` dumps the page and
the operation raises.

Candidate order is hand-tuned and identical on every page. **This is the single
place where a wrong guess is most expensive**: seven candidates that all miss
cost seven timeouts before the fallback even starts.

## ETA/ATA safety logic

Three separate guards, all deterministic:

1. **Selector-level.** Every ETA candidate carries
   `:not([id*='ATA' i])` / `:not([name*='ATA' i])`, and vice versa. An ETA
   lookup cannot match an ATA input at the selector level.
2. **Label-level.** The xpath candidates anchor on the label *starting with*
   `ETA` / `ATA Date`, which keeps the six other date fields in the Clearing
   Agent block (Customs Pre Entry, Duty Paid, Customs Release, …) out.
3. **Routing-level.** `update_internal_shipment` sends ETA to `COE_VIEW` and
   ATA to `BU_VIEW`, and nothing else may change that mapping.

`find_field_ignoring_visibility()` repeats guard 1 rather than relaxing it.

## Verification logic

Present today:

- `submit_portal_awb` confirms the typed AWB actually landed in the field
  before pressing Track.
- `type_into` re-reads `input_value()` and escalates to a scripted write with
  real events when the framework did not accept the keystrokes.
- `write_date_value` re-reads the field after writing and raises if empty.
- `save_manage_page` waits for a positive signal that the save completed —
  the Save control disappearing, or the results table returning.

**Not present today:** nothing reloads the Manage page after a save to confirm
the value *persisted*. A confirmed postback is not a confirmed write. This is
the one real gap the map turned up, and it is filled by
`VERIFY_AFTER_SAVE` — off by default, because turning it on changes the
shape of an existing successful run.

## Logging points

- `write_log(message)` — the single sink. Feeds the run log file, stdout and
  the dashboard bridge. `redact_secrets()` runs on the way in.
- `shipment_log(reference, message, carrier, level)` — per-shipment lines.
- `log_operation_failure(...)` — one structured `FAILURE | …` line per failure.
- `note_suppressed(where, error)` — records exceptions that are deliberately
  swallowed, and `report_suppressed()` prints the tally at the end.
- `save_result(...)` — one CSV row per shipment.
- `tower.*` (`dashboard/bridge.py`) — the live dashboard state.

Every one of these is prose aimed at a human reader. None of it is
machine-readable, which is why telemetry is added alongside rather than by
reshaping these.

## Where ATLAS attaches

ATLAS — Adaptive Logistics Strategy Engine — is the strategy layer. Its name,
its version scheme and the vocabulary of things it is allowed to say about
itself all live in `ml/identity.py`; `update_eta.py` and `dashboard/bridge.py`
mirror the constants so they still work with the package absent, and
`test_atlas.py` checks the mirrors against the original.

Three seams, all of them places where the code already makes an ordered guess:

| Seam | Today | With ML |
| --- | --- | --- |
| `fill_date_field` candidate order | fixed hand-tuned order | reordered by observed success in this context |
| `write_date_value` method | always click+fill, then scripted write on failure | starts with the method that has been working |
| `wait_for_any` budgets | fixed constant per call site | budget from the observed distribution, clamped to the same constant as its ceiling |

In all three the ML output is an **ordering or a number**, never a decision
about which field to write or what to write into it. And in the shipped
default (`ML_MODE=shadow`) it is not even that: the recommendation is recorded
and discarded, and the deterministic order runs.

## What ATLAS is allowed to claim

`atlas_log(label, detail)` is the only way an ATLAS event reaches the log or
the dashboard, and every call site passes a label constant rather than a
string. The set of claims is closed, and each has one truth condition:

| Emitted from | Label | Condition |
| --- | --- | --- |
| `predictor._log_decision` | `Strategy selected` | the recommendation was **used** |
| `predictor._log_decision` | `Deterministic fallback` | declined, or shadow |
| `fill_date_field` | `Strategy failed` | ATLAS's pick was not the strategy that found the field |
| `fill_date_field` | `Fallback activated` | the visibility-free lookup rescued an ATLAS-ordered attempt |
| `verify_saved_date` | `Verification passed` | read-back confirmed, on an influenced write |
| `ml_episode_end` | `Action completed` | `verified is True` **and** `atlas_influenced` |
| `ml_episode_end` | `Action unverified` | saved, never read back |

`episode.atlas_influenced` is set in exactly one place — `ml_order`, after the
recommendation is confirmed `used` and the reordered list is proven complete.
Nothing else in the run sets it, and nothing reads it before that point.

## Episodes: how an attempt becomes evidence

`update_one_view` is the unit of learning. It opens an **episode** at the top
and closes it in a `finally`, so every interaction recorded in between —
the tab-panel wait, each locator tried, the write, the read-back — carries the
same `episode_id`.

```
update_one_view(page, shipment, view, field, date)
  ml_episode_begin(...)                     ← id issued
  ├── select_shipment_info_tab              → interaction  (tab_postback)
  ├── fill_date_field                       → interaction  (one per locator)
  │     └── write_date_value                → interaction  (click_fill | scripted_events)
  ├── save_manage_page
  ├── verify_saved_date                     → interaction  (verify_reload)
  └── ml_episode_end(outcome, verified)     ← VERIFIED | MISMATCH | UNVERIFIED | ERROR
```

The verdict lives on the episode, and `ml/episodes.py` joins the two back
together at training time. A locator is a success only if the date it led to
was still in the Hub when the automation looked again. An episode with no
verdict — `VERIFY_AFTER_SAVE` off, or a run that died before the read-back —
is **excluded** from training rather than counted as a failure.

`ml_episode_end` is in a `finally` and swallows everything: an episode that
cannot be recorded must never be able to take down a run that is otherwise
doing its job.

## What is written, and where it goes

| File | Written by | Read by |
| --- | --- | --- |
| `ml/data/telemetry.jsonl` | the automation, during a run | the trainer, the evaluator, the dashboard |
| `ml/data/telemetry.test.jsonl` | the test suite | nothing — it exists so the suite can exercise the real writer without contaminating the file above |
| `ml/models/challenger.json` | `python -m ml.trainer` | the evaluator |
| `ml/models/champion.json` | `python -m ml.trainer --promote`, only on a `BETTER` verdict | **the predictor** — this is the only file a run loads |
