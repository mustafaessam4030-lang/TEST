# Service Intelligence — discovery audit and status report

**Date:** 23 Oct 2026 · **Scope:** this repository (all branches) and the Snowflake integration available to this session.

## 0. Status, 24 Oct 2026 (latest)

**Scope.** Only the tables in `SERVICE_INTELLIGENCE` (Cat telematics and OLGA copies). **Architecture:** Streamlit in Snowflake,
in `snowflake_native/`.

**Checked in this account:**
- Streamlit in Snowflake is already in use (DEV_ADMIN owns several apps).
- Cortex is available: `PUBLIC` holds `CORTEX_USER`, and `claude-sonnet-4-6` is allow-listed.

| Item | Status | Evidence |
|---|---|---|
| Curated layer: 4 views + daily materialized tables + refresh task (`snowflake_native/sql/10_setup.sql`) | BUILT; SELECTs VERIFIED LIVE | Every view ran read-only on the live copies; counts reconcile with independent queries (snowflake_native/README.md) |
| Metric registry (10 KPIs: fleet health and opportunity) and SQL builder | TESTED; SQL VERIFIED LIVE | 11 compiled queries ran live |
| Page 1, Analysis: KPI cards with deltas, trend, breakdown, drill-down, definitions, freshness, evidence | BUILT, TESTED ON FAKE DATA | `tests/test_pages.py` renders the page; not yet deployed |
| Page 2, Recommendations: detector, transparent score, evidence, save, workflow, audit | BUILT, TESTED ON FAKE DATA | headline = registry metric for the segment (live: 427 = 427) |
| Page 3, Assistant: rules planner, optional Cortex (validated), grounded answers, refusals, chat log | BUILT, TESTED ON FAKE DATA | assistant SQL is asserted identical to the dashboard SQL |
| Deployment (objects, Streamlit app, grants) | BLOCKED: needs an owner to run `10_setup.sql` and deploy | the connector is read-only by design |
| Service-order KPIs (resolution, backlog, SLA) | OUT OF SCOPE | no service-order data in `SERVICE_INTELLIGENCE` |

Sections 1–7 below describe the earlier FastAPI design and the Helios check. They are kept for the record.

## 1. Bottom line

> **Update, 24 Oct 2026.** This section replaces the 23 Oct status. The original findings in §2–3 still describe the repository.

- **Snowflake is now reachable** through a read-only connector (role `DEV_ADMIN`, warehouse `CLAUDE_WH`). Discovery and profiling of
  the phase-1 sources are in SERVICE_DATA_CATALOG.md §0–0.1.
- **The first real source is mapped.** The Helios service-order table `MANDL_CDM_PROD.MANCDM.SERVICEORDER` fits the app's
  service-request contract. Its mapping is `config/source_mapping.helios.yaml`.
  - Soft deletes and placeholder dates are excluded.
  - There is no SLA target, so the SLA KPIs show as unavailable.
- **The app's own KPI SQL was run against live data.** The SQL compiled by `si/query.py` for 9 KPIs and 1 breakdown was executed
  read-only through the connector and matches independent hand-written queries on the raw table (§7).
- **The application process has not run against Snowflake.** That needs:
  1. the contract view created (`sql/snowflake/04_helios_contract_view.sql`; the connector is read-only and cannot run DDL);
  2. a service user with a key pair on the app host.

  Until both exist, the running app serves only SYNTHETIC data, and says so on every page.

## 2. What was inspected

| Area | Finding | Evidence |
|---|---|---|
| Current branch (`claude/start-work-d53i2b`) | Holds a web film, the ATLAS planning documents and the Engineering Knowledge Assistant (FastAPI and a static UI, PDF RAG). It does not use Snowflake. | `engineering-assistant/README.md` §Snowflake: "Snowflake is not used." |
| `claude/adf-snowflake-pipeline-b8gn6d` | An ADF framework that copies Oracle into Snowflake. | `sql/snowflake/01–05`, `adf/*` |
| | It targets `ORACLE_LANDING.RAW` and `.CTL`, warehouse `WH_ADF_LOAD`, role `ADF_LOADER`, with key-pair auth through Key Vault. | |
| | Its seeded sources (`SALES.CUSTOMERS`, `ORDERS`, `ORDER_LINE`, `INVOICE_LINE`, `HR.EMPLOYEES`) look like template examples: the Oracle host is `oracle-prod.contoso.local`. | |
| | Audit columns `_ADF_LOAD_TS`, `_ADF_RUN_ID` and `_ADF_SOURCE` can serve as `loaded_at`. | |
| `claude/daily-inspection-pipeline-j80qy5` | A pipeline for daily inspections: `RAW_INSPECTIONS` and `INSPECTIONS`, keyed on `INSPECTION_NUMBER`, with `STATUS`, `INSPECTION_DATE` and `SERIAL_NUMBER`. | `app/warehouse/schema.sql` |
| | Its database and schema come from env vars. Its real status values are unknown (`allowed_statuses: []`). | |
| | It has no close time or SLA field. | |
| `claude/dsp-data-requirements-4btg0s` | `RAW.DSP.DSP_ASSETS`: an all-VARCHAR asset snapshot. It is not service work. | `dsp_sync/snowflake_load.py` |
| `claude/maya-equipment-automation-yxs92l` | Maia in `MAIA_PROD.CORE`: equipment, parts and automation-run tables, plus Cortex `AI_COMPLETE` with `mistral-large2`. | `maia/sql/snowflake/*`, `maia/docs/18-snowflake-cortex.md` |
| | Its own status report says Snowflake was "only run against the in-memory store". | |
| `claude/maia-m6rhri` | Mentions `FLEET_TELEMETRY` ("service records per unit") in design copy only, with no DDL. Says "Snowflake is not connected". | `how-maia-works.html`, `control-tower/PLATFORM.md` |
| Business definitions | None for SLA, backlog or resolution time anywhere. The only "SLA" is `SOURCE_REGISTRY.SLA_MS`, a source-latency figure. | |
| Auth conventions | Snowflake: key pair, PAT, `externalbrowser` and env-var settings with `SecretStr`. `QUERY_TAG`, `TIMEZONE=UTC`. | `inspection-pipeline/app/warehouse/connection.py`, `maia/.../snowflake_config.py` |
| | App auth: bearer token (engineering-assistant); OIDC/RBAC (ATLAS control plane). | |
| Test conventions | DuckDB for SQL logic and `fakesnow`. Live tests gated by `RUN_SNOWFLAKE_TESTS=1`. | inspection and Maia tests |

