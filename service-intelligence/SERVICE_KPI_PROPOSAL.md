# Service KPI proposal (phase 1): awaiting approval

**Status: PROPOSED, 23 Oct 2026; gates measured.** Nothing here is in `config/metrics.yaml` yet. Approved KPIs will be added to the
registry, which regenerates `SERVICE_METRIC_DEFINITIONS.md`, and will get hand-computed tests like the existing ones.

**How to read the tables.**
- **Gate** is the data-quality condition each KPI needed. The figures come from the profile in `SERVICE_DATA_CATALOG.md` §0.1.
- **Preview** is a single measured figure to show scale. It is not a reported KPI value.

## A. Service orders (`MANDL_CDM_PROD.MANCDM.SERVICEORDER`)

**Rules for every A-KPI:**
- exclude soft deletes (`ROWOPERATION <> 'DELETE'`, 11 % of rows);
- exclude `OPENDATE` before 2000;
- closed means status I (Invoiced) or C (Closed).

**Scope to approve:** all order types, or Revenue only (66 %; Internal 29 %, Expenses 5 %).

| # | KPI | Definition | Gate → result | Preview |
|---|---|---|---|---|
| A1 | Service orders opened | Distinct `SERVICEORDERID` with `OPENDATE` in the period | `OPENDATE` < 2 % null → **pass** (0.00 %; 2 rows from 1925 excluded) | 400–1,200 a month in 2026 |
| A2 | Service orders closed | Distinct orders with `LASTCLOSEDATE` in the period and status I/C | statuses map cleanly → **pass** (I/O/C, plus 1 row with M) | n/a |
| A3 | Open backlog | Orders with `ISOPEN`, as of the last refresh | **conditional**: nearly all Oracle Fusion orders show as open | 2,326 open |
| A4 | Aged backlog | Open orders older than 30 days | as A3 | 1,848 > 30 days (48 > 1 year) |
| A5 | Days to close (median, P90) | `LASTCLOSEDATE − OPENDATE` for orders closed in the period | close-before-open < 1 % → **pass** (0 rows) | median 19 days; P90 about 99–136 days for Revenue orders |
| A6 | On-time start rate | Share of orders with `FIRSTACTIVITYDATE` on or before `PROMISEDSTARTDATE` | promised start filled on ≥ 60 % → **pass** (≈100 %, and it is a real date, not the open date) | 92.0 % over the last 12 months |

**Dropped:**
- On-time *completion*: `PROMISEDENDDATE` is empty in every row.
- Breakdown by repair type: `REPAIRTYPE` is empty.

**Breakdowns instead:** store, division, priority and order type.

## B. Fleet health (Cat telemetry)

**Rules for every B-KPI:**
- de-duplicate fault events on asset+ECU+codes+occurrence time (37 % of FAULT_CODE_HISTORY rows and 3.5 % of FAULT_CODE_BASIC_DAILY rows repeat);
- `VALID_FLAG = 'V'`;
- one reading per asset per day;
- drop sentinels and out-of-range values: fuel 4,294,967,295; SMU > 200,000 h; idle > total; dates more than 1 day in the future.

| # | KPI | Definition | Gate → result | Preview |
|---|---|---|---|---|
| B1 | Connected assets reporting | Distinct serials with an SMU reading in the last 7 days | timestamps convert ≥ 99 % → **pass** (100 %) | 9,883 assets |
| B2 | High-severity fault assets | Distinct assets with ≥ 1 `SEVERITY = 'HIGH'` fault event (FAULT_CODE_HISTORY) in the period | severity scale known → **pass** (LOW/MEDIUM/HIGH; HIGH = 9 % of rows) | FAULT_CODE_HISTORY covers about 2,100 assets a month |
| B3 | Fault events per 1,000 operating hours | **Changed:** distinct fault events ÷ SMU-hour increase × 1,000, by model. Σ `OCCURRENCE_COUNT` is not used because it saturates at 255 in 11 % of rows | numbers convert ≥ 99 % → **pass** (100 %), with the cleaning rules | n/a |
| B4 | Repeat faults | Assets where the same code (SPN+FMI, or CID+FMI) recurs within 30 days of an earlier event | as B2 → **pass** | n/a |
| B5 | Idle time % | Idle-hour increase ÷ SMU-hour increase | **pass, partial coverage**: idle hours present on 49 % of readings | n/a |
| B6 | Idle fuel % | Idle-fuel increase ÷ cumulative-fuel increase | **pass, partial coverage**: idle fuel on 74 %, cumulative fuel on 88 % of readings | n/a |

