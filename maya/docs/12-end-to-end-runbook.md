# 12 — End-to-End Runbook (Maya → gateway → Playwright → SIS → Maya)

One command starts everything. You only ever talk to Maya.

```bash
make e2e                      # worker browser visible — you can watch it drive SIS
make e2e E2E_ARGS=--headless  # worker hidden
```

Then:

1. Open **http://127.0.0.1:5173/maya.html**
2. Click the chat bubble (bottom right)
3. Type: `Maya, find equipment data for serial number SN123456`

## What happens, and where each piece lives

| Stage | Component | File |
|---|---|---|
| Chat UI | Maya v9 | `frontend/mantrac-support-v9.html` |
| Serial extraction + intent | Maya's NLU | same file (`extractEntities`, `classifyIntent`) |
| Tool layer | `EQUIP` | `frontend/maya-equipment.js` |
| Tool gateway | FastAPI | `services/automation/app/api/routes_equipment.py` |
| Decision flow | EquipmentService | `app/services/equipment_service.py` |
| Browser automation | CatSisAdapter | `app/adapters/cat_sis.py` |
| Real browser | Playwright worker | `app/adapters/browser.py` |

Maya holds **no** Playwright code, **no** selectors, **no** credentials, and no way
to issue a browser command. Her whole reach into the source is one string — the
serial — which the gateway re-validates server-side.

## Self-provisioning on first use

A source with no verified contract is learned during the first search:
`LEARN_LAYOUT` runs the automatic capture, which signs in and proves each
selector by using it, loads the result into the live registry, and the run
continues into the search. It is attempted once per source per process; a
failure to learn is `WEBSITE_CHANGED` with the unresolved targets named, never
a guess. Turn it off with `MAYA_AUTO_CAPTURE=false`, or trigger it explicitly:

```
POST /v1/sources/cat_sis/capture?serial=<a serial that exists>
```

## The live panel

Because the gateway is called with `wait:false`, it returns an `automation_run_id`
immediately and Maya follows the run, printing each step as it lands:

```
Working on SN123456                         Run run_01M30FQJ118SRV4YSR487ZAX01
• Checking internal data…
• Not in internal data — querying Caterpillar SIS…
• SIS automation running… Run run_01M30FQJ…
✓ Starting a browser session                1062 ms
✓ Signing in to Caterpillar SIS             …
✓ Checking the SIS page contract            …
✓ Searching the serial number in SIS        …
✓ Reading the equipment record              …
```

Every line comes from `GET /v1/runs/{id}` — the same audit record stored in the
warehouse. Nothing in the panel is decorative.

## When sign-in needs a person

MFA and CAPTCHA are never bypassed. In a headed run the worker **pauses with the
browser session open**, the run goes to `AWAITING_HUMAN`, and the panel shows:

```
⏸ Waiting for human verification at SIS (MFA)      [ I have completed it — continue ]
```

The operator completes the verification in the worker's browser window and clicks
the button, which calls `POST /v1/runs/{id}/resume`. The automation continues in
the same session. Headless runs cannot be resumed by anyone, so they fail
immediately with `MFA_REQUIRED` rather than hanging.

## Configuration

| Variable | Default in `make e2e` | Meaning |
|---|---|---|
| `MAYA_ALLOW_LIVE_AUTOMATION` | `true` | master switch for touching the real source |
| `MAYA_HEADLESS` | `false` | worker browser visible |
| `MAYA_SIS_SECRET_REF` | `env://MAYA_CAT_SIS` | where credentials live (`vault://…` in prod) |
| `MAYA_CHROMIUM_PATH` | unset | explicit Chromium binary |
| `MAYA_REPOSITORY` | `memory` | `snowflake` in production |
| `MAYA_HUMAN_WAIT_TIMEOUT_S` | `600` | how long a paused run waits for a person |

Credentials are read by the **worker process only**, from the secret store, at the
moment of use. The browser, the chat, and the model never see them.

## Failure codes you will actually see

`LOGIN_FAILED` · `MFA_REQUIRED` · `CAPTCHA_DETECTED` · `SESSION_EXPIRED` ·
`WEBSITE_CHANGED` · `SERIAL_NOT_FOUND` · `SEARCH_FAILED` · `TIMEOUT` ·
`EXTRACTION_ERROR` · `INVALID_DATA` · `DATABASE_ERROR` · `CIRCUIT_OPEN`

On any of them Maya states the failure and the run id and shows **no equipment
values** — the error response carries no `data` object, so there is nothing to
paraphrase.

## Proving the chain without SIS credentials

`make e2e E2E_ARGS="--source local_fixture"` points the same machinery at a local
page (`scripts/capture/fixture.html`) instead of SIS. Everything else is real: a
real Chromium, a real sign-in form, real DOM extraction, real normalization,
validation, persistence and rendering. It exists to prove the plumbing when SIS
itself is out of reach.

It is **not** SIS and can never be mistaken for it: the source is registered as
`local_fixture`, label *"Local test fixture (NOT Caterpillar SIS)"*, precedence
900 so `auto` never picks it, disabled unless `MAYA_ENABLE_FIXTURE_SOURCE=true`,
and the label is carried into the answer, the card and every audit row.

Verified run, 2026-09-20:

```
✓ Starting a browser session       703 ms
✓ Signing in                      8144 ms
✓ Checking the page contract        52 ms
✓ Searching the serial number      395 ms
✓ Reading the equipment record      39 ms
✓ Normalizing · Validating · Saving
→ Model 336 · HYDRAULIC_EXCAVATOR · 2019-07 · C9.3B · 36200 kg · 225 kW
  Operation manual: not published   Quality 100%
  Source: Local test fixture (NOT Caterpillar SIS) · Run run_01M30H7KXRKTX9NA61ZEM4CQ58
```

Asking again answered in **2.38 s** from the store with a single
`GET /v1/equipment/SN123456` and no browser at all — the cache-first rule holding.

## Verified run (2026-09-20, this container)

Real, unmocked, with `MAYA_ALLOW_LIVE_AUTOMATION=true`:

```
GET  /v1/equipment/SN123456                    → 404 SERIAL_NOT_FOUND (not in store)
POST /v1/equipment/search                      → 202 run_01M30FQJ118SRV4YSR487ZAX01
GET  /v1/runs/run_01M30FQJ118SRV4YSR487ZAX01   → live steps
  1. ACQUIRE_CONTEXT  OK       1062 ms
  2. ENSURE_SESSION   FAILED   2024 ms   https://signin.cat.com/cwslogin.onmicrosoft.com/…
status FAILED · LOGIN_FAILED
"Credentials are missing or incomplete at reference 'env://MAYA_CAT_SIS'."
```

The worker's browser really reached SIS2 and really followed the redirect to the
Cat B2C sign-in host. It stopped where it should: no credentials are configured
in this environment. Supply them through the secret store and the same run
continues into sign-in, then the selector contract (`docs/11`).
