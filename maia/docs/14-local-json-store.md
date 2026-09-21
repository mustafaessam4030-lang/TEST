# 14 — The temporary local JSON store

Snowflake is not ready. Until it is, every successful Caterpillar SIS lookup is
saved to disk, in full, in a form a person can read and a machine can re-load.

This is a **storage** change only. The SIS extraction goal is unchanged, the
local test fixture is not a source of truth, and the source on every saved
record is the source that actually produced it.

## 14.1 One seam, three implementations

```
EquipmentRepository                    (app/repositories/base.py — a Protocol)
   ├── LocalJsonRepository   ← active now      (local_json_repo.py)
   ├── MemoryEquipmentRepository  dev + tests  (memory_repo.py)
   └── SnowflakeEquipmentRepository  later     (snowflake_repo.py)
```

Switching is one setting:

```bash
MAIA_REPOSITORY=local_json      # default today
MAIA_LOCAL_STORE_DIR=logs/sis-results
MAIA_REPOSITORY=snowflake       # when the warehouse is ready
```

Nothing above the seam changes: not Maia, not her tools, not the gateway, not
the SIS automation. That is the whole point of putting the temporary store
behind the same interface as the permanent one.

`upsert()` takes an optional `ExtractionArtifact` alongside the record — the
complete raw page read. The local store writes it to `raw_data`; Snowflake
writes it to the `RAW_DATA` VARIANT column; the memory store ignores it.

## 14.2 The flow

```
Maia
  ↓  get_equipment_from_local_store(serial)        ← always first, no browser
Tool gateway
  ↓  repo.get_current(serial, source)
found + fresh ────────────────────────────────────→ return it (cache hit)
  ↓  missing / stale
REAL Caterpillar SIS            (scroll the content pane, extract everything)
  ↓  NORMALIZE → VALIDATE
  ↓  PERSIST    JSON + TXT + screenshots
  ↓
return to Maia
```

`get_equipment_from_local_store` is the same store read as
`get_equipment_from_database`; the name says where the records physically live
today. Both tool names stay valid after the warehouse lands.

## 14.3 What a successful lookup writes

```
logs/sis-results/
    <SERIAL>_<RUN_ID>.json      the record + the COMPLETE raw extraction
    <SERIAL>_<RUN_ID>.txt       the same values, laid out for a person
    <SERIAL>_<RUN_ID>/
        page.png                the detail page as it first rendered
        details.png             the equipment-details section, after scrolling
        parts.png               the "Product - …" parts group
    _index.json                 (source, serial) -> the newest result file
    _runs/<RUN_ID>.json         every step of the run, with timings
```

The JSON carries, at the top level: `serial_number`, `run_id`, `source`,
`source_system`, `retrieved_at`, `saved_at`, `final_url`, `page_title`,
`model`, `equipment_type`, `manufacturer`, `build_date`,
`machine_serial_number`, `machine_build_date`, `engine_serial_number`,
`engine_build_date`, `engine_family`, `specifications`, `parts_data`,
`parts_summary` (group titles, part numbers, part names, part serials pulled
out for quick reading), `parts_manual_url`, `operation_manual_url`,
`manual_urls`, `extraction_status`, `payload_kind`, `selector_version`,
`field_provenance`, `quality`, `data_hash`, `schema_version`, `status`,
`screenshots`, `evidence`, `record`, and `raw_data`.

## 14.4 Three rules the store exists to enforce

**Nothing is discarded.** `raw_data.extracted_fields` is exactly what the
adapter read from the page, including keys the canonical schema has no column
for. A field with no column is still evidence; dropping it means driving a
browser again to get it back.

**A missing value is `null`.** Never an empty string, never a default, never a
guess — and `field_provenance` records *why* it is null (`NOT_PUBLISHED` when
the source does not publish it, versus a `quality.violations` entry when we
failed to read it). The `.txt` prints `null` and says what that means.

**A write that fails is a failed lookup.** `PERSIST` is a step of the run, and
it runs *before* the run is marked successful. If the file cannot be written,
the lookup returns `PERSISTENCE_FAILED` (HTTP 500, not retryable) and Maia is
told the lookup did not complete. A result nobody can read later did not
happen.

## 14.5 What is never written

Every payload passes through `scrub()` before it touches disk. It drops any key
matching `password | secret | token | cookie | authorization | session_state |
storage_state | credential | api_key | bearer | otp | mfa_code`, masks
credential-shaped text (`password=…`, `token: …`) wherever it appears, and
replaces the live `SIS_USERNAME` / `SIS_PASSWORD` values by exact match even if
they arrive under an innocent-looking key.

The store's files are real equipment data. They are git-ignored and excluded
from the release package: they stay on the machine that produced them.

## 14.6 A record is labelled by the source that produced it

`source` comes from the registry entry for `record.source_system` — never from
a default. A record produced by the local fixture is saved as
`"Local test fixture (NOT Caterpillar SIS)"`. Writing "Caterpillar SIS" onto a
row that came from a fixture is the one mislabelling that would make every
other guarantee in this platform worthless, and a test asserts it cannot
happen.

## 14.7 Running one real lookup

```bash
# Linux / macOS
make sis-lookup SERIAL=JAZ01865
# or: python3 scripts/e2e/sis_lookup.py --serial JAZ01865 [--headed]
```

```powershell
# Windows: double-click SIS-LOOKUP.bat, or
.\scripts\windows\sis-lookup.ps1 -Serial JAZ01865        # -Headed if MFA is asked for
```

It runs the same code the gateway runs and then prints the exact JSON path, the
exact TXT path, the extracted field count, and a YES/NO for each of Machine
Build Date, Engine Serial Number, Engine Build Date, and the parts group and
part numbers. It refuses to run against any base URL that is not Caterpillar
SIS.
