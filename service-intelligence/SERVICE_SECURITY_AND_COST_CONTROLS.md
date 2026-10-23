# Service Intelligence — security and cost controls

## 1. Snowflake roles and permissions required

All names are **proposals** for your Snowflake admins. The grants script is `sql/snowflake/00_roles_and_grants.sql` and the plan schema is `sql/snowflake/02_app_schema.sql`.

| Principal | Grants | Used for |
|---|---|---|
| `SI_READER` (application read role) | USAGE on warehouse `SI_WH`, database and schema; **SELECT on the contract view only** | Every dashboard, recommendation and assistant query |
| `SI_PLAN_WRITER` | USAGE on `SI_APP`; SELECT, INSERT and UPDATE on `RECOMMENDATIONS`; SELECT and INSERT on `RECOMMENDATION_EVENTS`; USAGE on `RECOMMENDATION_SEQ` | Plan workflow only. There is no DELETE, and the audit table cannot be updated. |
| `SI_VIEW_OWNER` | SELECT on the mapped source table; CREATE VIEW in the contract schema | Owns the SECURE contract view. The app roles never read the source table. |
| `SI_SVC` service user (`TYPE=SERVICE`) | Granted `SI_READER` and `SI_PLAN_WRITER`; key-pair auth; no password | The application |
| Discovery (one-off) | A role that can USAGE the candidate databases and read their INFORMATION_SCHEMA; SHOW WAREHOUSES | `python -m si.discover` |
| *Optional:* `SNOWFLAKE.CORTEX_USER` database role on `SI_READER` | Cortex functions | Only if `SI_CORTEX_ENABLED=1` |

**Optional AI capabilities.**

