# Service data catalog

> **Update, 23 Oct 2026.** Snowflake is now connected (account FY56654, AZURE_WESTEUROPE, read-only connector).
> Section 0 below is real metadata. Row counts come from `SHOW TABLES`; columns come from `SHOW COLUMNS`.
> **Not yet measured:** value ranges, freshness, null rates and duplicates. Measuring them needs a warehouse on the connector.
> Sections 1–4 are the original pre-connection notes and the contract design.

## 0. Phase-1 source tables (chosen by the business owner)

**Source.** Everything comes from `CAT_SHARE`, a database imported from Caterpillar's share `CAT.CAT_AZURE_WEST_EU_NL.HELIOS_DEALER_DATA_SHARE_K666_PROD_SHARE`. All tables are TRANSIENT and maintained by Cat.

**Target.** `SERVICE_INTELLIGENCE.<same schema>.<same table>`, created by `sql/snowflake/01_phase1_cat_share_copy.sql`. The business owner runs it; the read-only connector cannot.

| Table | Rows | Size | Grain (from column names — confirm in profiling) |
|---|---|---|---|
| DOMAIN_TELEMETRY.FAULT_CODE_BASIC_DAILY | 67,929,254 | 4.2 GB | One fault-code event per asset per message: SPN/FMI/CID/EID codes, `LEVEL`, occurrence count and time |
| DOMAIN_TELEMETRY.FAULT_CODE_HISTORY | 7,853,364 | 0.56 GB | Fault-code history: `SEVERITY`, ECU and fault-type descriptions, SMH total, location |
| DOMAIN_TELEMETRY.TELEMETRY_FUEL_BASIC_DAILY | 5,381,210 | 0.31 GB | Daily fuel readings per asset: fuel level %, cumulative fuel, idle fuel (each with a unit of measure) |
| DOMAIN_TELEMETRY.TELEMETRY_SMU_LOCATION_BASIC_DAILY | 8,520,895 | 0.57 GB | Daily service-meter hours, idle hours, distance, location and `VALID_FLAG` per asset |
| OLGA.COMPANY_CONFIG_TABLE | 9 | — | Dealer codes and descriptions, generation time |
| OLGA.CUSTOMER_TOTALS_TABLE | 14,911 | 1.9 MB | Monthly per customer and division: BASE, SALES, BOOST and OPPORTUNITY by component family |
| OLGA.EQUIPMENT_REPORT_DETAIL_TABLE | 125,138 | 18.5 MB | One row per machine: model, customer, segment, utilisation, SMU, parked status, 12-month sales, base and opportunity |
| OLGA.LOST_OPPORTUNITY_REPORT_TABLE | 25,554,944 | 0.92 GB | Monthly (`YEAR_MONTH`) per serial, branch and SMCS component: SALES, BASE, BOOST, OPPORTUNITY and LOST_OPPORTUNITY (`_SCP`) |
| OLGA.OPPORTUNITIES_TABLE | 13,497,886 | 1.6 GB | One opportunity per serial and component/job: TOTAL_VALUE, LABOR_VALUE, TARGET_DATE, TARGET_SMU, LEAD_SCORE, CONFIDENCE_INDEX, BRANCH, `END_OF_MONTH` |
| OLGA.OPPORTUNITY_MAJORCLASS_SUBCATEGORY_PARTCOST_TABLE | 245,196,480 | 3.6 GB | Part cost per opportunity (`OPPORTUNITY_KEY`) by major class and commercial group |

### Findings that shape the design

1. **Telemetry values are stored as text.** That includes every date and time (`MODULE_DATETIME`, `OCCURRENCE_DATETIME`, `RECEIVED_DATETIME`, `SERVICE_METER_HOURS_DATETIME`, …) and every number (`OCCURRENCE_COUNT`, `SERVICE_METER_HOURS_VALUE`, `CUMULATIVE_FUEL_VALUE`, `LATITUDE`, …). The curated views must convert them with `TRY_TO_TIMESTAMP` / `TRY_TO_NUMBER` and report how many values fail to convert. The formats are unknown until profiling.
2. **Join keys between the sources:**
   - Machines join on serial number: `ASSET_SERIAL_NUMBER` (telemetry) = `SERIAL_NUMBER` (OLGA) = `SERIALNUMBER` (Helios `MANDL_CDM_PROD.MANCDM.SERVICEORDER`).
   - Dealers join on `DEALER_CODE`.
   - Customers join on `PRIMARY_CUSTOMER_IDENTIFIER` / `CUSTOMER_NUMBER`.
   - Inside OLGA, opportunities join to part cost on `OPPORTUNITY_KEY`.
   - Each match rate must be measured before any join is used in a KPI.
3. **OLGA tables look like monthly snapshots** (`END_OF_MONTH`, `YEAR_MONTH`, `GENERATION_TIME`). KPIs must choose one snapshot (latest, or the month asked for) so totals are not counted twice.
4. **Freshness columns** are `REFRESH_TIMESTAMP` (LTZ) and `LAST_UPDATE_TIMESTAMP` on telemetry, and `LAST_UPDATE_TIMESTAMP` / `GENERATION_TIME` on OLGA. `FAULT_CODE_HISTORY.LAST_UPDATE_TIMESTAMP` is text.
5. **Sensitive columns are excluded from the app's views and the assistant:**
   - names: `CUSTOMER_NAME`, `DEALER_CUSTOMER_NAME`, `PARENT_CUSTOMER_NAME`, `SALES_REP_NAME`;
   - location: `LATITUDE`/`LONGITUDE`, `LOCATION_LATITUDE`/`LOCATION_LONGITUDE`, `LOCATION_ADDRESS`, `POSTAL_CODE`, `COUNTY`, `STATE`;
   - other: `CUSTOM_FIELD` (array).

   Customer and asset identifiers stay, for drill-down by authorised roles only.
6. **The app's data model needs extending.** The current contract assumes one service-request table (open → closed, SLA). These sources are about **events** (faults), **readings** (hours, fuel) and **opportunities** (value by component). The next step adds one governed view and one metric set per subject (fault events, asset utilisation, opportunities), using the same registry, guard and executor.

### Other service data found (not in phase 1)

| Object | Rows | Notes |
|---|---|---|
| `MANDL_CDM_PROD.MANCDM.SERVICEORDER` | 74,144 | Helios production service orders: OPENDATE, LASTCLOSEDATE, FINISHEDDATE, PROMISEDENDDATE, status, open status, priority, repair type, store, division and department. This fits the existing service-request contract. |
| `MANDL_CDM_PROD.MANCDM.SERVICEORDERJOB` | 140,018 | Job lines: estimated, standard and target hours; rework flag and reason; component and job codes |
| `SNOWPY.ANALYTICS.SERVICE_RESPONSE_TIME` | — | Existing dbt rule: `is_on_time = actual_start_date <= planned_start_date`, from ManPRO DB2 invoices (customer type 'CUSTOMER' only) |
| `SVC_CORTEX.MANDW.V_RPT_SERVICE_METRICS` | — | Existing invoice-segment financial metrics: labour, parts and misc sell/cost, gross profit %, labour-to-invoice days |
| `MANDL_DEV.RENTAL_APP.V_SERVICE_JOBS` | — | ManPRO service jobs on rental units since 2025 |

---

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
