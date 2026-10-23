# Service data catalog

> **Update, 23 Oct 2026 (step 2, profiling done).** Snowflake is connected (account FY56654, AZURE_WESTEUROPE, read-only
> connector, warehouse `CLAUDE_WH`, role `DEV_ADMIN`). Section 0 is metadata (`SHOW TABLES`, `SHOW COLUMNS`).
> Section 0.1 is the measured profile. Every figure comes from an aggregate-only query: counts, percentages, min/max and
> value counts, no rows. Sensitive columns got a null rate only.
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

1. **Telemetry values are stored as text.** That includes every date and time (`MODULE_DATETIME`, `OCCURRENCE_DATETIME`, `RECEIVED_DATETIME`, `SERVICE_METER_HOURS_DATETIME`, …) and every number (`OCCURRENCE_COUNT`, `SERVICE_METER_HOURS_VALUE`, `CUMULATIVE_FUEL_VALUE`, `LATITUDE`, …). The curated views must convert them with `TRY_TO_TIMESTAMP` / `TRY_TO_NUMBER` and report how many values fail to convert. Profiling measured 100 % conversion with AUTO format (section 0.1).
2. **Join keys between the sources:**
   - Machines join on serial number: `ASSET_SERIAL_NUMBER` (telemetry) = `SERIAL_NUMBER` (OLGA) = `SERIALNUMBER` (Helios `MANDL_CDM_PROD.MANCDM.SERVICEORDER`).
   - Dealers join on `DEALER_CODE`.
   - Customers join on `PRIMARY_CUSTOMER_IDENTIFIER` / `CUSTOMER_NUMBER`.
   - Inside OLGA, opportunities join to part cost on `OPPORTUNITY_KEY`.
   - Measured in section 0.1: serial joins work (77–100 %); dealer and customer codes do not join across Cat and dealer systems (0 %).
3. **OLGA tables look like monthly snapshots** (`END_OF_MONTH`, `YEAR_MONTH`, `GENERATION_TIME`). KPIs must choose one snapshot (latest, or the month asked for) so totals are not counted twice. Measured: OPPORTUNITIES and CUSTOMER_TOTALS hold one snapshot (Sep 2026); LOST_OPPORTUNITY holds 36 months.
4. **Freshness columns** are `REFRESH_TIMESTAMP` (LTZ) and `LAST_UPDATE_TIMESTAMP` on telemetry, and `LAST_UPDATE_TIMESTAMP` / `GENERATION_TIME` on OLGA. `FAULT_CODE_HISTORY.LAST_UPDATE_TIMESTAMP` is text.
5. **Sensitive columns are excluded from the app's views and the assistant:**
   - names: `CUSTOMER_NAME`, `DEALER_CUSTOMER_NAME`, `PARENT_CUSTOMER_NAME`, `SALES_REP_NAME`;
   - location: `LATITUDE`/`LONGITUDE`, `LOCATION_LATITUDE`/`LOCATION_LONGITUDE`, `LOCATION_ADDRESS`, `POSTAL_CODE`, `COUNTY`, `STATE`;
   - other: `CUSTOM_FIELD` (array).

   Customer and asset identifiers stay, for drill-down by authorised roles only.
6. **The app's data model needs extending.** The current contract assumes one service-request table (open → closed, SLA). These sources are about **events** (faults), **readings** (hours, fuel) and **opportunities** (value by component). The next step adds one governed view and one metric set per subject (fault events, asset utilisation, opportunities), using the same registry, guard and executor.

## 0.1 Profiling results (measured 23 Oct 2026)

**Snapshot.** The copies in `SERVICE_INTELLIGENCE` were taken on 23 Oct 2026 at 21:27 UTC. Cat's `REFRESH_TIMESTAMP` is one value per
telemetry table, `2026-10-23 16:00 +04:00`, so Cat reloads each telemetry table in full every day.

**Null rates.** Full scans for COMPANY_CONFIG, CUSTOMER_TOTALS, EQUIPMENT_REPORT_DETAIL and SERVICEORDER. Fixed-seed Bernoulli
samples elsewhere: 0.8–2.5M rows per table (FAULT_CODE_BASIC_DAILY 2 %, FAULT_CODE_HISTORY 10 %, FUEL 20 %, SMU 15 %,
LOST_OPPORTUNITY 5 %, OPPORTUNITIES 10 %, PART_COST 1 %). The sampling error is under 0.1 percentage points. Everything else in this
section is a full-table count.

### Telemetry (Cat, `DOMAIN_TELEMETRY`)

