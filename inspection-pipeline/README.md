# Inspection pipeline

A daily batch job that collects inspection/equipment records from the
authorised inspection web application and loads them into Snowflake:
first a RAW layer, then an analytics-ready `INSPECTIONS` table, with an audit
row in `PIPELINE_RUNS` for every execution.

```
Scheduler (cron / Task Scheduler / ADF / Container Apps Job)      no schedule in code
   │  python -m app.collect
   ▼
app.pipeline ── new run_id ── PIPELINE_RUNS (STARTED)
   │
   ▼
InspectionCollector ──► ApiInspectionCollector        (preferred: the app's own backend call)
   │                └─► PlaywrightInspectionCollector (fallback: normal browser automation)
   ▼  SourceRecord (untouched payload)
normalization (source.yaml mapping) ─► validation (Pydantic, duplicates)
   │ valid                                   │ invalid
   ▼                                         ▼
RAW_INSPECTIONS (append) ─MERGE─► INSPECTIONS    VALIDATION_ERRORS
   │
   ▼
PIPELINE_RUNS (SUCCESS / PARTIAL_SUCCESS / FAILED + counts)
```

## Status: what is known, what is not

This repository held no code for this site: no URL, endpoint, payload,
selector or login details. The brief rules out guessing any of these, so:

* **Everything source-specific lives in `config/source.yaml`.** That covers the
  endpoint, parameters, pagination, JSON paths, login labels, table headers and
  date formats. The committed template, `config/source.example.yaml`, has
  `<FILL_ME>` placeholders. The pipeline won't run until each one it needs is
  filled in, and the error lists exactly which are missing.
* **`python -m app.discovery` is the tool that fills them in.** It records the
  fetch/XHR calls the application makes while you use it normally, and
  pinpoints the endpoint and JSON path that hold a value you can see on screen.
* The collectors, validation, Snowflake layer, audit, retries, diagnostics
  and tests are all built. They are tested end-to-end against a **synthetic**
  fixture site (`tests/fixtures/site/`). Its field names (`inspectionNo`,
  `serialNo`, …) exist only for tests and say nothing about the real site.

## Setup

```bash
cd inspection-pipeline
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium        # skip if a matching Chromium is preinstalled
cp .env.example .env                          # fill in; never commit .env
cp config/source.example.yaml config/source.yaml
```

## Step 1: discover how the site loads its data (API first)

```bash
python -m app.discovery --probe SYW57101 --probe 30081769
```

A normal Chromium window opens. Log in as usual (MFA/SSO works because you
do it yourself), open the inspection page, search, and page through the
results. Then press Enter. Everything is written to `discovery/<timestamp>/`
(gitignored):

| File | Contents |
| --- | --- |
| `REPORT.md` | Candidate endpoints ranked by probe hits. Each shows its query params, JSON body and GraphQL operation name, every list of objects (`records_path` + keys), and suggested `records_path` / field paths for each probe value. |
| `requests.json` | Every captured call. Header **names** only, never values. Keys that look like credentials are masked, and so are sensitive query values. |
| `aria_snapshot.yaml` | The accessibility tree: the roles, labels and names needed by the Playwright fallback and the login section. |

Use it to fill in `config/source.yaml`:

1. **`auth`**: the login page path, the field labels, the submit button name,
   and a URL regex or text that only appears after login.
2. **`api`**: copy `method`, `path` and static `params` from the endpoint that
   returns inspections. Then set `records_path`, `fields.*`, and
   `filter_params` (the parameter the app sends when you search by S/N). Work
   out `pagination` by comparing the calls for page 1 and page 2.
3. If no usable endpoint exists, set **`browser`** (headers exactly as the table
   shows them) and set `COLLECTOR_TYPE=playwright`.

Check your config without touching Snowflake:

```bash
python -m app.collect --dry-run --output out/records.jsonl
```

## Step 2: create the Snowflake objects, then run

```bash
python -m app.collect --init-schema            # idempotent: tables + views
python -m app.collect                          # one full run
python -m app.collect --serial-number SYW57101 # ad-hoc filtered run
```

| Exit code | Meaning |
| --- | --- |
| `0` | SUCCESS, or skipped (`--skip-if-succeeded-today` and today already succeeded) |
| `2` | PARTIAL_SUCCESS: some records failed validation, or the source reported more records than were collected |
| `1` | FAILED: a configuration, authentication, source, pagination or Snowflake error, or no valid records |