**Candidates that may hold service data** (unconfirmed — discovery must decide):

- an inspection or job table loaded by the inspection pipeline;
- a work-order or service-order table, if the ADF framework is pointed at the real Oracle service module;
- whatever `FLEET_TELEMETRY` "service records" becomes.

None of these is assumed in code.

## 3. Decisions taken because of these findings

1. **Contract-first.** The application reads exactly one view.
   - Its shape is fixed in `si/contract.py`.
   - A reviewed YAML mapping names the real table and columns once.
   - `python -m si.mapping ddl` generates the view; the app never runs DDL.
   - A metric whose required columns are unmapped is shown as *Not available — needs X*. It is never estimated.
2. **Synthetic data is opt-in and labelled.** It appears only with `SI_BACKEND=fixture`, and `SI_ENV=prod` refuses it. Every API response carries `data_source.synthetic`; the UI shows a banner and every chat answer carries a note.
3. **Stack.** The repository's existing pattern is reused: Python, FastAPI and a static UI, as in the engineering assistant, ATLAS and Maia. Snowflake connection conventions are reused from the inspection pipeline and Maia. See SERVICE_INTELLIGENCE_ARCHITECTURE.md for why this was chosen over Streamlit in Snowflake.

## 4. Status of every feature

**Evidence levels.** **TESTED** means covered by the automated suite, run here on synthetic data. **BUILT, NOT RUN LIVE** means implemented and tested with mocks or fixtures, but never executed against Snowflake. **BLOCKED** means it needs something this session cannot get. **NOT IMPLEMENTED** means no code exists.

| Feature | Status | Notes |
|---|---|---|
| Metric registry (12 KPIs) and generated definitions doc | TESTED | Every KPI is checked against hand-computed fixture values (`tests/test_kpis.py`). |
| Query builder: parameterised SQL, filters, scope, time zones, null buckets | TESTED | Covers the Cairo period boundary and `(not set)` filtering. |
| SQL guard (read-only, approved objects and columns, LIMIT, no `*`, no system functions) | TESTED | 27 refusal cases; every registry query passes. |
| Executor: cache, single-flight, timeouts, row cap, query log | TESTED | The Snowflake timeout path is mocked; the DuckDB interrupt is real. |
| Source mapping validation, view generation, SLA policy, time-zone conversion, dedupe, row exclusions | TESTED | Runs on the fixture. The Helios view's SELECT was run live (§7); the `CREATE VIEW` DDL has not been executed (needs a role that can create it). |
| Snowflake backend: key pair / OAuth / SSO, qmark binds, session limits, error classes | BUILT, NOT RUN LIVE | Connection arguments and error mapping are tested with a mocked driver. |
| Discovery CLI: inventory, profile, mapping check | BUILT, NOT RUN LIVE | Tested with canned metadata and the fixture. |
| Page 1, Analysis: KPI cards, deltas, trend, breakdown, drill-down, data quality, freshness | TESTED | API tests and browser tests (Chromium). |
| Page 2, Recommendations: 7 detectors, transparent ranking, evidence with query ids, projections labelled | TESTED | Detectors find the patterns planted in the synthetic data. |
| Plan persistence: workflow, permissions, optimistic concurrency, append-only audit | TESTED (DuckDB) | Snowflake tables in `sql/snowflake/02_app_schema.sql`, not created live. |
| Page 3, Assistant: rules planner, 8 intents, follow-ups, sources, refusals | TESTED | Chat figures are asserted equal to dashboard figures. |
| Optional Cortex planner (`SNOWFLAKE.CORTEX.COMPLETE`) | BUILT, NOT RUN LIVE | Output validation and prompt fencing are tested with a fake. Off by default. |
| AuthN/AuthZ: token, trusted header, dev; roles; per-user data scope | TESTED | |
| Helios contract view and KPI SQL on live data | VERIFIED LIVE (read-only, via connector) | 9 KPIs and 1 breakdown, compiled by the app and reconciled with independent queries (§7). |
| Live Snowflake tests (`tests/test_snowflake_live.py`) | BLOCKED | Need the service user and the created view. |
| Real data catalog and profiles | DONE | SERVICE_DATA_CATALOG.md §0.1 (10 phase-1 tables plus SERVICEORDER, aggregate-only queries). |
| Confirmation of KPI definitions by the business | BLOCKED | Needs an owner decision; see SERVICE_KPI_PROPOSAL.md. There is no SLA target source. |
| Fleet-health (telemetry) and opportunity (OLGA) KPIs | NOT IMPLEMENTED | Need new contract views and metric sets; proposed in SERVICE_KPI_PROPOSAL.md, awaiting approval. |
| Free-form natural-language-to-SQL | NOT IMPLEMENTED (by design) | The guard is ready for it, but approved templates cover the stated questions. |
| Backlog history trend, business-hours durations, effort or cost estimates | NOT IMPLEMENTED | No source data for them (status history, calendar, cost). |
| Per-user Snowflake identity (OAuth pass-through so row-access policies see the end user) | NOT IMPLEMENTED | The app uses a service role plus app-level scope. See the security document. |
| Snowflake-side query log table, Streamlit-in-Snowflake packaging | NOT IMPLEMENTED | The query log is JSONL on the app host. |

