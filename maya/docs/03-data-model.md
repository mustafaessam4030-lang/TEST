# 03 — Data Model & Normalized Schema

## 3.1 Tables

| Table | Grain | Purpose |
|---|---|---|
| `EQUIPMENT_DATA` | 1 row per `(source_system, serial_number)` | Current authoritative snapshot |
| `EQUIPMENT_DATA_HISTORY` | 1 row per successful retrieval | Immutable audit / time travel |
| `AUTOMATION_RUNS` | 1 row per run | Run-level observability |
| `AUTOMATION_RUN_STEPS` | 1 row per step | Step-level forensics |
| `SOURCE_REGISTRY` | 1 row per source | Freshness TTL, enablement, SLA |
| `SELECTOR_VERSIONS` | 1 row per source+version | Which DOM contract produced a row |

`EQUIPMENT_DATA` is *not* a model training corpus. Nothing in it is ever fine-tuned
into a model or pasted wholesale into a prompt. It is read through tools, one
record at a time, with provenance attached (see `docs/08`).

## 3.2 Key design decisions

**Composite key `(source_system, serial_number)`** — the same serial can be
described by SIS2, by the dealer ERP, and by a telematics feed. They are
different claims and are stored separately, then reconciled at read time by a
precedence list. Overwriting one source with another destroys provenance.

**VARIANT columns, typed projections** — `specifications`, `parts_data`,
`raw_data` and `field_provenance` are `VARIANT` (Snowflake) / `STRING` holding
JSON (Databricks Delta). Hot attributes (`equipment_model`, `build_date`,
`engine_family`) are also materialized as typed columns for BI and fast filters.

**`data_hash`** — SHA-256 over the canonical (sorted, whitespace-normalized)
JSON of the *normalized business fields only*, excluding `retrieved_at`,
`automation_run_id` and raw HTML. Two retrievals with the same hash mean nothing
changed: we bump `last_verified_at` and skip the history insert. This keeps the
history table meaningful (it records *changes*, not *polls*).

**`field_provenance`** — per field: `{value_source, selector_id, run_id,
confidence, extracted_at}`. This is what makes "never fabricate" auditable: a
field with no provenance entry cannot be rendered by Maya.

**`status`** — `ACTIVE | NOT_FOUND | STALE | QUARANTINED | SUPERSEDED`.
`NOT_FOUND` is a real, cacheable answer (negative caching, TTL 7 days) so we do
not re-drive a browser for a typo'd serial 40 times a day. `QUARANTINED` means
validation failed — the row is retained for forensics but is never served to
Maya as fact.

## 3.3 Normalized equipment JSON (canonical contract)

Full JSON Schema: `schemas/equipment.normalized.schema.json` (Draft 2020-12).
Shape:

```jsonc
{
  "schema_version": "1.1.0",
  "serial_number": "CAT0336LKBW00123",     // normalized: upper, no spaces/dashes
  "serial_number_raw": "cat0336-lkbw00123",// exactly what the user/source gave
  "equipment_model": "336",
  "equipment_type": "HYDRAULIC_EXCAVATOR", // controlled vocabulary
  "manufacturer": "Caterpillar",
  "build_date": "2019-07",                 // ISO-8601, partial dates allowed
  "engine_family": { "model": "C9.3B", "arrangement": "5170340", "emissions": "Tier 4 Final" },
  "specifications": [
    { "group": "Hydraulics", "name": "Pump flow", "value": 2, "unit": "L/min",
      "value_raw": "2 x 260 L/min", "provenance_id": "p_014" }
  ],
  "parts_data": { "media_number": "SEBP7015", "groups": [ /* ... */ ] },
  "parts_manual_url": "https://sis2.cat.com/#/...",
  "operation_manual_url": null,            // null = not published by source. NEVER a guess.
  "source_system": "cat_sis",
  "source_url": "https://sis2.cat.com/#/...",
  "retrieved_at": "2026-09-20T18:04:11Z",
  "automation_run_id": "run_01JB…",
  "data_hash": "9f2c…",
  "quality": { "score": 0.94, "required_present": 6, "required_total": 6, "violations": [] },
  "field_provenance": { "equipment_model": { "selector_id": "sis2.detail.model.v3", "confidence": 1.0 } }
}
```

Rules that make the contract enforceable:

1. `additionalProperties: false` at every level — an unexpected key from a
   changed page is a validation error, not silent data.
2. **Absent ≠ null ≠ empty string.** A field the source does not publish is
   `null` with a `"NOT_PUBLISHED"` provenance reason. A field we failed to read
   is *omitted* and recorded in `quality.violations`. Maya renders the two
   differently ("not published by SIS" vs "couldn't read it this run").
3. Units are always split into `value` + `unit`, with `value_raw` kept verbatim
   so nothing is lost in normalization.
4. `schema_version` is stored per row; readers must tolerate older versions.

## 3.4 Freshness metadata

Every row carries `retrieved_at`, `last_verified_at`, `source_ttl_days` (copied
from `SOURCE_REGISTRY` at write time) and a computed `age_days`. The freshness
*decision* lives in `config/freshness.yaml`, evaluated server-side — never in the
prompt, never in the model's judgement.
