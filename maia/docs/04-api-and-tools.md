# 04 — API Design & Maia Tool Contracts

## 4.1 HTTP API (FastAPI — `services/automation`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/equipment/search` | The decision flow: cache → freshness → source → normalize → validate → save |
| `GET`  | `/v1/equipment/{serial}` | Store-only read (never launches a browser) |
| `GET`  | `/v1/equipment/{serial}/history` | Change history (audit) |
| `POST` | `/v1/equipment/save` | Explicit upsert (corrections, backfills, other pipelines) |
| `GET`  | `/v1/runs/{run_id}` | Run status + steps |
| `GET`  | `/v1/sources` | Registered sources, TTLs, circuit-breaker state |
| `POST` | `/v1/admin/selectors/validate` | Selector-contract self-test against the live site |
| `GET`  | `/healthz` `/readyz` `/metrics` | Liveness, readiness, Prometheus |

### `POST /v1/equipment/search`

```jsonc
// request
{
  "serial_number": "SN123456",
  "source": "cat_sis",          // optional; default = registry precedence
  "mode": "auto",               // auto | cache_only | force_refresh
  "wait": true,                 // false → returns 202 + run id immediately
  "timeout_ms": 30000,
  "reason": "user_request",     // user_request | stale_refresh | backfill
  "requested_by": "user:me@mantracgroup.com",
  "idempotency_key": "optional-client-supplied"
}
```

```jsonc
// 200 — served from store
{ "status":"success", "serial_number":"SN123456", "source":"internal_store",
  "origin_source":"cat_sis", "cache":{"hit":true,"freshness":"FRESH","age_days":3},
  "data":{ /* normalized schema */ },
  "retrieved_at":"2026-09-17T09:12:44Z", "automation_run_id":"run_01JA…" }

// 200 — freshly retrieved
{ "status":"success", "source":"cat_sis", "cache":{"hit":false},
  "data":{…}, "retrieved_at":"2026-09-20T18:04:11Z",
  "automation_run_id":"run_01JB…", "execution_time_ms":18240 }

// 202 — running in background
{ "status":"in_progress", "automation_run_id":"run_01JB…", "poll_after_ms":4000 }

// 404 — authoritative "not there"
{ "status":"error", "error_code":"SERIAL_NOT_FOUND", "retryable":false,
  "message":"Serial not found in Caterpillar SIS", "automation_run_id":"run_01JB…" }

// 502/503/504 — source failure, with optional stale fallback
{ "status":"error", "error_code":"TIMEOUT", "retryable":true,
  "automation_run_id":"run_01JB…",
  "fallback":{ "available":true, "freshness":"STALE", "age_days":210, "data":{…} } }
```

**Invariants**

* Every response carries `automation_run_id` (even cache hits — the id of the
  run that originally produced the row) and `retrieved_at`.
* There is **no** response shape that returns equipment fields without
  `source` + `retrieved_at`. Maia literally cannot render an unattributed value.
* `error` responses never carry a `data` object. A failure cannot be
  mistaken for a result.
* Idempotency: `sha256(source|serial|mode)` collapses concurrent duplicates onto
  one run (see `docs/02` §2.7).

## 4.2 Maia's five tools

Definitions live in `schemas/maia_tools.json` (Claude `tools` array, `strict: true`,
`additionalProperties: false`) and are served to the model unchanged on every
turn so the prompt prefix stays cacheable.

| Tool | Side effects | Notes |
|---|---|---|
| `get_equipment_from_database` | none (read) | Always tried first. Returns `found`, `freshness`, `age_days`, `data`. |
| `search_equipment_in_sis` | launches automation | Requires `reason`. Rate-limited per user. Returns run id always. |
| `save_equipment_data` | write | Restricted to correction flows; requires `field_provenance`. |
| `get_equipment_history` | none | For "did anything change?" questions. |
| `get_automation_run_status` | none | For polling a 202 and for "why did it fail?". |

**What the tools deliberately do not expose:** no `url` parameter, no
`selector`, no `sql`, no `credentials`, no `click`/`navigate` primitive. The
model's entire reach into the browser is one string: a serial number, which the
server re-validates against `^[A-Z0-9]{3,17}$` before anything happens.

## 4.3 Tool result envelope

Every tool returns the same envelope so failure handling is uniform:

```jsonc
{ "ok": true|false,
  "error_code": "SERIAL_NOT_FOUND|TIMEOUT|…",     // only when ok=false
  "user_message_hint": "short, factual, no invented detail",
  "attribution": { "source":"cat_sis", "source_label":"Caterpillar SIS",
                   "retrieved_at":"…", "automation_run_id":"…", "freshness":"FRESH" },
  "data": { … } }                                  // only when ok=true
```

`attribution` is mandatory whenever `data` is present. Maia's system prompt
binds rendering to it: no attribution → no equipment claim.

## 4.4 AuthN/Z

* Maia → API: OIDC client-credentials token, audience `maia-automation`, scope
  `equipment.read` / `equipment.search` / `equipment.write`.
* End-user identity is forwarded as `X-Maia-Actor` and recorded on the run; the
  API enforces per-actor quotas (default 20 SIS lookups/hour) so a prompt
  injection cannot turn Maia into a scraper.
* `save_equipment_data` requires scope `equipment.write`, which the chat-facing
  agent identity does **not** hold by default.
