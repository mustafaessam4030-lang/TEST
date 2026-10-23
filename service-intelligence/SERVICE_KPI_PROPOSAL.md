# Service KPI proposal (phase 1) — awaiting approval

**Status: PROPOSED, 23 Oct 2026.** Nothing here is in `config/metrics.yaml` yet. Approved KPIs will be added to the registry,
which regenerates `SERVICE_METRIC_DEFINITIONS.md`, and will get hand-computed tests like the existing ones.

**What each KPI is based on.**
- The columns and metadata facts in `SERVICE_DATA_CATALOG.md` sections 0 and 0.1.
- **Gate** names a measurement from the full profile (`sql/snowflake/03_phase1_profile.sql`) that must pass before the KPI is built.
- A KPI whose gate fails is dropped or redefined, not shipped with a caveat.

## A. Service orders (Helios `MANDL_CDM_PROD.MANCDM.SERVICEORDER`)

These fit the existing service-request contract and app.

| # | KPI | Definition | Gate |
|---|---|---|---|
| A1 | Service orders opened | Distinct `SERVICEORDERID` with `OPENDATE` in the period | `OPENDATE` null % < 2; pre-2000 dates excluded and counted under data quality |
| A2 | Service orders closed | Distinct orders with `LASTCLOSEDATE` in the period | Q17 status values map cleanly to closed vs cancelled |
| A3 | Open backlog | Orders with `ISOPEN = TRUE`, as of the last refresh | none |
| A4 | Aged backlog | Open orders older than **30 days** (threshold to confirm) | none |
| A5 | Days to close (median, P90) | `LASTCLOSEDATE − OPENDATE` for orders closed in the period; close-before-open excluded | close-before-open < 1 % |
| A6 | On-time start rate | Share of orders whose first activity (`FIRSTACTIVITYDATE`) is on or before `PROMISEDSTARTDATE` | `PROMISEDSTARTDATE` filled on ≥ 60 % of recent orders |

**Dropped.** On-time *completion*: `PROMISEDENDDATE` is empty in every row. `REPAIRTYPE` is also empty, so A1–A6 break down by
`STORECODE`, `DIVISIONCODE`, `SERVICEORDERPRIORITY` and `SERVICEORDERTYPE` instead.

## B. Fleet health (Cat telemetry)

| # | KPI | Definition | Gate |
|---|---|---|---|
| B1 | Connected assets reporting | Distinct serials with an SMU reading in the last 7 days | `RECEIVED_DATETIME` converts ≥ 99 % |
| B2 | High-severity fault assets | Distinct assets with ≥ 1 fault at the top `SEVERITY` (FAULT_CODE_HISTORY) in the period | Q13 confirms the severity scale |
| B3 | Fault rate per 1,000 operating hours | Σ `OCCURRENCE_COUNT` ÷ Σ daily SMU increase × 1,000, by model | `OCCURRENCE_COUNT` and `SERVICE_METER_HOURS_VALUE` convert ≥ 99 %; SMU never decreases per asset |
| B4 | Repeat faults | Assets where the same SPN+FMI (or CID+FMI) recurs within 30 days of an earlier occurrence | as B2 |
| B5 | Idle time % | Idle hours ÷ total hours (from the daily SMU and idle-SMU increases) | as B3 |
| B6 | Idle fuel % | Idle litres ÷ total litres (from the cumulative fuel and idle fuel increases) | fuel values convert ≥ 99 % |

## C. Parts & service opportunity (OLGA)

| # | KPI | Definition | Gate |
|---|---|---|---|
| C1 | Open opportunity value | Σ `TOTAL_VALUE` in the latest snapshot, by branch, SMCS group and month of `TARGET_DATE`; target dates after 2036 excluded | `OPPORTUNITY_KEY` unique (Q09) |
| C2 | Opportunities due next 90 days | Count and Σ `TOTAL_VALUE` with `TARGET_DATE` in the next 90 days | as C1 |
| C3 | Lost opportunity | Σ `LOST_OPPORTUNITY_SCP` per `YEAR_MONTH`, by branch and SMCS group (trend) | the meaning of negative values is confirmed with the OLGA owner |
| C4 | Capture rate | Σ `SALES_SCP` ÷ Σ `BASE_SCP` per month (share of the expected parts and service spend that Mantrac actually sold) | as C3 |

## D. Cross-source (built only if the serial match is good)

| # | KPI | Definition | Gate |
|---|---|---|---|
| D1 | Fault-to-service-order conversion | Share of B2 assets with a Helios service order opened within 14 days of the fault | serial match telemetry ↔ `SERVICEORDER` ≥ 80 % of telemetry serials that have orders (Q14) |
| D2 | Overdue opportunities without an order | C1 opportunities with `TARGET_DATE` passed and no service order on the serial since the snapshot | serial match OLGA ↔ `SERVICEORDER` ≥ 80 % (Q14) |

## Decisions needed

1. Approve, change or drop each KPI above.
2. The aged-backlog threshold (A4): is 30 days right?
3. Which `SEVERITY` values count as "high" (B2, D1)? The value list comes from Q13.
4. Currency for C1–C4: local or USD (OLGA has both `PART_COST` and `PART_COST_USD`)?
5. The warehouse fix (catalog §0.2), so the gates can be measured.
