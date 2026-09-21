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
field with no provenance entry cannot be rendered by Maia.

**`status`** — `ACTIVE | NOT_FOUND | STALE | QUARANTINED | SUPERSEDED`.
`NOT_FOUND` is a real, cacheable answer (negative caching, TTL 7 days) so we do
not re-drive a browser for a typo'd serial 40 times a day. `QUARANTINED` means
validation failed — the row is retained for forensics but is never served to
Maia as fact.

## 3.3 Normalized equipment JSON (canonical contract)

Full JSON Schema: `schemas/equipment.normalized.schema.json` (Draft 2020-12).
Shape:

```jsonc
{
  "schema_version": "1.2.0",
  "serial_number": "CAT0336LKBW00123",     // normalized: upper, no spaces/dashes
  "serial_number_raw": "cat0336-lkbw00123",// exactly what the user/source gave
  "equipment_model": "336",
  "equipment_type": "HYDRAULIC_EXCAVATOR", // controlled vocabulary
  "manufacturer": "Caterpillar",
  "build_date": "2019-07",                 // ISO-8601, partial dates allowed

  // The equipment-details block, read FIRST from the source detail page, in
  // this order. Dates arrive as the source publishes them (SIS: MM/DD/YYYY)
  // and are stored as ISO-8601.
  "machine_serial_number": "CAT0336LKBW00123",  // must equal serial_number
  "machine_build_date": "2014-08-02",
  "engine_serial_number": "FIX00588",
  "engine_build_date": "2014-06-30",

  "engine_family": { "model": "C9.3B", "arrangement": "5170340", "emissions": "Tier 4 Final" },
  "specifications": [
    { "group": "Hydraulics", "name": "Pump flow", "value": 2, "unit": "L/min",
      "value_raw": "2 x 260 L/min", "provenance_id": "p_014" }
  ],
  // Every "Product - …" group the detail page showed, in page order, entire
  // group first. Rows are the table's own cells; `values` keys them by the
  // table's own column headings when the page publishes them.
  "parts_data": {
    "group_titles": ["Product - Entire Group (CAT0336LKBW00123)", "Product - Attachments (…)"],
    "group_count": 2,
    "entire_group_title": "Product - Entire Group (CAT0336LKBW00123)",
    "columns": ["Part Number", "Serial Number", "Part Name", "Install Ind.", "Install Date", "Description"],
    "total_rows": 5,
    "serial_mismatched_groups": [],
    "selector_id": "detail.parts_group",
    "groups": [
      { "title": "Product - Entire Group (CAT0336LKBW00123)", "group_serial": "CAT0336LKBW00123",
        "is_entire_group": true, "discovered_by": "selector:detail.parts_group",
        "columns": [ /* … */ ], "row_count": 4, "column_count": 6,
        "rows": [ { "cells": ["1000", "FIX00588", "Engine", "Factory", "", "ENGINE"],
                    "values": { "Part Number": "1000", "Serial Number": "FIX00588" } } ] }
    ]
  },
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
   is *omitted* and recorded in `quality.violations`. Maia renders the two
   differently ("not published by SIS" vs "couldn't read it this run").
3. Units are always split into `value` + `unit`, with `value_raw` kept verbatim
   so nothing is lost in normalization.
4. `schema_version` is stored per row; readers must tolerate older versions.
5. **`machine_serial_number` is the cross-check, not a duplicate.** It is what
   the detail page itself claims the machine is. It must equal `serial_number`;
   a mismatch means the wrong record was read, and the row is quarantined rather
   than served. A row with no `machine_serial_number` cannot be served at all —
   it is a required field.
6. A parts row is only ever the cells the table showed. Nothing is totalled,
   deduplicated or inferred, and a group whose table could not be read has
   `rows: []` with its `row_count`, never a partial list presented as complete.

### Migrating an existing warehouse (1.1.0 → 1.2.0)

`sql/snowflake/003_equipment_details_columns.sql` and
`sql/databricks/002_equipment_details_columns.sql` add the four columns. They
are re-runnable. Rows written before the migration have `NULL` there: that means
"not collected by that run", never "the source has none" — which is why the
distinction in rule 2 is carried in `field_provenance`, not inferred from NULL.

## 3.4 Freshness metadata

Every row carries `retrieved_at`, `last_verified_at`, `source_ttl_days` (copied
from `SOURCE_REGISTRY` at write time) and a computed `age_days`. The freshness
*decision* lives in `config/freshness.yaml`, evaluated server-side — never in the
prompt, never in the model's judgement.
