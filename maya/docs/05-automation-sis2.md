# 05 — Playwright Automation Architecture (target: https://sis2.cat.com/#/)

## 5.1 What SIS2 is, mechanically

SIS2 is a single-page Angular application served from `https://sis2.cat.com/`
with **hash routing** (`/#/…`), sitting behind Caterpillar's corporate identity
provider (CWS / Cat Login, an OIDC redirect flow). Three consequences drive the
design:

1. **`page.goto()` is not "page loaded".** The shell returns instantly; the real
   content arrives over XHR afterwards. Every step waits on an *application*
   signal (a rendered result region, or the network response itself), never on
   `networkidle` and never on `sleep()`.
2. **Hash navigation does not fire a document load.** Moving between routes is a
   client-side state change; the adapter drives it through the app's own UI or
   `page.evaluate(() => location.hash = …)` and then waits for the route's
   ready-marker.
3. **Auth is a redirect dance with a session cookie.** We log in once per session
   slot, persist `storage_state`, and reuse it. Login is the expensive,
   rate-sensitive operation — reusing sessions is what makes 5 000 lookups/day
   viable.

**We do not fabricate selectors.** No DOM path in this repo is guessed from
memory. `config/sources/cat_sis.yaml` ships with the *structure* of the selector
contract and `TODO_CAPTURE` placeholders; `scripts/capture/capture_selectors.py` (see `docs/11`) opens an authenticated
session, lets an engineer click each element, verifies every candidate against
the live DOM, and writes `config/sis_selectors.json`.
`POST /v1/admin/selectors/validate` then re-asserts that contract on a schedule,
so a SIS2 redesign is caught by a CI alarm rather than by a customer getting a
wrong answer. This is also why the error taxonomy separates `WEBSITE_CHANGED`
from `SERIAL_NOT_FOUND` (see §5.5).

## 5.2 Layering

```
EquipmentService  (source-agnostic orchestration)
        │  adapter = registry.get("cat_sis")
        ▼
SourceAdapter (Protocol)
  ├ capabilities()        → {supports_serial_search, needs_login, …}
  ├ ensure_session(ctx)   → step ENSURE_SESSION
  ├ search(ctx, serial)   → step SEARCH_SERIAL  → SearchOutcome(found|not_found)
  └ extract(ctx)          → step EXTRACT_RAW    → RawPayload(dict + artifacts)
        ▼
CatSisAdapter (Playwright)  — the ONLY place that knows about sis2.cat.com
        ▼
BrowserPool → BrowserContext (1 per run, isolated) → Page
SessionVault → storage_state per (source, account slot), encrypted at rest
```

The adapter returns **raw** data. It never normalizes, never writes to the
database, never decides about freshness. That separation is what lets a second
source (an API, not a browser) implement the same three methods.

## 5.3 The deterministic step machine

A run is a fixed, declared sequence. The model cannot add, reorder, or skip a
step; there is no "click whatever looks right" affordance anywhere in the code.

```python
STEPS = ["ACQUIRE_CONTEXT", "ENSURE_SESSION", "HEALTH_CHECK", "SEARCH_SERIAL",
         "EXTRACT_RAW", "NORMALIZE", "VALIDATE", "PERSIST"]
```

`HEALTH_CHECK` is the selector-contract gate (`app/adapters/selector_health.py`):
five questions — app reachable, session authenticated, app shell present, serial
input visible, search button visible, results container attached. Any failure is
`WEBSITE_CHANGED` with a screenshot, and the run stops. There is no fallback
clicking.

Each step is wrapped by `run_step()`, which records start/end, duration, outcome,
the current URL, and on failure an artifact bundle (screenshot + trimmed HTML +
console log + HAR slice), then maps the exception to an `ErrorCode`. Artifacts go
to object storage under `runs/{run_id}/{step}/…` with a 90-day lifecycle; the run
row keeps only the pointer.

## 5.4 Browser and session management

| Concern | Decision |
|---|---|
| Browser | Chromium, headless, `--disable-dev-shm-usage`, fixed viewport |
| Isolation | One `BrowserContext` per run; destroyed after. No cross-tenant bleed. |
| Reuse | The *browser process* is pooled (expensive); contexts are cheap and disposable |
| Session | `storage_state` JSON per account slot in the session vault; TTL-checked before use, revalidated by a cheap authenticated ping |
| Concurrency | `MAX_CONTEXTS_PER_POD` (default 4) + a global per-source semaphore; SIS2 is a partner system, not a load test target |
| Politeness | Min inter-request delay per source, jitter, and a nightly cap — all in `config/sources/cat_sis.yaml` |
| Credentials | Fetched from Vault at context creation, injected via `page.fill`, never logged, never in env dumps, never in a prompt |

## 5.5 Distinguishing "not found" from "broken"

The most dangerous failure mode in scraping is reading a changed page as an
empty result. The adapter requires **positive evidence** for every terminal
state:

* `SERIAL_NOT_FOUND` requires the app's explicit empty-state marker
  (`selectors.search.no_results_marker`) **and** an otherwise healthy results
  region. A missing results container is `WEBSITE_CHANGED`, never "not found".
* A successful extraction requires the `required_fields` set declared in the
  source config to be present. Fewer than that → `EXTRACTION_ERROR`, and the run
  fails loudly instead of persisting a half-record.
* Redirect to the login host during any step → `SESSION_EXPIRED` (self-healing,
  one relogin + one replay).
* Challenge/captcha markers → `CAPTCHA_DETECTED`, non-retryable, circuit opens,
  human alerted. We do not attempt to defeat it.

## 5.6 Selector contract (excerpt of `config/sources/cat_sis.yaml`)

```yaml
source_id: cat_sis
label: Caterpillar SIS
base_url: https://sis2.cat.com/#/
selector_version: v1-unconfirmed        # bumped when engineers confirm captures
auth:
  kind: oidc_redirect
  login_host_markers: ["signin.cat.com", "login.cat.com", "cws"]
  secret_ref: vault://kv/maya/cat_sis   # {username,password}
routes:
  search: "#/search"
ready_markers:                           # app-level "I am usable" signals
  app_shell: "TODO_CAPTURE"
  search_page: "TODO_CAPTURE"
selectors:
  search: { input: TODO_CAPTURE, submit: TODO_CAPTURE,
            results: TODO_CAPTURE, first_result: TODO_CAPTURE,
            no_results_marker: TODO_CAPTURE }
  detail: { model: TODO_CAPTURE, type: TODO_CAPTURE, build_date: TODO_CAPTURE,
            engine: TODO_CAPTURE, spec_rows: TODO_CAPTURE }
extraction:
  required_fields: [equipment_model]
  strategy: dom_then_xhr        # prefer intercepted JSON; fall back to DOM text
```

`strategy: dom_then_xhr` matters: where SIS2 fetches JSON over XHR, the adapter
captures the **response body** via `page.route`/`page.on("response")` and treats
it as the primary raw payload. JSON from the app's own API is stabler and richer
than scraped text; DOM reading is the fallback, not the default.