- **Snowflake Cortex `COMPLETE`.** Used only as a fallback question classifier. The default model is `mistral-large2` (the model this repository's Maia project already uses), set with `SI_CORTEX_MODEL`. Its availability depends on the account's region and the Cortex settings.
- **Cortex Analyst, Cortex Search and semantic views.** Not used. The metric registry already provides a governed semantic layer. A Cortex Analyst semantic model could be generated from `config/metrics.yaml` later if the account supports it.

## 2. Authentication and authorisation

- **No credentials in code or in the browser.**
  - Snowflake credentials come from the environment or a mounted key file (`SNOWFLAKE_PRIVATE_KEY_PATH`).
  - `prod` refuses password authentication.
  - The UI holds only an app token (in tab storage), or nothing at all behind SSO.
  - The frontend never connects to Snowflake.
- **App sign-in.**
  - `trusted_header` (recommended) reads identity and groups from the company's SSO reverse proxy, which must strip those headers from client requests.
  - `token` uses bearer tokens, stored only as SHA-256 hashes and compared in constant time (`python -m si.tokens new <user>`).
  - `dev` is refused outside `SI_ENV=dev`.
- **Server-side permissions per route:** `analysis.read`, `analysis.drilldown`, `chat.ask`, `plan.read`, `plan.add`, `plan.review`, `plan.approve`, `admin.read`. Roles are viewer, analyst, approver and admin (`config/users.example.yaml`).
- **Per-user data scope.** A user's scope (for example `region: [North]`) is added as a mandatory AND condition to every query that user causes, including drill-down, chat and recommendation generation. A filter can only narrow it. Plan items are visible only when their evidence lies inside the scope. Scoped users cannot save candidates generated for a different scope.
- **Snowflake policies still apply.** The contract view is a SECURE view over the source, so row-access and masking policies on the source table keep applying. They evaluate against the **service role**, not the end user. Per-user Snowflake enforcement (OAuth pass-through, or a SiS front end with caller's-rights objects) is **not implemented**. Until then, the app-level scope is the per-user control.

## 3. Query safety

- **Every statement passes `si/guard.py` before execution.** It must be:
  - exactly one statement;
  - SELECT or UNION only;
  - free of DDL, DML, CALL, USE, SET, GRANT, COPY, table functions, `SYSTEM$` functions, unknown functions and `SELECT *`;
  - only against approved fully qualified relations (the contract view);
  - only over approved columns, with mapped sensitive columns explicitly denied;
  - capped by a literal LIMIT ≤ `SI_MAX_ROWS` for any row-returning statement.
- **Values are bind parameters.** Server-side `qmark` binding is used for every value. Identifiers come only from the contract and registry.
- **The assistant cannot run SQL.** It maps questions to registry plans, and SQL typed into the chat is refused. Optional Cortex output is validated against registry ids, and the model never sees data rows.
- **Untrusted input.** User text and database content are treated as data. Answer text is template-composed, so stored text cannot inject instructions. The UI renders everything with `textContent`.
- **HTTP hardening:**
  - strict CSP (`default-src 'self'`, no inline script or style);
  - `X-Frame-Options: DENY`, `nosniff`, `no-referrer`;
  - `Cache-Control: no-store` on the API;
  - request size limits through pydantic;
  - OpenAPI docs disabled in prod.
- **No writes to company data.** The only write statements are the fixed plan-store statements, run on the writer role and touching the `SI_APP` tables only.

## 4. Cost and performance controls

| Control | Where |
|---|---|
| X-Small warehouse, `AUTO_SUSPEND=60`, `STATEMENT_TIMEOUT_IN_SECONDS=120`, queued timeout 60 s; optional resource monitor | `00_roles_and_grants.sql` |
| Session `STATEMENT_TIMEOUT_IN_SECONDS` (`SI_QUERY_TIMEOUT_S`, default 60) and a per-call timeout; `ABORT_DETACHED_QUERY` | `backends/snowflake.py` |
| Row cap (`SI_MAX_ROWS`), mandatory LIMIT, paginated drill-down (`SI_DRILLDOWN_PAGE`) | guard, executor, API |
| Result cache keyed by (SQL, binds); TTL `SI_CACHE_TTL_S` (default 300 s) | `executor.py` |
| Single-flight: concurrent identical queries share one execution | `executor.py` |
| UI: identical in-flight GETs de-duplicated; the analysis page reloads only when the period or filters change | `static/app.js` |
| Aggregates only on the dashboard; no `SELECT *`; every period query filters on its time column; the analysis "as of" time is the data refresh time, so the cache is not defeated by the clock | `query.py` |
| Recommendations and Cortex run only on an explicit user action (Generate / Ask); no background jobs | API, UI |
| Connection pool (4 reader connections), one writer connection, no keep-alive heartbeat | `backends/snowflake.py` |
| `QUERY_TAG='service-intelligence'` on every session, for `QUERY_HISTORY` attribution | `backends/snowflake.py` |
| Query log with query id, duration, rows and status, without bind values or secrets | `executor.py` → `data/query_log.jsonl`, `/api/admin/queries` |

**Not yet done.** Run these once the contract view exists:

- Inspect query profiles and `QUERY_HISTORY` for the contract view.
- Decide on clustering, or a materialised or dynamic table, for the view if the source is large. The current design scans the view per aggregate.
- Size the cache TTL to the real load frequency.

## 5. Failure handling

Warehouse errors are classified and reported in plain language, and no figures are shown when a query fails:

- suspended or unavailable warehouse (503);
- timeout (504);
- permission denied (502);
- connection failure (503).

Failures are never cached. A connection error triggers one reconnect.

## 6. Environments and data handling

- **Synthetic data stays out of prod.** `prod` refuses synthetic fixtures. Synthetic results are labelled in every response and in the UI. Automated tests use only synthetic data; live tests are read-only and opt-in (`RUN_SNOWFLAKE_TESTS=1`).
- **Discovery is read-only.** It runs metadata and aggregate queries only; it samples no rows and writes no company data. Its outputs (`discovery/`) are git-ignored.
- **Secrets stay out of git.** `.env`, `config/source_mapping.yaml` and `config/users.yaml` are git-ignored.
