# Service data catalog

**Status: no company service data has been catalogued yet.** Snowflake was not reachable from the build session (see SERVICE_INTELLIGENCE_AUDIT.md §1). This file records:

- what is known from the repository;
- the data contract the application needs;
- the synthetic development dataset;
- how to produce the real catalog.

Nothing below describes a table that was observed in a live Snowflake account.

## 1. Snowflake objects referenced in this repository

These come from code on other branches, not from Snowflake metadata. Whether any of them exists in the company account is unknown.

| Object | Branch | Kind | Relevant to service KPIs? |
|---|---|---|---|
| `ORACLE_LANDING.RAW.<table>` (seeded examples `SALES.ORDERS`, `ORDER_LINE`, `INVOICE_LINE`, `CUSTOMERS`, `HR.EMPLOYEES`) | adf-snowflake-pipeline | Landing tables, plus `_ADF_LOAD_TS`, `_ADF_RUN_ID`, `_ADF_SOURCE` | Only if the framework is pointed at a service or work-order module. The seeds look like templates. |
| `ORACLE_LANDING.CTL.PIPELINE_CONTROL`, `LOAD_WATERMARK`, `LOAD_AUDIT`, `V_LAST_LOAD_STATUS` | adf-snowflake-pipeline | Load control and audit | Freshness of landed tables |
| `<env db>.<env schema>.INSPECTIONS` / `RAW_INSPECTIONS` (`INSPECTION_NUMBER`, `SERIAL_NUMBER`, `STATUS`, `INSPECTION_DATE`) | daily-inspection-pipeline | Current state and append-only history | Possibly inspection volume or status. It has no close time or SLA. |
| `RAW.DSP.DSP_ASSETS` / `DSP_ASSETS_LATEST` | dsp-data-requirements | Asset snapshot (all VARCHAR) | A possible equipment dimension, not service work |
| `MAIA_PROD.CORE.EQUIPMENT_DATA`, `AUTOMATION_RUNS`, `EQUIPMENT_PARTS`, … | maya-equipment-automation | Equipment, parts and automation telemetry | No |
| `FLEET_TELEMETRY` ("service records per unit") | maia-m6rhri | Design copy only, no DDL | Unknown |

## 2. The data contract (what the application reads)

One view, `SI_CONTRACT_VIEW` (fully qualified, for example `<SI_DB>.<SERVICE_INTEL>.V_SERVICE_REQUESTS`). It is generated from `config/source_mapping.yaml` by `python -m si.mapping ddl`.

| Column | Type | Required | Source requirement |
|---|---|---|---|
| `request_id` | VARCHAR | yes | Unique id of the request, work order or case |
| `created_at` | TIMESTAMP_NTZ (UTC) | yes | Opened time; the source time zone is declared in the mapping |
| `closed_at` | TIMESTAMP_NTZ (UTC) | no | Resolution or close time; enables resolution and throughput metrics |
| `due_at` | TIMESTAMP_NTZ (UTC) | no | SLA target time, or derived from an approved `sla_policy`; enables the SLA metrics |
| `updated_at` | TIMESTAMP_NTZ (UTC) | no | Last source change; used for dedupe and freshness |
| `loaded_at` | TIMESTAMP_NTZ (UTC) | no | Load time (for example `_ADF_LOAD_TS`); the preferred freshness basis |
| `status` | VARCHAR | yes | Raw status |
| `status_class` | VARCHAR | derived | OPEN, CLOSED, CANCELLED or UNMAPPED, from `status_classes` |
| `priority`, `category`, `service_type`, `team`, `region` | VARCHAR | no | Breakdown and filter dimensions |

**Deliberately excluded:** customer, person, free-text, contact and financial fields. The mapping validator refuses to map any column listed in `sensitive_columns`. The SQL guard also refuses any column that is not in the contract.

**Metric availability follows the mapping:**

| If this is unmapped | These are reported as unavailable |
|---|---|
| `closed_at` | Resolution metrics and throughput |
| `due_at` (and no SLA policy) | The SLA metrics |
| A dimension | Its breakdowns and filters |

## 3. SYNTHETIC development dataset

`si/fixtures.py` generates it deterministically (seed 7). It is about 5,700 requests, from 1 Apr 2025 to an "as of" time of 20 Oct 2026 06:00 UTC, in `SI_SYNTHETIC.RAW.SERVICE_REQUESTS`. It is used only with `SI_BACKEND=fixture`.

- **Column names differ from the contract on purpose** (`TICKET_NO`, `OPENED_TS`, `STATUS_TXT`, …), so the mapping is exercised. It includes two fake sensitive columns (`CUSTOMER_NAME`, `PROBLEM_DESCRIPTION`) that the mapping excludes.
- **Planted patterns:**
  - slower and worsening resolution in region South;
  - a Hydraulics volume surge in the last 60 days;
  - an aged backlog in Field Team C.
- **Planted defects:**
  - missing SLA target (5%), priority (2%) and region (3%);
  - unmapped status values;
  - close time before open time;
  - stale duplicate rows.

These figures mean nothing about Mantrac's operations.

`tests/conftest.py::KNOWN_ROWS` is a separate set of 11 hand-built rows. Every KPI's expected value was computed by hand from them.

## 4. Producing the real catalog

```bash
export SI_BACKEND=snowflake SI_CONTRACT_VIEW=<SI_DB>.<SERVICE_INTEL>.V_SERVICE_REQUESTS   # plus SNOWFLAKE_* (see README)
python -m si.discover inventory [--databases DB1,DB2]
python -m si.discover profile <DB.SCHEMA.TABLE> --id <ID> --created <CREATED_TS> --status <STATUS> \
       --columns <CLOSED_TS>,<PRIORITY>,<REGION>
```

- **`inventory`** writes `discovery/inventory.md` and `discovery/inventory.json`. They contain:
  - the role, warehouse and region;
  - the visible databases and warehouses;
  - every visible table with its row count and last-altered time;
  - a heuristic ranking of service-like tables, with candidate id, created, closed, due and status columns, and possibly-sensitive columns flagged;
  - anything the role could not read.
- **`profile`** runs aggregate-only queries and samples no rows. It reports:
  - row and distinct-id counts, so duplicates show;
  - first and last creation time;
  - monthly volume, which shows historical coverage and gaps;
  - null share per column;
  - status value counts.

Replace sections 1–3 of this file with that output, then write `config/source_mapping.yaml` from `config/source_mapping.example.yaml`. Then run:

```bash
python -m si.mapping check   # every mapped column exists; every status value is classified
python -m si.mapping ddl     # CREATE SECURE VIEW for the schema owner to run
```
