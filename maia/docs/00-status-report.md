# Maia Equipment Automation — Status Report

**Project:** Maia (Maia) equipment-data capability for the Mantrac Control Tower
**Source system:** Caterpillar SIS — https://sis2.cat.com/#/
**Branch:** `claude/maia-equipment-automation-yxs92l` · as of 2026-09-21
**Tests:** 128 backend · 39 front-end · 1 capture self-test — all passing

---

## 1. What was asked for, and where it stands

Give Maia a Caterpillar serial number; she returns the equipment data — from our
own store when we have it, from SIS when we don't — with the source, the
timestamp and an automation run id on every answer. Maia orchestrates; she never
drives a browser, holds a credential, or writes SQL.

| Capability | Status |
|---|---|
| Maia chat → tool gateway → Playwright worker → back to Maia | **Working, proven end to end** |
| Cache-first decision flow with a configurable freshness policy | **Working** |
| Normalization, validation, persistence, audit trail | **Working** |
| Live progress in the chat while the automation runs | **Working** |
| MFA / CAPTCHA detected, paused for a human, resumed in the same session | **Built; unexercised against SIS** |
| Automatic selector capture (no script, no clicking) | **Built and proven; unexercised against SIS** |
| Windows-local worker deployment | **Built; awaiting your run** |
| A real SIS search returning real Caterpillar data | **Not done — needs your credentials on your PC** |

The last row is the only outstanding item, and it is not a coding task. Details
in §5.

---

## 2. What was built

**Maia's front end** — her existing chat (v8) is untouched. The capability was
added as a separate layer and patched in at nine hook points by a script that
refuses to run if any anchor has moved.

* `frontend/maia-equipment.js` — the tool layer: five tools, cache-first logic,
  live run panel, equipment and failure cards, tool trace
* `frontend/build_v9.py` → `frontend/mantrac-support-v9.html` — the file you open
* With the capability switched off, v9 behaves exactly like v8

**Tool gateway (FastAPI)** — `services/automation/`

* `POST /v1/equipment/search`, `GET /v1/equipment/{serial}`, `/history`,
  `GET /v1/runs/{id}`, `POST /v1/runs/{id}/resume`, `/v1/sources`,
  `POST /v1/sources/{id}/capture`, `/healthz` `/readyz` `/metrics`
* Decision flow: validate → store → freshness → source → normalize → validate →
  persist, with idempotent de-duplication so concurrent questions share one run
* Error taxonomy of 15 codes with per-code retry policy and a per-source circuit
  breaker that degrades to store-only rather than failing the Control Tower

**SIS automation worker** — `app/adapters/cat_sis.py`

* A deterministic step machine: LEARN_LAYOUT → ACQUIRE_CONTEXT → ENSURE_SESSION
  → HEALTH_CHECK → SEARCH_SERIAL → EXTRACT_RAW → NORMALIZE → VALIDATE → PERSIST
* Two-step sign-in (username screen, then password), which is the shape Cat's
  Azure AD B2C flow actually uses, with an MFA check between the steps
* Session reuse via stored `storage_state`; one browser context per run

**Selector capture** — `scripts/capture/`

* Automatic mode: signs in, discovers candidates from the page's own labels and
  roles, then **proves each one by using it** — a serial that exists must produce
  a result, an unknown one must produce the empty state
* Manual mode: a click-to-pick overlay, for anything automation cannot prove
* Either way, unprovable targets are written `TODO_CAPTURE` and the run stops

**Data layer** — Snowflake DDL, views and governance; a Databricks Delta variant;
an in-memory store for development. Current state plus append-only history keyed
on a content hash, so history records changes rather than polls.

**Operations** — `make doctor` (readiness), `make e2e` (whole stack),
`make capture`, `make package`; PowerShell equivalents under `scripts/windows/`.

---

## 3. What is proven, and how

Nothing below is asserted from code review; each was run.

**The full chain, end to end.** From a source with **zero** selectors
configured, one question in Maia triggered: learn the layout (17 selectors
discovered and proven, including sign-in) → sign in → search → extract →
normalize → validate → persist → answer, with a 100 % quality score. No script
was run by a human.

**Cache-first.** Asking the same serial again answered in 2.38 s from the store
with a single GET and no browser.