## Collectors

**API (`COLLECTOR_TYPE=api`, preferred).** Calls the backend endpoint that the
web app itself calls, as recorded by discovery.

* Pagination can be `none`, `page`, `offset` or `cursor`, in the query or the
  body. Guards stop runaway paging (`max_pages`, cursor-loop detection), and a
  later page that fails raises a `PaginationError` instead of silently
  truncating the results.
* Only safe failures are retried, with bounded exponential backoff and jitter:
  timeouts, network errors, 429 and 5xx (a `Retry-After` header is honoured up
  to 120 s). 4xx errors, malformed JSON and an unexpected response shape fail
  immediately.
* A 401, or a redirect to the login page, counts as an expired session. The
  collector re-authenticates **once**, and a second rejection raises
  `SessionExpiredError`. A 403 raises `AuthorizationError` and is never retried.

**Playwright (`COLLECTOR_TYPE=playwright`, fallback).** One stock Chromium
session per run: log in, open the page, search, read the table, click *Next*,
repeat.

* Elements are found only by role, label or accessible name.
* Cells are read by **column header text**, so a column re-order doesn't break
  extraction, but a renamed or removed column fails with a `PageStructureError`
  that lists what the table does show.
* Links inside the attachments column become attachment metadata
  (`name`, `url`).
* There are no stealth plugins, no fingerprint changes and no CAPTCHA handling.

**Authentication (`AUTH_MODE`).**

* `browser_session` logs in through the real login form. The resulting cookies
  are passed to the HTTP client **in memory only**, which is what the web app
  itself does.
* `bearer_token` uses a token issued to the service account
  (`WEBSITE_API_TOKEN`).
* `none` uses no authentication.

**Failure diagnostics.** When a browser step fails, the collector saves
`DIAGNOSTICS_DIR/<run_id>/<timestamp>_<stage>.png` and `.json`. The JSON holds
the URL with query values masked, the page title, the error, the timestamp,
the run_id and the stage. Password inputs are cleared before the screenshot
is taken.

## Data model and validation

`SourceRecord` holds the untouched payload returned by a collector.
`Inspection` (Pydantic) is the typed record that gets loaded:

`run_id, source, collector_type, inspection_number, serial_number, status,
summary{counter:int>=0}, attachments[{name,url,...}], inspection_date,
collected_at, raw_data`, plus a derived `record_hash` (SHA-256 of the
content, excluding run_id and collected_at).

Checks:

* Required identifiers must be non-blank.
* Timestamps must parse (ISO-8601, epoch seconds or milliseconds, or the
  formats in `date_formats`). Naive values are read in `SOURCE_TIMEZONE`.
  Dates in the future or before 1990 are rejected.
* Summary counts must be integers of zero or more.
* Each attachment needs a name or a URL.
* Optionally, the status must be in `allowed_statuses`.
* Completeness: the collected count is compared with the source's reported
  total (`total_path`), and `MIN_EXPECTED_RECORDS` sets a floor.

Every input record ends up in exactly one bucket: **valid**, **failed** (a
`VALIDATION_ERRORS` row with run_id, record identifier, error type, message,
raw payload and timestamp) or **identical duplicate** (counted). If the same
business key appears twice in one run with *different* content, the first
occurrence is kept and the second is recorded as `DUPLICATE_CONFLICT`.

## Snowflake

DDL: [`app/warehouse/schema.sql`](app/warehouse/schema.sql) and
[`app/warehouse/views.sql`](app/warehouse/views.sql).

