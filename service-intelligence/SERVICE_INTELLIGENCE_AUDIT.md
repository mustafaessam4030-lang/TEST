# Service Intelligence — discovery audit and status report

**Date:** 23 Oct 2026 · **Scope:** this repository (all branches) and the Snowflake integration available to this session.

## 1. Bottom line

- **Snowflake could not be reached from this session.** The Snowflake connector (MCP) was configured but failed to connect (`ProtocolError`), and the environment holds no Snowflake credentials (`SNOWFLAKE_*` variables are absent). No metadata query, profile or KPI ran against company data.
- **The repository contains no service-request, work-order, case or repair table definition.** All Snowflake account identifiers in the repository are placeholders (`myorg-myaccount`, `xy12345…`, `YOUR_ACCOUNT_IDENTIFIER`).
- So the application is built against an explicit **data contract** (one governed view). It is developed and tested on **clearly labelled SYNTHETIC data** that runs the same Snowflake SQL through DuckDB. Connecting it to real data needs four things, all built and tested here:
  1. run discovery;
  2. write and review the source mapping;
  3. apply the generated view;
  4. set the environment.
- **Nothing in this repository has been validated against live Snowflake.** Every figure shown by the running application today is synthetic, and the UI says so on every page.

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
| Source mapping validation, view generation, SLA policy, time-zone conversion, dedupe | TESTED | Runs on the fixture. The generated Snowflake DDL has not been executed in Snowflake. |
| Snowflake backend: key pair / OAuth / SSO, qmark binds, session limits, error classes | BUILT, NOT RUN LIVE | Connection arguments and error mapping are tested with a mocked driver. |
| Discovery CLI: inventory, profile, mapping check | BUILT, NOT RUN LIVE | Tested with canned metadata and the fixture. |
| Page 1, Analysis: KPI cards, deltas, trend, breakdown, drill-down, data quality, freshness | TESTED | API tests and browser tests (Chromium). |
| Page 2, Recommendations: 7 detectors, transparent ranking, evidence with query ids, projections labelled | TESTED | Detectors find the patterns planted in the synthetic data. |
| Plan persistence: workflow, permissions, optimistic concurrency, append-only audit | TESTED (DuckDB) | Snowflake tables in `sql/snowflake/02_app_schema.sql`, not created live. |
| Page 3, Assistant: rules planner, 8 intents, follow-ups, sources, refusals | TESTED | Chat figures are asserted equal to dashboard figures. |
| Optional Cortex planner (`SNOWFLAKE.CORTEX.COMPLETE`) | BUILT, NOT RUN LIVE | Output validation and prompt fencing are tested with a fake. Off by default. |
| AuthN/AuthZ: token, trusted header, dev; roles; per-user data scope | TESTED | |
| Live Snowflake tests (`tests/test_snowflake_live.py`) | BLOCKED | Need credentials and the contract view. |
| Real data catalog and profiles | BLOCKED | Need connectivity. Run `python -m si.discover inventory`. |
| Confirmation of KPI definitions and SLA targets by the business | BLOCKED | Needs an owner decision. |
| Free-form natural-language-to-SQL | NOT IMPLEMENTED (by design) | The guard is ready for it, but approved templates cover the stated questions. |
| Backlog history trend, business-hours durations, effort or cost estimates | NOT IMPLEMENTED | No source data for them (status history, calendar, cost). |
| Per-user Snowflake identity (OAuth pass-through so row-access policies see the end user) | NOT IMPLEMENTED | The app uses a service role plus app-level scope. See the security document. |
| Snowflake-side query log table, Streamlit-in-Snowflake packaging | NOT IMPLEMENTED | The query log is JSONL on the app host. |

## 5. Test results (this session)

The suite ran in this container (Linux, Python 3.13, DuckDB 2.0, Chromium for the browser tests). There was no Snowflake.

```
python -m pytest tests            -> 156 passed, 3 skipped (live Snowflake tests, RUN_SNOWFLAKE_TESTS unset)
python -m pyflakes si tests       -> clean
```

Passing tests show the logic is consistent on synthetic data. They do not show the application works on company data, and nothing here should be read as production readiness.

## 6. What is needed from the company to go live

1. **Restore the Snowflake connector, or provide a service user**, using key pair (preferred), OAuth or SSO. The role must be able to read metadata in the candidate databases. Then run `python -m si.discover inventory`.
2. **Agree which table holds service requests or work orders**, and who owns it.
3. **Have the service-operations owner confirm or adjust:**
   - the status-class mapping;
   - the SLA target source (a column or an approved policy);
   - the 30-day ageing threshold;
   - the KPI definitions in SERVICE_METRIC_DEFINITIONS.md.
4. **Have a Snowflake admin approve** the roles, warehouse, resource monitor and SI_APP schema in `sql/snowflake/`. If the AI planner is wanted, they must also confirm Cortex availability for the account's region.
5. **Choose the hosting and sign-in integration:** a reverse proxy with SSO, using `trusted_header` mode, is recommended.