## 5. Test results (this session)

The suite ran in this container (Linux, Python 3.13, DuckDB 2.0, Chromium for the browser tests). The suite itself uses no Snowflake; live checks are in §7.

```
python -m pytest tests            -> 163 passed, 3 skipped (live Snowflake tests, RUN_SNOWFLAKE_TESTS unset)
python -m pyflakes si tests       -> clean
```

Passing tests show the logic is consistent on synthetic data. They do not show the application works on company data, and nothing here should be read as production readiness.

## 6. What is needed from the company to go live

1. **Create the view and roles.** Run `sql/snowflake/04_helios_contract_view.sql` as ACCOUNTADMIN. Rename `SI_WH`, `SI_READER`, `SI_PLAN_WRITER`,
   `SI_VIEW_OWNER` and `SI_SVC` first if the admins prefer other names. Then run `02_app_schema.sql` for the plan tables.
2. **Create the service user.** Create `SI_SVC` with a key pair and store the private key in the app host's secret store.
   Then run `RUN_SNOWFLAKE_TESTS=1 python -m pytest tests/test_snowflake_live.py` and the mapping check
   (`python -m si.mapping check`).
3. **Have the service-operations owner decide** the open points in SERVICE_KPI_PROPOSAL.md:
   - order-type scope;
   - Oracle Fusion open status;
   - the 30-day ageing threshold;
   - the meaning of division codes G/E/X.
4. **Choose the hosting and sign-in integration:** a reverse proxy with SSO, using `trusted_header` mode, is recommended.

## 7. Live verification of the first vertical slice (24 Oct 2026)

**Method.** The app's `QueryBuilder` compiled each KPI with the Helios mapping:
- reporting period Q3 2026 (1 Jul – 1 Oct, UTC);
- `as_of` = latest load 2026-10-23 14:50:24 UTC.

Bind values were inlined, and the view name was replaced by the generated view SELECT (as a CTE), so the SQL is otherwise
unchanged. Each statement ran read-only through the connector. An independent query on the raw table, written without the
app, checked each value.

| KPI (app SQL) | Live value | Independent check |
|---|---|---|
| Requests created, Q3 2026 | 2,818 | 2,818 ✔ |
| Requests closed, Q3 2026 | 1,699 | 1,699 ✔ |
| Average resolution time | 1,583.7 h (66.0 days) | 66.0 days ✔ |
| Median resolution time | 672 h (28 days) | 28 days ✔ |
| 90th percentile resolution time | 4,440 h (185 days) | n/a |
| Open backlog | 2,305 | 2,308 orders with `ISOPEN`; the 3 without an `OPENDATE` are excluded by the view ✔ |
| Aged backlog (> 30 days) | 1,732 | 1,732 ✔ |
| Aged share of backlog | 75.1 % | n/a |
| Average backlog age | 89.4 days | n/a |
| SLA compliance, SLA breach rate, open past SLA | Not available (no `due_at`) | `PROMISEDENDDATE` is empty in every row |

**Evidence for the first recommendation.** 1,533 of the 2,305 open orders (66 %) come from Oracle Fusion, and almost every
Fusion order has stayed `O`. They are concentrated in stores 99 (490), 29 (151), 15 (104), 35 (90) and 9 (59). Store 1 has the largest
Helios backlog (838 open; 488 Helios, 350 Fusion).

This is a **data-feed hypothesis, not a measured operational backlog**: Fusion status updates may not be reaching SERVICEORDER.
The proposed action is to confirm the Fusion status feed with its owner before the backlog KPIs are published.