**Failure honesty.** Every failure path was exercised: the error response
carries no `data` object at all, so Maia shows the error code and run id and no
equipment values.

**Reachability of the real site.** From this container a browser reaches
`https://sis2.cat.com/#/`, follows the redirect to the Cat B2C sign-in host
(`signin.cat.com`, policy `B2C_1A_P2_V1_SignIn_Prod`) and renders the sign-in
page. Screenshots in `artifacts/`.

**Against real SIS**, the same command stops at `LOGIN_FAILED — credentials are
missing at reference env://SIS`, with the worker's browser on the real sign-in
host. That is the correct stop, and the run record proves the worker got there.

> The completed chain above ran against a **local test fixture**, registered as a
> separate source labelled *"Local test fixture (NOT Caterpillar SIS)"*. It
> exercises the machinery with a real browser; it is not SIS data and is never
> presented as such. A guard prevents any non-Caterpillar origin from writing the
> SIS contract file.

**The equipment-details block, below the fold.** The SIS detail page renders
inside its own scrolling pane, with the equipment details and the parts groups
below its fold and rendered only once it is scrolled. Against a fixture shaped
the same way — a scrolling pane, filler above the fold, and a section that does
not exist until the pane is scrolled — the adapter scrolled the pane (not the
window) in 2 steps, waited for the render to settle, and read:

```
machine_serial_number = SN123456      machine_build_date = 2014-08-02
engine_serial_number  = FIX00588      engine_build_date  = 2014-06-30
Product - … groups    = 2 (5 rows)    columns = Part Number, Serial Number,
                                                Part Name, Install Ind.,
                                                Install Date, Description
quality score 0.909, no violations
```

Dates arrive as MM/DD/YYYY and are stored ISO-8601; the source contract declares
the order, and a value that settles its own order wins over it.

**The wrong-record guard.** Asked for one serial while the page showed another,
extraction stopped with `EXTRACTION_ERROR` naming both values; a detail page
with no details section stopped with `WEBSITE_CHANGED` rather than an empty
answer. Both were run, not reasoned about.

---

## 4. Defects found by running it, not by testing parts

These are the argument for insisting on real runs. Every one would have reached
production; two would have failed silently.

1. **Empty-state detected by presence, not visibility.** Every SPA keeps its "no
   results" element in the DOM and toggles it — so a *successful* search reported
   `SERIAL_NOT_FOUND`. A false negative served to a customer as fact is the worst
   failure this design can produce.
2. **Authentication inferred from the app shell.** A shell renders before auth
   resolves, so sign-in was skipped silently and surfaced later as a confusing
   `WEBSITE_CHANGED`. Now requires positive evidence of a session.
3. **URL fields read link text**, so `parts_manual_url` became "Parts manual" —
   correctly quarantined by the validator, which is the validator doing its job.
4. **Serial extraction** read "serial number SN123456" as `NUMBER`, and
   "for SN123456" as `123456`. Fixed and verified across ten phrasings including
   Arabic.
5. **A learned contract had no login selectors**, so the worker could not sign in
   after learning.
6. **Fixture output was written to the real SIS contract file** — the exact
   contamination that must never happen. Now blocked by origin, with a test.
7. Discovery treated a `<tr class="result-row">` as its own container; pre-flight
   failed on a results container that only exists after a search; a partial
   capture could overwrite a working contract; a busy port left a half-started
   stack.

---

## 5. What is not done, and why

**A real SIS search has not run.** It needs two inputs that exist only on your
side, and no amount of engineering here substitutes for them:

1. **Your SIS credentials on the machine running the worker.** They must never
   pass through the chat, the model, or this repository — that is a design rule,
   not a limitation. The worker reads them from `SIS_USERNAME` / `SIS_PASSWORD`
   (or a vault reference) in its own environment.
2. **A serial number that genuinely exists in SIS.** The first search uses it to
   prove the selectors: if it returns nothing, the results container cannot be
   proven and the capture stops rather than guessing.

Claude's container additionally has no display and no route for you to complete
MFA, which is why the worker belongs on your Windows PC. `make doctor` reports
this precisely on any machine.

**Also outstanding**