| Object | Grain | Purpose |
| --- | --- | --- |
| `PIPELINE_RUNS` | one row per run | RUN_ID, SOURCE, COLLECTOR_TYPE, FILTERS, START/END_TIME, STATUS, RECORDS_FOUND/VALID/LOADED/FAILED/DUPLICATE/INSERTED/UPDATED/UNCHANGED, EXPECTED_TOTAL, ERROR_MESSAGE, CREATED/UPDATED_AT |
| `RAW_INSPECTIONS` | inspection × run (append-only) | RUN_ID, SOURCE, COLLECTED_AT, INSPECTION_NUMBER, SERIAL_NUMBER, STATUS, INSPECTION_DATE, SUMMARY, ATTACHMENTS, ATTACHMENT_COUNT, RECORD_HASH, **RAW_JSON** (VARIANT), CREATED/UPDATED_AT |
| `INSPECTIONS` | one row per (SOURCE, INSPECTION_NUMBER) | current state + FIRST_SEEN_RUN_ID/AT, LAST_SEEN_RUN_ID/AT, LAST_CHANGED_RUN_ID, CREATED/UPDATED_AT |
| `VALIDATION_ERRORS` | one row per rejected record | RUN_ID, RECORD_IDENTIFIER, ERROR_TYPE, ERROR_MESSAGE, RAW_JSON, FAILED_AT |
| `V_PIPELINE_DAILY_STATUS` | source × day | latest run of the day, its counts, and RUNS_THAT_DAY |
| `V_INSPECTION_LINEAGE` | inspection | which runs first loaded, last changed and last saw each inspection |
| `V_VALIDATION_ERRORS_BY_RUN` | run × error type | failure counts with an example message |

All timestamps are `TIMESTAMP_TZ` and written in UTC. The session is opened
with `TIMEZONE=UTC` and `QUERY_TAG=inspection-pipeline:<run_id>`, so the
warehouse's query history is traceable to a run as well.

### Idempotency: the MERGE strategy

**Business key:** `(SOURCE, INSPECTION_NUMBER)`. An inspection number
identifies one inspection. A serial number identifies a machine, which has
many inspections, so it can't be the key. `run_id` is also not a key: it is
kept for auditing only. Once discovery shows the real data, check that
inspection numbers really are unique in the source. If they aren't, the key
must be widened.

Each run does the following in **one transaction** (`BEGIN … COMMIT`, with a
`ROLLBACK` if anything fails):

1. Append the run's validated rows to `RAW_INSPECTIONS`, using batched
   multi-row `INSERT … SELECT … FROM (VALUES …)` statements. Each batch holds
   up to 500 rows and stays under about 700 KB, and `PARSE_JSON` fills the
   VARIANT columns.
2. `MERGE INTO INSPECTIONS` from this run's RAW rows:
   * not matched → **INSERT**, with FIRST_SEEN, LAST_SEEN and LAST_CHANGED set to this run
   * matched and `RECORD_HASH` differs → **UPDATE** the content, LAST_CHANGED_RUN_ID and UPDATED_AT
   * matched and hash equal → only LAST_SEEN_RUN_ID and LAST_SEEN_AT move

As a result, reruns, double scheduler fires and daily re-collection of the
same inspections never create duplicate business records. `UPDATED_AT` only
changes when the source data changed, and RAW keeps the full history of what
was seen in each run. The MERGE SQL is executed **verbatim** against DuckDB in
the test-suite (`tests/integration/test_warehouse_duckdb.py`).

### Observability queries

```sql
-- Did today's run happen, when, and how did it go?
SELECT * FROM V_PIPELINE_DAILY_STATUS
WHERE SOURCE = 'website' AND RUN_DATE = CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE;

-- Alert: no successful run in the last 26 hours
SELECT COUNT(*) = 0 AS ALERT FROM PIPELINE_RUNS
WHERE SOURCE = 'website' AND STATUS = 'SUCCESS'
  AND START_TIME >= DATEADD('hour', -26, CURRENT_TIMESTAMP());

-- Why did a run fail or partially fail?
SELECT STATUS, ERROR_MESSAGE FROM PIPELINE_RUNS WHERE RUN_ID = '<run_id>';
SELECT * FROM VALIDATION_ERRORS WHERE RUN_ID = '<run_id>';

-- Which run(s) produced a record?
SELECT RUN_ID, COLLECTOR_TYPE, COLLECTED_AT, RECORD_HASH
FROM RAW_INSPECTIONS WHERE INSPECTION_NUMBER = '30081769' ORDER BY COLLECTED_AT;
SELECT * FROM V_INSPECTION_LINEAGE WHERE INSPECTION_NUMBER = '30081769';
```

For Power BI, use `INSPECTIONS` (or `V_INSPECTION_LINEAGE`). Once the real
counter names are known, turn `SUMMARY` into columns in a view, for example
`SUMMARY:critical::INT AS CRITICAL`.

## Daily scheduling

The code has no schedule. Each invocation performs one stateless run with a
new run_id. The scheduler should:

* run it **once a day**
* alert when the exit code isn't 0
* optionally retry once on failure (with `--skip-if-succeeded-today`, a retry
  after a success does nothing)

