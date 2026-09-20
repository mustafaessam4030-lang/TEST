# 11 — Selector Capture Runbook (the blocker, and how you clear it)

The automation refuses to run against SIS2 until the DOM contract is captured
from a real authenticated session. This is that procedure. It takes ~10 minutes
and needs one person who can sign in to SIS.

**It must run on a machine with a screen and your SIS access** — a laptop. It
cannot run in CI, in a container, or in an agent sandbox, because a human has to
complete sign-in (including MFA) and then point at the real elements.

## Prerequisites

```bash
git clone <repo> && cd maya
pip install playwright pyyaml pydantic pydantic-settings
python -m playwright install chromium
```

You also need two serial numbers:

* one that **exists** in SIS (to capture results + detail)
* one that **does not** (to capture the empty state — the only legitimate proof
  of `SERIAL_NOT_FOUND`); `ZZZ00000` usually works

## Run it

```bash
python scripts/capture/capture_selectors.py \
    --serial CAT0336LKBW00123 \
    --missing-serial ZZZ00000
```

A Chromium window opens on `https://sis2.cat.com/#/`.

1. **Sign in yourself.** Username, password, MFA — all of it, by hand. The tool
   never asks for, reads, or stores your credentials. Press Enter in the
   terminal when you are through.
2. **Pick each element.** For every target the terminal prints what it needs and
   the browser shows a yellow bar at the top. Hover — the element highlights —
   and click it. `Esc` skips a target.
3. **Follow the prompts** through: search page → a no-result search → a real
   search → open the first result → the detail page.
4. The tool then verifies everything it captured and runs **one real
   serial-number search** using only the captured selectors.

## What you get

```
config/sis_selectors.json                  the contract (only on a complete run)
artifacts/capture/live-<ts>/*.png          a screenshot per step and per pick
artifacts/capture/live-<ts>/*.html         DOM snapshots
artifacts/capture/live-<ts>/report.json    everything, including the step log
```

Each entry looks like:

```json
{
  "name": "search.input",
  "selector": "input[name=\"serialNumber\"]",
  "strategy": "name",
  "element_text": "",
  "url": "https://sis2.cat.com/#/search",
  "captured_at": "2026-09-20T21:38:42+00:00",
  "confidence": "verified",
  "match_count": 1,
  "alternates": [{"selector": "role=textbox[name=\"Serial number\"]", "strategy": "role+name"}]
}
```

`confidence: "verified"` means the tool re-queried that selector against the live
DOM and it resolved to exactly the element you clicked, and nothing else.

## What it will never do

* Invent, approximate, or "probably" a selector. If no candidate resolves
  uniquely to the element you picked, the entry is written as
  `{"status": "TODO_CAPTURE"}` and **the run stops** before verification.
* Overwrite a working contract with a partial run (the partial goes to the run
  folder instead).
* Work around MFA or a bot challenge. It detects both, stops, and screenshots.

## Selector preference order

`data-testid` → `id` → `name` → `aria-label` → `role + accessible name` →
stable CSS path → XPath (last resort). Framework-generated ids (`mat-input-0`,
`cdk-…`, `foo-10429`) are demoted automatically — they change on the next SIS
deploy. Every candidate is verified before it is kept; the runners-up are stored
as `alternates` so you have a fallback if the first one ages out.

## After the capture

1. Review `config/sis_selectors.json` — it is a normal file in a PR.
2. Bump `selector_version` if you want a name more meaningful than the run stamp.
3. Restart the API. It loads the file at startup, takes only
   `confidence: "verified"` entries, and logs how many it accepted.
4. Set `MAYA_ALLOW_LIVE_AUTOMATION=true`.

## Keeping it honest afterwards

Every real run now starts with a **pre-flight health check**
(`app/adapters/selector_health.py`) answering five questions: is the app
reachable, is the session authenticated, is the app shell there, is the serial
input visible, is the search button visible, is the results container present.
Any failure ends the run with `WEBSITE_CHANGED` and a screenshot — no fallback
clicking, no "try something similar". Run the same checks on a schedule
(`POST /v1/admin/selectors/validate`) so a SIS redesign pages engineering
instead of surfacing as a wrong answer to a customer.

## Proving the tool before you trust it

```bash
python scripts/capture/capture_selectors.py --self-test
```

Runs the identical engine against `scripts/capture/fixture.html`, a local
SIS-shaped page (hash routing, Angular-ish markup, generated ids, an empty
state). It captures 16 targets, verifies them, and performs a real search
against the fixture. Output goes to the run folder as
`sis_selectors.self-test.json` — never to `config/sis_selectors.json`, because
fixture selectors are not SIS selectors.

## Other modes

| Mode | What it does | When |
|---|---|---|
| `--self-test` | fixture end-to-end, no network | CI, and before you trust the tool |
| `--recon-only` | opens the base URL unauthenticated, inventories what is there, writes screenshots + DOM | checking reachability from a new network |
| `--auto-login` | authenticates from `MAYA_CAT_SIS_USERNAME`/`PASSWORD`, then inventories the authenticated DOM and **stops** | a shared runner where a human cannot click; it never picks selectors for you |

`--auto-login` exists for reachability and MFA diagnosis. It deliberately does
not capture selectors: knowing *which* element is the serial box is a judgement
only a person looking at SIS can make, and guessing it is the one thing this
whole design refuses to do.