* Snowflake is implemented but has only been run against the in-memory store
* The async worker tier (queue + separate worker pods) is stubbed; the
  synchronous path with a 202 fallback is what runs today
* MFA pause/resume and the circuit breaker are built and unit-tested but have
  not met real SIS behaviour

---

## 6. Design decisions worth knowing

**Hallucination is prevented structurally, not by prompting.** No path from the
warehouse into the model's weights; the model cannot reach the source; `data`
never exists without `attribution`; error responses carry no `data` at all;
server-side validation quarantines bad records; every field carries provenance;
scraped text is data, never instruction. The prompt layer sits on top of all of
that, not in place of it.

**Never guess a selector.** Automatic capture does not weaken this: candidates
come from the page's own labels and are accepted only when their behaviour
proves them. Unprovable means `TODO_CAPTURE` and a stopped run.

**Security controls are boundaries, not obstacles.** MFA and CAPTCHA are
detected and handed to a human. Nothing attempts to defeat them, and I declined
to run with certificate checking disabled when a proxy blocked the browser —
typing SIS credentials into an unverified connection is worse than not running.

**Credentials never reach the model.** The agent process has no secret
environment variables and no tool that accepts one. When credentials were pasted
into this chat, I did not use them and advised rotating that password — it is
still in the transcript.

---

## 7. Running it

Full procedure: `docs/13-windows-deployment.md`. In short, on a Windows PC with
SIS access:

```powershell
.\scripts\windows\setup.ps1
$env:SIS_USERNAME = "your.sis.username"
$secure = Read-Host "SIS password" -AsSecureString
$env:SIS_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
.\scripts\windows\start-maia.ps1
```

Open http://127.0.0.1:5173/maia.html and ask:
*"Maia, find equipment data for serial number &lt;a serial that exists&gt;"*

The first search learns the page layout automatically; later searches skip
straight to the lookup.

---

## 8. Document map

| Doc | Covers |
|---|---|
| `01-architecture.md` | layered and physical architecture, NFR targets |
| `02-sequence-flows.md` | seven end-to-end flows including failures |
| `03-data-model.md` | tables, provenance, the canonical JSON contract |
| `04-api-and-tools.md` | API surface, Maia's tools, envelopes, authz |
| `05-automation-sis2.md` | SIS2 specifics, step machine, selector contract |
| `06-reliability.md` | error taxonomy, retries, circuit breaker |
| `07-observability-security-freshness.md` | audit, metrics, secrets, freshness |
| `08-anti-hallucination-and-extensibility.md` | the seven controls; adding sources |
| `09-maia-integration.md` | exactly what was added to Maia |
| `10-example-conversation.md` | transcripts including failure cases |
| `11-selector-capture-runbook.md` | selector capture, manual and automatic |
| `12-end-to-end-runbook.md` | the full chain, live progress, MFA resume |
| `13-windows-deployment.md` | **running it for real on your PC** |
| `14-local-json-store.md` | the temporary local JSON store, and the repository seam that makes it temporary |

---

## 9. Change log

| Commit | What it delivered |
|---|---|
| `22056a0` | The platform: gateway, decision flow, adapters, data layer, Maia's tool layer |
| `3c583af` | Interactive selector capture and the pre-flight health gate |
| `c7b20a3` | Credentials through the secret store; clear TLS failures |
| `42fcebd` `1e8aa2d` `a0aacc5` | Contract-file guards, package target |
| `9e11af3` | Auth-flow probe for the B2C sign-in |
| `019f21b` | The end-to-end path wired: live progress, MFA pause/resume |
| `47d71d9` | Chain proven end to end; three real bugs fixed |
| `7f02d5b` | Credential reference, readiness doctor, onboarding |
| `0625e5f` | Windows deployment and fully automatic capture |
| `6f94899` | Self-provisioning: the first search learns the contract |
| *(this change)* | A temporary local JSON store behind the repository seam: JSON + TXT + screenshots per lookup, store-first reads, and PERSISTENCE_FAILED when a save fails |
| `c39fc05` | A run explainer; a partial capture can no longer occupy the contract path |
| *(prev)* | The equipment-details block: scroll the SIS content pane, read the four machine/engine fields first, collect every `Product - …` group, cross-check the serial |
