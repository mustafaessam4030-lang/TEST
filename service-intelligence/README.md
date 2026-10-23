# Service Intelligence

The app has three pages, all built on one governed Snowflake view and one metric registry:

- **Analysis:** KPIs, trends, breakdowns, drill-down to records, data quality and freshness.
- **Recommendations:** an evidence-backed, ranked plan with a review and approval workflow and an audit trail.
- **AI Assistant:** answers business questions with the same metric definitions, showing its sources.

> **Current state.** This has been built and tested on **SYNTHETIC data only**. Snowflake was not reachable when it was built, so it has **not** been validated against company data. See [SERVICE_INTELLIGENCE_AUDIT.md](SERVICE_INTELLIGENCE_AUDIT.md) for what is tested, blocked and not implemented.

| Document | Contents |
|---|---|
| [SERVICE_INTELLIGENCE_AUDIT.md](SERVICE_INTELLIGENCE_AUDIT.md) | Discovery findings, feature status, test results, what is needed to go live |
| [SERVICE_DATA_CATALOG.md](SERVICE_DATA_CATALOG.md) | Known Snowflake objects, the data contract, the synthetic dataset, how to catalogue real data |
| [SERVICE_METRIC_DEFINITIONS.md](SERVICE_METRIC_DEFINITIONS.md) | Every KPI (generated from `config/metrics.yaml`) |
| [SERVICE_INTELLIGENCE_ARCHITECTURE.md](SERVICE_INTELLIGENCE_ARCHITECTURE.md) | Design decision, components, assistant pipeline, recommendation engine |
| [SERVICE_SECURITY_AND_COST_CONTROLS.md](SERVICE_SECURITY_AND_COST_CONTROLS.md) | Snowflake roles and permissions, optional AI capabilities, query safety, cost controls |

## Run locally on synthetic data

This needs Python 3.11 or later.

```bash
cd service-intelligence
pip install -r requirements-dev.txt
python -m si.api                      # http://127.0.0.1:8780  (SI_ENV=dev, SI_BACKEND=fixture, SI_AUTH_MODE=dev)
```

Every page shows a yellow **SYNTHETIC** banner in this mode.

## Tests

```bash
python -m pytest tests                # unit, API and browser tests; all on synthetic data
python -m pyflakes si tests
SI_E2E_CHROMIUM=/path/to/chrome python -m pytest tests/test_ui_e2e.py   # browser tests (skipped if Chromium is absent)
RUN_SNOWFLAKE_TESTS=1 SI_BACKEND=snowflake ... python -m pytest tests/test_snowflake_live.py   # read-only, live
```

## Connect to Snowflake

1. **Credentials.** Have an admin create a service user with key-pair auth, and a discovery role (`sql/snowflake/00_roles_and_grants.sql`, sections 1, 2 and 6). Keep the private key in your secret store.
2. **Discover.** Set `SI_BACKEND=snowflake`, the `SNOWFLAKE_*` variables (see `.env.example`) and a provisional `SI_CONTRACT_VIEW`, then run:
   ```bash
   python -m si.discover inventory
   python -m si.discover profile DB.SCHEMA.TABLE --id .. --created .. --status .. --columns ..
   ```
   Then update `SERVICE_DATA_CATALOG.md` with the output.
3. **Map.** Copy `config/source_mapping.example.yaml` to `config/source_mapping.yaml`. Fill it in and review it with the data owner, then run `python -m si.mapping check`.
4. **Create the view and plan tables.** Run `python -m si.mapping ddl` and have the view owner execute the output. Then run `sql/snowflake/02_app_schema.sql` and the remaining grants.
5. **Confirm definitions.** The service-operations owner confirms the KPI definitions, the status classes, the SLA source and the `aged_days` threshold.
6. **Run the live tests:** `RUN_SNOWFLAKE_TESTS=1 python -m pytest tests/test_snowflake_live.py`.

## Deploy

These are the minimum settings for production:

```bash
SI_ENV=prod SI_BACKEND=snowflake SI_AUTH_MODE=trusted_header \
SI_CONTRACT_VIEW=<SI_DB>.<SERVICE_INTEL>.V_SERVICE_REQUESTS SI_MAPPING_PATH=/etc/si/source_mapping.yaml \
SI_USERS_PATH=/etc/si/users.yaml SI_APP_SCHEMA=<SI_DB>.SI_APP SI_SNOWFLAKE_WRITER_ROLE=<SI_PLAN_WRITER> \
SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=<SI_SVC> SNOWFLAKE_ROLE=<SI_READER> SNOWFLAKE_WAREHOUSE=<SI_WH> \
SNOWFLAKE_PRIVATE_KEY_PATH=/run/secrets/si_key.p8 SI_HOST=127.0.0.1 SI_PORT=8780 \
uvicorn --factory si.api:create_app --host 127.0.0.1 --port 8780 --workers 1
```

- **Run it behind the company reverse proxy.** The proxy terminates TLS, performs SSO, and sets `X-Forwarded-User` and `X-Forwarded-Groups`. It must strip any client-supplied copies of those headers.
- **Use one worker per process.** The candidate cache and conversation context live in process memory. To run several processes, use sticky sessions.
- **Separate environments.** dev, test and prod each need their own `SI_USERS_PATH`, mapping, Snowflake role and app schema.
- **Optional AI planner.** Set `SI_CORTEX_ENABLED=1` after granting `SNOWFLAKE.CORTEX_USER` and confirming Cortex availability for the account's region.

## Layout

```
si/            application (api, auth, analysis, recommend, plans, chat/, query, guard, executor, backends/, discover, mapping)
si/static/     the UI (no build step, no CDN)
config/        metrics.yaml (registry), mapping examples, users example
sql/snowflake/ roles, grants, plan schema (the contract view DDL is generated)
tests/         synthetic-data tests; live tests gated by RUN_SNOWFLAKE_TESTS
```
