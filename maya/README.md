# Maya — Equipment Data Automation Platform

Maya (Maia) already exists as the Mantrac support assistant. This adds one
capability to her: **give her a Caterpillar serial number, get real equipment
data back — from our own data store when we have it, from Caterpillar SIS
(https://sis2.cat.com/#/) when we don't — with the source, the timestamp and the
automation run id attached to every answer.**

The rule the whole design serves: *Maya orchestrates, she does not operate.* She
calls five deterministic tools. She never drives a browser, holds a credential,
writes SQL, decides a retry, or states a value that did not come back from a tool.

```
USER → MAYA (LLM orchestrator) → TOOL GATEWAY (FastAPI)
                                      ├── internal store (Snowflake / Databricks)
                                      └── source adapters ── cat_sis (Playwright → SIS2)
                                                          └── (cat_pcc, dealer_erp, telematics …)
```

## Layout

```
maya/
├── docs/                     architecture, flows, reliability, security, anti-hallucination
├── frontend/
│   ├── mantrac-support-v8.html   your original Maia, untouched
│   ├── maya-equipment.js         the added tool layer
│   ├── build_v9.py               additive patcher (fails loudly if an anchor moves)
│   ├── mantrac-support-v9.html   built result: v8 + equipment capability
│   └── test_equipment.mjs        23 behavioural checks
├── services/automation/      FastAPI + Playwright + repositories  (38 tests)
│   └── app/{api,core,domain,adapters,repositories,services,models}
├── agent/                    system prompt, tool definitions, reference agent loop
├── schemas/                  normalized equipment JSON Schema + Claude tool schemas
├── sql/                      Snowflake DDL/views/governance, Databricks Delta variant
├── config/                   freshness policy, per-source contracts, .env.example
└── scripts/capture_selectors.py  capture SIS2 selectors from a real session
```

## Quick start

```bash
make install                         # deps + chromium
make test                            # 38 python tests + 23 frontend checks
make run                             # API on :8080  (live automation OFF by default)
make smoke
open frontend/mantrac-support-v9.html
```

Out of the box the API runs on an in-memory store with live automation disabled,
so nothing touches a partner site. Going live is four deliberate steps — see
`docs/09-maya-integration.md`.

## The decision flow

```
serial → validate → internal store → freshness policy
   FRESH        → answer from the store (no browser)          ~200 ms
   MISSING      → SIS automation → normalize → validate → save ~15-40 s
   STALE        → present with its age + offer a refresh
   NOT_FOUND    → negative-cached 7 days, stated plainly
   source fails → explain the failure + run id; offer stale copy, labelled
```

## Why it does not hallucinate

Seven structural controls, not a prompt asking nicely: no path from the
warehouse into the weights; the model cannot reach the source; attribution is
mandatory in the data shape; error responses carry no `data` object at all;
server-side schema + business validation with quarantine; per-field provenance
and a change hash; scraped text is data, never instruction. Details and the
nightly verification job: `docs/08-anti-hallucination-and-extensibility.md`.

## Reading order

| Doc | Covers |
|---|---|
| `01-architecture.md` | layered + physical architecture, NFR targets |
| `02-sequence-flows.md` | seven end-to-end flows incl. failures and concurrency |
| `03-data-model.md` | tables, keys, provenance, the canonical JSON contract |
| `04-api-and-tools.md` | FastAPI surface, Maya's five tools, envelopes, authz |
| `05-automation-sis2.md` | SIS2 SPA specifics, step machine, session reuse, selector contract |
| `06-reliability.md` | error taxonomy, retry policy, circuit breaker, degradation ladder |
| `07-observability-security-freshness.md` | run/step audit, metrics, alerts, secrets, freshness |
| `08-anti-hallucination-and-extensibility.md` | the seven controls; adding sources |
| `09-maya-integration.md` | exactly what was added to Maia and how to enable it |
| `10-example-conversation.md` | real transcripts incl. stale, not-found, timeout, CAPTCHA |

## Status

Working: the API and its decision flow, freshness engine, normalizer, validator,
repositories (memory + Snowflake), run/step audit, error taxonomy, retry and
circuit breaker, the adapter framework, Maya's tool layer and UI, all tests.

Needs a human before production: **SIS2 selectors are `TODO_CAPTURE`.** They must
be captured from a real authenticated session (`scripts/capture_selectors.py`)
and reviewed. The adapter refuses placeholders and raises `WEBSITE_CHANGED`
rather than guessing — a guessed selector is how a scraper starts returning
confident nonsense.

Also deliberately out of scope until the queue is wired: the async worker tier
(`maya-worker`, stubbed in compose). The sync path with a 202 fallback works today.

Access to SIS must stay within the dealer's licensed entitlement. The platform
honours rate limits, reuses sessions, and stops cleanly on any bot challenge
rather than attempting to defeat it.