| | FAULT_CODE_BASIC_DAILY | FAULT_CODE_HISTORY | TELEMETRY_FUEL_BASIC_DAILY | TELEMETRY_SMU_LOCATION_BASIC_DAILY |
|---|---|---|---|---|
| Rows / assets | 67,929,254 / 14,007 | 7,853,364 / 3,960 | 5,381,210 / 10,216 | 8,520,895 / 16,718 |
| Event-time range | Oct 2021 – Oct 2026 (2 rows dated 2041) | Apr 2023 – Oct 2026 (volume from Dec 2023) | Oct 2021 – Oct 2026 (1 row 2027) | Oct 2021 – Oct 2026 (2 rows 2027/2037) |
| Freshness (latest event) | 23 Oct 2026 11:53 | 23 Oct 2026 14:20 | 23 Oct 2026 | 23 Oct 2026 |
| Exact duplicate rows | 0 | 0 | 0 | 0 |
| Key duplicates | 0 on message+asset+ECU+codes+time; **2,390,327 (3.5 %)** repeat the same fault event in another message | **2,907,134 (37 %)** repeat asset+ECU+codes+time | 42 on asset+received time; 26,903 extra readings per asset-day | 71 on asset+received time; 41,816 extra readings per asset-day |
| Text → timestamp | 100 % (`YYYY-MM-DD HH:MI:SS.fff`) | 100 % (all date columns, `LAST_UPDATE_TIMESTAMP` and `CREATE_TIMESTAMP` included) | 100 % (ISO `…T…Z`) | 100 % (ISO `…T…Z`) |
| Text → number | 100 %. `OCCURRENCE_COUNT` 0–255; **255 is a saturated counter in 11 % of rows** | 100 %. `TRUSTED_MASTER_SMH_TOTAL` max 1,040,706 (implausible) | 100 %. **`CUMULATIVE_FUEL_VALUE` max 4,294,967,295 (2³²−1 sentinel)**; 6 rows of fuel level > 100 %; 8,808 rows where idle fuel > total fuel | 100 %. SMU median 3,909 h; **1,462 rows > 200,000 h** (max 449,645); 135,085 rows (1.6 %) where idle hours > total hours |
| Main nulls | SPN 85 %, EID 59 %, CID 56 %, FMI 41 % (the J1939 SPN/FMI and Cat CID/EID code systems are mutually exclusive); `OCCURRENCE_COUNT` 2.1 % | SPN 82 %, CID 64 %; location 19 % | fuel level 33 %, idle fuel 26 %, cumulative fuel 12 % | idle hours 51 %, distance 35 %, location 9 %, SMU 3.3 % |
| Values | `LEVEL` 1/2/3 (2 stray rows 128/136); manufacturer CAT 99.5 % | **`SEVERITY` LOW 56 % / MEDIUM 35 % / HIGH 9 %**; Diagnostic 54 % / Event 46 % | units: Percentage, Liters | units: Hours, Kilometer; `VALID_FLAG` V 90.6 % / **I 9.4 %** |

- **Coverage.** Connected assets grow steadily: SMU-reporting assets went from 4,449 in Nov 2021 to 11,244 in Sep 2026, and 9,883 assets
  reported in the last 7 days. **FAULT_CODE_BASIC_DAILY volume doubles from Mar 2026** (≈1.2M → 2.7–3.8M rows a month) while assets stay
  flat at about 7,000; June 2025 is also a one-month spike (2.86M). Cat should confirm whether this reflects reporting or real fault behaviour.
