# 01 — High-Level Architecture

Maia is an LLM **orchestrator**, not an operator. It never touches a browser, a
DOM node, a credential, or a SQL cursor. It calls a small, fixed set of
deterministic tools and renders their structured output.

## 1.1 Layered view

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ CHANNEL LAYER            Teams / Web Control Tower / WhatsApp / API           │
└───────────────┬──────────────────────────────────────────────────────────────┘
                │ user utterance (AR/EN)  +  channel identity
┌───────────────▼──────────────────────────────────────────────────────────────┐
│ MAIA AGENT LAYER (stateless)                                                  │
│  • Claude (claude-opus-5) + system prompt + strict tool schemas               │
│  • Intent + serial-number extraction                                          │
│  • Tool selection / result presentation / failure explanation                 │
│  • NEVER: selectors, credentials, SQL, retries, browser state                 │
└───────────────┬──────────────────────────────────────────────────────────────┘
                │ JSON tool calls (HTTPS, mTLS, OIDC service token)
┌───────────────▼──────────────────────────────────────────────────────────────┐
│ TOOL GATEWAY / ORCHESTRATION API  (FastAPI — services/automation)             │
│  • AuthN/Z, tenant + user scoping, rate limit, idempotency keys               │
│  • EquipmentService: the deterministic decision flow (cache → source → save)  │
│  • Freshness policy engine (config-driven, NOT model-driven)                  │
│  • Run recorder (automation_run_id) + structured audit log                    │
└───────┬────────────────────────────────────────────┬─────────────────────────┘
        │                                            │
┌───────▼───────────────────────┐        ┌───────────▼──────────────────────────┐
│ DATA LAYER                    │        │ ACQUISITION LAYER                     │
│  Snowflake / Databricks       │        │  Source Adapter Registry              │
│   • EQUIPMENT_DATA (current)  │        │   ├── cat_sis   (SIS2 — Playwright)   │
│   • EQUIPMENT_DATA_HISTORY    │        │   ├── cat_pcc   (future, API)         │
│   • AUTOMATION_RUNS           │        │   └── dealer_erp (future, JDBC)       │
│   • AUTOMATION_RUN_STEPS      │        │  Browser pool + session vault         │
│   • SOURCE_REGISTRY           │        │  Deterministic step machine           │
└───────────────────────────────┘        └───────────┬──────────────────────────┘
                                                     │ headless Chromium
                                         ┌───────────▼──────────────────────────┐
                                         │ https://sis2.cat.com/#/  (Angular SPA)│
                                         │ auth: CWS / Cat Login (OIDC redirect) │
                                         └───────────────────────────────────────┘
```

## 1.2 Physical deployment

```
                      ┌───────────────────────────┐
  Teams / Web ───────►│  API Gateway (WAF, mTLS)  │
                      └────────────┬──────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
   ┌──────▼──────┐        ┌────────▼────────┐      ┌────────▼─────────┐
   │ maia-agent  │        │ maia-api        │      │ maia-admin       │
   │ (stateless) │        │ (FastAPI, N pods)│     │ (selectors, cfg) │
   └──────┬──────┘        └────────┬────────┘      └──────────────────┘
          │                        │  enqueue (Redis Streams / SQS)
          │                ┌───────▼────────────────────────────┐
          │                │  maia-worker (Celery/arq)          │
          │                │   ├─ browser pool (Playwright)     │
          │                │   ├─ 1 context per run, isolated   │
          │                │   └─ concurrency = N_CTX per pod   │
          │                └───────┬────────────────────────────┘
          │                        │
   ┌──────▼────────────────────────▼───────────────────────────────┐
   │ Snowflake (MAIA_PROD)   Vault/KeyVault   OTel Collector       │
   │                          (SIS creds)      → Datadog/Grafana   │
   └───────────────────────────────────────────────────────────────┘
```

**Why a worker tier:** browser automation is slow (5–40 s), memory heavy
(~250 MB/context) and failure-prone. Keeping it off the request path lets the
API stay <100 ms for the cache-hit path (which will be >90 % of traffic at
steady state) and lets us scale browsers independently of the API.

Synchronous mode (`wait=true`, bounded by `SIS_SYNC_TIMEOUT_MS`) is kept for the
chat experience; on timeout the API returns `202 + automation_run_id` and Maia
tells the user it is still working and can poll `get_automation_run_status`.

## 1.3 Component responsibilities

| Component | Owns | Must never |
|---|---|---|
| `maia-agent` | NL understanding, serial extraction, tool choice, phrasing | Invent field values, retry logic, credentials |
| `maia-api` | Decision flow, freshness, validation, persistence, audit | Free-form navigation |
| Source adapter | Login, navigate, extract **raw** payload, classify errors | Normalization, DB writes |
| Normalizer | raw → canonical JSON schema, per-field provenance | Filling gaps with guesses |
| Validator | schema + business rules, quality score | Silently coercing bad data |
| Repository | MERGE/UPSERT, history, run records | Business decisions |
| Freshness engine | cache vs refresh decision | Being overridden by the LLM |

## 1.4 Non-functional targets

| Dimension | Target |
|---|---|
| Cache-hit latency (p95) | ≤ 300 ms |
| SIS cold lookup (p95) | ≤ 35 s |
| Throughput | 5 000 serials/day sustained, 300/h burst |
| Availability (API) | 99.5 % |
| Data provenance coverage | 100 % of stored fields carry `source` + `retrieved_at` |
| Fabricated field rate | 0 (enforced structurally, see `docs/10`) |
| Audit retention | 400 days of run records, 7 years of raw snapshots (object store) |