| Scheduler | How |
| --- | --- |
| **cron** (Linux VM) | `15 5 * * * /opt/inspection-pipeline/scripts/run_daily.sh >> /var/log/inspection-pipeline.log 2>&1`. The script uses `flock` to prevent overlapping runs. |
| **Windows Task Scheduler** | `scripts/run_daily.ps1` has the `Register-ScheduledTask` commands (daily, `MultipleInstances IgnoreNew`, 2 h limit). |
| **Azure (recommended for production)** | Build the `Dockerfile` (official Playwright image) and run it as an **Azure Container Apps Job** with a schedule trigger (`15 5 * * *`) and `replicaTimeout`. Secrets come from Key Vault references as environment variables. If orchestration must live in **Azure Data Factory**, use an ADF *schedule trigger* (daily) whose pipeline starts the job, via a Web activity to the Container Apps Job start API or an Azure Batch Custom activity running the same image. Either way ADF only schedules; the code stays scheduler-agnostic. |

Collect logs from stdout. Set `LOG_FORMAT=json` for Log Analytics or Splunk.

## Configuration

Everything is read from environment variables (or `.env` in development);
see [`.env.example`](.env.example).

* **Secrets** (`WEBSITE_PASSWORD`, `WEBSITE_API_TOKEN`, `SNOWFLAKE_PASSWORD`,
  key passphrase) are stored as `SecretStr`, which never appears in a
  `repr()`. They are also registered with the log redactor, which masks them,
  along with bearer tokens and `password=` / `token=` / `cookie=` patterns, in
  every log line and stored error message.
* **Snowflake** supports password, key-pair (`SNOWFLAKE_PRIVATE_KEY_PATH`,
  recommended for service users) and `SNOWFLAKE_AUTHENTICATOR`.
* **Source definition:** `SOURCE_CONFIG_PATH` points at `config/source.yaml`.
  It is not secret and should be committed once it is filled in.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q                                  # 81 passed, 1 skipped
RUN_SNOWFLAKE_TESTS=1 pytest -m snowflake  # optional, against a disposable schema
```

No production credentials are needed.

| Area | Test file |
| --- | --- |
| API parsing, all pagination modes, filters, POST bodies, retries and their limits, 401 → re-auth once, 403, 404, non-JSON, malformed JSON, wrong shape | `tests/unit/test_api_collector.py` |
| Pydantic validation, failure recording, duplicate detection, record hash | `tests/unit/test_validation.py` |
| Field mapping and date parsing | `tests/unit/test_normalization.py` |
| Placeholder detection in `source.yaml` | `tests/unit/test_source_config.py` |
| Retry and backoff | `tests/unit/test_retry.py` |
| Snowflake connect retry and error classification | `tests/unit/test_snowflake_connection.py` |
| Secret redaction, run_id in logs, discovery helpers | `tests/unit/test_logging_and_discovery.py` |
| RAW insert, MERGE insert/update/unchanged, rollback, run audit (real SQL on DuckDB) | `tests/integration/test_warehouse_duckdb.py` |
| Whole pipeline: success, idempotent rerun, partial, incomplete, all-invalid, collector/Snowflake/load failures, skip-if-succeeded, dry run, CLI exit codes | `tests/integration/test_pipeline.py` |
| Real Chromium on the fixture site: login, extraction, pagination, attachments, search, bad credentials with diagnostics and no leaked secret, changed page structure, API collector with browser-session cookies | `tests/integration/test_browser.py` |
| Discovery run against the fixture site | `tests/integration/test_discovery_fixture.py` |

## Known limitations

* **Real endpoint, fields and selectors are unknown until discovery is run**
  against the actual application with an authorised account.
* Interactive MFA/SSO can't be automated, by design. Use a service account
  without interactive MFA, or a token issued for automation
  (`AUTH_MODE=bearer_token`).
* Login forms that aren't label-based, or tables that aren't `table`/`grid`,
  would need small collector changes. Virtualised grids that render only the
  visible rows are not handled by the table reader, and the API collector is
  the right choice for those.
* Attachment content isn't downloaded, only its metadata.
* Records that disappear from the source aren't deleted from `INSPECTIONS`.
  `LAST_SEEN_RUN_ID` shows when a record was last seen.
* A failed run is reported through the exit code and logs, but only after
  Snowflake is reachable is it also written to `PIPELINE_RUNS`.
