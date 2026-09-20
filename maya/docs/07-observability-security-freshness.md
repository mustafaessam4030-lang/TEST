# 07 — Observability, Security, Freshness

## 7.1 Observability

**Run record (`AUTOMATION_RUNS`)** — one per attempt: `automation_run_id` (ULID,
sortable), `serial_number`, `source`, `trigger` (user/schedule/backfill),
`requested_by`, `started_at`, `completed_at`, `execution_time_ms`, `status`,
`error_code`, `error_message`, `retry_count`, `steps_executed` (VARIANT),
`extracted_field_count`, `quality_score`, `selector_version`, `artifact_uri`,
`trace_id`.

**Step record (`AUTOMATION_RUN_STEPS`)** — `run_id`, `seq`, `step`, `status`,
`duration_ms`, `url`, `error_code`, `artifact_uri`. This is what turns "it
failed" into "it failed at SEARCH_SERIAL, 12.4 s in, at `/#/search`, selector
`search.results` never appeared, here is the screenshot".

**Logs** — structured JSON only, one event per line, every line carrying
`trace_id`, `run_id`, `serial_number`, `source`, `step`, `actor`. A redaction
filter drops known secret keys and anything matching credential patterns before
the log leaves the process.

**Traces** — OpenTelemetry. Span tree: `maya.turn` → `tool.search_equipment_in_sis`
→ `api.equipment.search` → `repo.lookup` / `automation.run` → one span per step.
The browser steps carry the URL and selector id as attributes.

**Metrics (Prometheus)**

```
maya_tool_calls_total{tool,outcome}
equipment_search_total{source,result}                  # cache_hit|fetched|not_found|error
equipment_search_duration_seconds{source,path}         # histogram, path=cache|live
automation_runs_total{source,status,error_code}
automation_step_duration_seconds{source,step}
automation_retries_total{source,error_code}
source_circuit_state{source}                           # 0 closed 1 half 2 open
equipment_quality_score{source}                        # histogram
selector_contract_failures_total{source,selector_id}
cache_hit_ratio{source}
sis_session_reuse_ratio
```

**Alerts that matter:** `selector_contract_failures_total > 0` (page changed —
page engineering, not ops), `CAPTCHA_DETECTED` (any), `LOGIN_FAILED` (any),
circuit open > 10 min, cache hit ratio < 60 % (freshness policy mis-tuned or
someone is force-refreshing), quality score p10 dropping (silent degradation).

**Audit trail.** Every served equipment record can be reconstructed:
`answer → run_id → steps → artifacts → raw_data → normalized row → hash`.
Retention: runs 400 days, artifacts 90 days, raw snapshots 7 years in object
storage (compliance), normalized history indefinitely.

## 7.2 Security

| Control | Implementation |
|---|---|
| Credential storage | HashiCorp Vault / Azure Key Vault; short-lived lease; app reads at context creation |
| Credentials in prompt | **structurally impossible** — the agent process has no secret env vars and no tool that accepts one |
| Transport | TLS everywhere; mTLS agent→API; OIDC client credentials |
| AuthZ | Scopes `equipment.read` / `equipment.search` / `equipment.write`; chat identity has no write scope |
| Tenant isolation | `tenant_id` on every row and every query; row access policies in Snowflake |
| Prompt-injection containment | Scraped text is data, never instruction: it is rendered into a fenced, labelled block; the system prompt states that source content cannot change tools or policy; tool inputs are re-validated server-side |
| Abuse limiting | Per-actor quota (20 SIS lookups/h), global source semaphore, nightly cap |
| PII | Serial numbers and equipment specs are not PII; the actor identity is, and is stored hashed in the warehouse, plaintext only in the access log |
| Secrets in logs | Redaction filter + CI secret scan + no raw HTML in logs (artifacts go to object storage with signed, expiring URLs) |
| Browser hardening | No extensions, no persistent profile beyond the session vault, downloads disabled, `--no-sandbox` only inside an already-sandboxed container |
| Supply chain | Pinned deps, hash-locked lockfile, pinned Playwright browser build, image scanning |
| Legal boundary | No anti-bot circumvention; rate limits honoured; access strictly under the dealer's licensed SIS entitlement |

## 7.3 Freshness (configurable, server-side)

`config/freshness.yaml`:

```yaml
default:
  ttl_days: 30
  hard_stale_days: 365
  negative_cache_ttl_days: 7
  on_stale: refresh_if_source_healthy   # serve_cache | refresh_if_source_healthy | force_refresh
  allow_stale_fallback: true
sources:
  cat_sis:
    ttl_days: 90            # build data barely changes
    hard_stale_days: 730
field_classes:              # per-field override beats per-source
  static:   { fields: [build_date, engine_family, manufacturer], ttl_days: 3650 }
  volatile: { fields: [parts_data, parts_manual_url],            ttl_days: 30 }
overrides:
  - when: { reason: force_refresh, actor_role: engineer } then: { ttl_days: 0 }
  - when: { circuit_state: open }                         then: { on_stale: serve_cache }
```

Evaluation returns one of `FRESH | STALE | HARD_STALE | MISSING | NEGATIVE_CACHED`
plus an action (`use_cache | refresh | refresh_or_fallback`). The model sees the
*label and age*, and is required to state them; it does not get to decide the
policy. Changing freshness is a config change and a deploy — an auditable event,
not a prompt edit.