- **Dealer.** `DEALER_CODE` is `K666` (the share's code) in every row, so it can't split results by branch. `FAULT_CODE_HISTORY.DEALER_CODE_SHARE`
  carries the OLGA dealer codes (K260, K270, K275, K290, K660, K66L, K680, K690, M620), with K666 on 63 % of rows.

### OLGA (`OLGA`)

| Table | Measured |
|---|---|
| COMPANY_CONFIG_TABLE | 9 dealer codes: K260, K270, K275, K290, K660, K66L, K680, K690, M620. `GENERATION_TIME` 16 Oct 2026 (format `YYYY-MM-DD HH:MI:SS.fff -0500`). |
| CUSTOMER_TOTALS_TABLE | **One snapshot**, `END_OF_MONTH` = `'2026-09'`. No nulls and no key duplicates (dealer+customer+division+vertical). `GENERATION_TIME` is `YYYY-MM-DD-HH-MI-SS--0500` and **needs an explicit format: AUTO conversion gives 0 %**. Σ opportunity = 609.6M. |
| EQUIPMENT_REPORT_DETAIL_TABLE | 125,138 rows, 122,370 serials. **2,768 serials appear under two dealers** (unique on dealer+serial; OLGA flags 3,681 rows `DUPLICATE_SERIAL_NUMBER = YES`). 55 % Included / 45 % Excluded; 10 % connected. `MONTHLY_UTILIZATION`: median 30, p99 557, **740 rows > 744**. Its unit depends on `SMU_TYPE` (Hour 78 %, Day 15 % = standby engines, Mile 1 %, blank 6 % = "Utilization Error"). `SMU` max 3.4M (938 rows > 200k). Dates convert 100 % (`IN_SERVICE_DATE` 1960 → Sep 2026). `LAST_INVOICE_DATE` 69 % null. |
| LOST_OPPORTUNITY_REPORT_TABLE | **36 monthly snapshots, Oct 2023 → Sep 2026**, about 0.66–0.77M rows and 28.7–31.6k serials each. **`LOST_OPPORTUNITY_SCP = OPPORTUNITY_SCP − SALES_SCP` in 100 % of rows**, so a negative value means Mantrac sold more than OLGA predicted (484,934 rows, Σ −652M); it is not an error. Monthly Σ lost is 20–34M until Jun 2026, then **12.7–15.0M for Jul–Sep 2026**: confirm whether recent months restate. 272 exact duplicate rows; the 11-column candidate key still has 65,922 duplicates (no natural key). No nulls. |
| OPPORTUNITIES_TABLE | **One snapshot**, `END_OF_MONTH` = `'September 2026'` (a different format from CUSTOMER_TOTALS). 13,497,886 rows, 59,055 serials, Σ `TOTAL_VALUE` 3.61bn. `OPPORTUNITY_KEY` and `OPPORTUNITY_ID` are unique. `TARGET_DATE` runs Jul 2023 → 2099: **7.40M (55 %) are already past**, 64,817 use the `2099-12-31` placeholder, and 493,936 (value 124.9M) fall in the next 90 days. 510k rows have `TOTAL_VALUE = 0`. `LEAD_SCORE` (0–15), `CONFIDENCE_INDEX`, `LABOR_HOURS` and the intervals are 72 % null. Source: LCCO 72 %, CAT 20 %, CAT GENERIC 8 %. `LAST_REPORTED_SMU_DATE` uses a `9999-12-31` placeholder. Updated 13 Jul – 16 Oct 2026. |
| OPPORTUNITY_MAJORCLASS_SUBCATEGORY_PARTCOST_TABLE | 245M rows, no duplicates. **Every `OPPORTUNITY_KEY` matches OPPORTUNITIES_TABLE (100 %).** **`PART_COST` = `PART_COST_USD`** (Σ ratio 1.000), so OLGA values are in USD. Codes 1.35 % null. |

### Helios service orders (`MANDL_CDM_PROD.MANCDM.SERVICEORDER`, live, not copied)

- **Size and freshness.** 74,144 rows, `SERVICEORDERID` unique (658 `SERVICEORDERNUMBER` repeats). `OPENDATE` runs from 2002 to 22 Oct 2026, plus 2 rows
  dated 1925. `LASTUPDATEDDATETIME` max is 23 Oct 2026 14:50. `MODIFIEDON` uses a `9999-12-31` placeholder.
- **Deleted rows.** **8,502 rows (11 %) have `ROWOPERATION = 'DELETE'`.** These are soft deletes and must be excluded everywhere.
- **Order types.** Revenue 66 %, Internal 29 %, Expenses 5 %, Warranty under 0.1 %.
- **Statuses.** `SERVICEORDERSTATUS` is I (Invoiced) 71,790, O (Open) 2,326, C (Closed) 27 and M 1. `ISOPEN` agrees with O. 486 non-open rows
  have no `LASTCLOSEDATE`.
- **Source systems.** Helios `CAJHDR-CAJLBR-EQPOPH` carries roughly 400–600 orders a month. **Oracle Fusion is taking over:** it went from
  a few orders a month in 2025 to 601 in Sep 2026, more than Helios that month. **Nearly every Fusion order is still `ISOPEN`**,
  so either Fusion statuses do not flow into this table yet or those orders really are open. Until the owner confirms, Fusion orders
  inflate the backlog.
- **Null rates.** `PROMISEDENDDATE` 100 %, `FIRSTLABORDATE` 100 %, `REPAIRTYPE` 100 %, `LASTCLOSEDATE` 3.8 %, `FINISHEDDATE` 4.8 %,
  `FIRSTACTIVITYDATE` 0.8 %, priority 1.0 % blank, `PROMISEDSTARTDATE` 0.01 %. `PROMISEDSTARTDATE` is a real date, not a
  default: it falls on the open day for only about 10–25 % of orders.
- **Data checks.** No order closes before it opens. 3 orders finish before they open.
- **Previews** (all types, deletes included):
  - median 19 days from open to close;
  - 2,326 open orders, 1,848 of them older than 30 days and 48 older than a year;
  - first activity on or before the promised start for 92.0 % of orders in the last 12 months (6,595 of 7,171).
- **Dimensions.** Priority N 87 %, H 9 %, E/L/M about 2 %. Division G 54 %, E 38 %, X 8 %. Brand CA (Cat) 79 %, OY 13 %.

### Join keys

| Key | Result |
|---|---|
| **Serial number.** All sources use 8-character Cat serials (98.7–99.9 % match `^[A-Z0-9]{3}[0-9]{5}$`); exact and letters/digits-only matching give the same result. | **Telemetry ↔ OLGA equipment: 13,026 of 16,822 telemetry serials (77.4 %).** The 3,781 that don't match have valid formats, so the machines are probably outside OLGA's population. Telemetry ↔ OLGA opportunities 68.2 %, ↔ lost opportunity 69.6 %. OLGA opportunities ↔ equipment 100 %; lost opportunity ↔ equipment 97.6 %. |
| | **SERVICEORDER (live rows) ↔ OLGA equipment: 10,105 of 12,731 order serials (79.4 %); Cat-brand orders 90.4 %.** SERVICEORDER ↔ telemetry: 3,617 (28.4 % of order serials; 21.5 % of telemetry serials have a Helios order). `SERVICEORDER.DEVICENUMBER` is **not** a serial (0 matches). |
| Dealer | Telemetry `DEALER_CODE` (K666) ↔ OLGA: **0 %**. Use `FAULT_CODE_HISTORY.DEALER_CODE_SHARE` or reach OLGA through the serial. |
| Customer | Telemetry `PRIMARY_CUSTOMER_IDENTIFIER` (10-digit Cat customer ID) ↔ OLGA `CUSTOMER_NUMBER` (8-character dealer number): **0 %** under every transformation tried, so a Cat-to-dealer customer mapping is needed. SERVICEORDER `CUSTOMERID` ↔ OLGA: 2,070 of 3,490 (59 %). |
| Opportunity | Part-cost `OPPORTUNITY_KEY` ↔ OPPORTUNITIES_TABLE: 100 %. |

### What this changes

1. **Service-order KPIs filter on `ROWOPERATION <> 'DELETE'`.** Closed means status I or C. `PROMISEDENDDATE` is empty, so there is no completion SLA;
   on-time *start* uses `FIRSTACTIVITYDATE ≤ PROMISEDSTARTDATE`.
2. **Oracle Fusion orders need their open/closed status confirmed** before the backlog KPIs include them.
3. **Fault events must be de-duplicated:**
   - FAULT_CODE_BASIC_DAILY by asset+ECU+codes+time (ignoring message ID);
   - FAULT_CODE_HISTORY by the same key.

   Fault "volume" counts distinct events, not `OCCURRENCE_COUNT`, which saturates at 255.
4. **Telemetry readings need cleaning rules:**
   - `VALID_FLAG = 'V'` only;
   - one reading per asset per day (the latest);
   - drop sentinel and out-of-range values (fuel ≥ 4,294,967,295; SMU > 200,000 h; fuel level > 100 %; idle > total);
   - drop event dates more than 1 day in the future.
5. **OLGA snapshots.**
   - Opportunities and customer totals are single month-end snapshots. Our copies must be snapshotted monthly to build a trend;
     LOST_OPPORTUNITY already has 36 months.
   - Parse `END_OF_MONTH` per table (`'YYYY-MM'` vs `'Month YYYY'`), and `GENERATION_TIME` with an explicit format.
   - Open opportunities exclude `TARGET_DATE` = 2099-12-31 and need a rule for the 55 % that are past due.
6. **Joins go through the serial number.**
   - Telemetry ↔ OLGA (77 %) and OLGA ↔ Helios (79 %, Cat 90 %) are usable.
   - Telemetry ↔ Helios (28 %) only covers machines Mantrac services through Helios.
   - Dealer and customer codes don't join across Cat and dealer systems without a mapping.
7. **Copies go stale daily.**
   - The telemetry copies need a scheduled refresh: a task re-running `01_phase1_cat_share_copy.sql` section 3, or views straight on `CAT_SHARE`.
   - SERVICEORDER is read live.

## 0.2 Profile queries

`sql/snowflake/03_phase1_profile.sql` holds the full aggregate-only profile: 17 queries, one result set each. The figures above were
produced with the same checks, sent through the read-only connector in compact form, plus a column-null scan (`OBJECT_CONSTRUCT(*)` + `FLATTEN`
on the samples listed above). Re-run the file in Snowsight to refresh the profile after each copy refresh.

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