**Caution.** Fault-row volume doubled from March 2026 with a flat asset count. B3/B4 trends across that date should wait until Cat explains it.

## C. Parts & service opportunity (OLGA, already in USD)

| # | KPI | Definition | Gate → result | Preview |
|---|---|---|---|---|
| C1 | Open opportunity value | Σ `TOTAL_VALUE` in the latest snapshot with `TARGET_DATE` ≥ snapshot month-end, excluding the 2099-12-31 placeholder; by branch and SMCS group | `OPPORTUNITY_KEY` unique → **pass** | Σ all rows 3.61bn USD; 55 % of rows are already past their target date (rule to approve) |
| C2 | Opportunities due next 90 days | Count and Σ `TOTAL_VALUE` with `TARGET_DATE` in the next 90 days | as C1 → **pass** | 493,936 opportunities, 124.9M USD |
| C3 | Lost opportunity | Σ `LOST_OPPORTUNITY_SCP` per `YEAR_MONTH`, by branch and SMCS group | meaning of negative values → **pass**: lost = opportunity − sales exactly, so negative means sold above forecast | 20–34M USD a month until Jun 2026, **12.7–15.0M for Jul–Sep 2026** (confirm that recent months are final) |
| C4 | Capture rate | **Changed:** Σ `SALES_SCP` ÷ Σ `OPPORTUNITY_SCP` per month (consistent with C3) | as C3 → **pass** | n/a |

## D. Cross-source (joined on serial number)

| # | KPI | Definition | Gate → result |
|---|---|---|---|
| D1 | Fault-to-service-order conversion | Share of B2 assets with a Helios order opened within 14 days of the fault | serial match ≥ 80 % → **fail as specified**: only 21.5 % of telemetry assets ever appear in SERVICEORDER (3,617 serials). **Proposal:** restrict the denominator to assets with Helios service history in the last 24 months, and label it "serviced fleet only". |
| D2 | Overdue opportunities without an order | C1 opportunities past `TARGET_DATE` with no service order on the serial since the snapshot | OLGA ↔ SERVICEORDER ≥ 80 % → **pass for Cat machines** (90.4 %; 79.4 % across all brands) |

## Decisions needed

1. Approve, change or drop each KPI.
2. A-KPIs: all order types, or Revenue only?
3. Oracle Fusion orders: are they really open? Ask the Helios/Fusion owner, or exclude them from A3/A4 until confirmed.
4. The aged-backlog threshold (A4): is 30 days right?
5. C1: open opportunities include past-due ones, or only `TARGET_DATE` ≥ snapshot?
6. D1: accept the "serviced fleet only" denominator?

Settled by the data: "high severity" means `SEVERITY = 'HIGH'`. **Correction (24 Oct):** only the part-cost table is confirmed as USD (`PART_COST` = `PART_COST_USD`). `TOTAL_VALUE` and the `*_SCP` columns carry no currency, so the OLGA owner must confirm it.

> **Scope update, 24 Oct 2026.** The business owner limited the project to the tables in `SERVICE_INTELLIGENCE` and a Snowflake-native build. Section A (Helios service orders) and section D are out of scope. Sections B and C are implemented in `snowflake_native/` (see its README).
