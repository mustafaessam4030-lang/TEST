# Service metric definitions

Generated from `config/metrics.yaml` (registry version 2026.10.1) by `python -m si.metric_docs`.
Do not edit by hand; `tests/test_docs.py` fails if this file and the registry disagree.

These are the only KPI calculations in the application. The Analysis page, the
recommendation engine and the AI Assistant all compile them through
`si/query.py`, so the same question returns the same number everywhere.

**Status of these definitions.** No existing company KPI definitions were found
in the repository or in any accessible Snowflake object (see
SERVICE_INTELLIGENCE_AUDIT.md). These are proposed, conventional definitions over
the data contract below. Each needs confirmation from the service-operations
owner before it is reported as an official KPI.

## Conventions

- **Data source.** One governed view (`SI_CONTRACT_VIEW`) with the contract columns below, generated from a reviewed source mapping.
- **Time.** Contract timestamps are UTC (`TIMESTAMP_NTZ`). Reporting periods are whole days in the reporting time zone (`SI_REPORTING_TZ`); start inclusive, end exclusive. Trend buckets are truncated in the reporting time zone.
- **Period vs current-state.** *Period* metrics count records whose time column falls in the period, and are compared with the previous period of equal length. *Current-state* metrics describe open work as of the last data refresh (`as_of` = latest load time, else latest source update, else latest creation time). They have no history, because the source has no status history.
- **Nulls.** A record with a null dimension value is grouped as `(not set)`; it is never dropped silently. Records with a close time earlier than their creation time are excluded from duration metrics and counted under data quality.
- **Duplicates.** The contract view keeps one row per `request_id` when the mapping defines `dedupe.order_by`; duplicates are reported under data quality.
- **Small samples.** Figures based on fewer than `min_sample` records are flagged and never ranked or used by a recommendation detector.
- **Status classes.** `status_class` is OPEN, CLOSED or CANCELLED according to the reviewed status mapping; any other value is UNMAPPED, excluded from status-based metrics and counted under data quality.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `aged_days` | 30 | Age in days after which an open request counts as aged. 30 is a starting assumption, not a company standard; change it once the business confirms one. |

## Data contract

| Column | Type | Required | Meaning |
|---|---|---|---|
| `request_id` | VARCHAR | yes | Unique identifier of the service request / work order / case. |
| `created_at` | TIMESTAMP_NTZ | yes | When the request was opened, in UTC. |
| `closed_at` | TIMESTAMP_NTZ | no | When the request was resolved or closed, in UTC. |
| `due_at` | TIMESTAMP_NTZ | no | SLA target time in UTC (source column, or derived from an approved SLA policy). |
| `updated_at` | TIMESTAMP_NTZ | no | Last change to the source record, in UTC. |
| `loaded_at` | TIMESTAMP_NTZ | no | When the row was loaded into Snowflake, in UTC (data freshness). |
| `status` | VARCHAR | yes | Source status value, unchanged. |
| `status_class` | VARCHAR | yes | OPEN, CLOSED, CANCELLED or UNMAPPED, from the reviewed status mapping. |
| `priority` | VARCHAR | no | Priority as recorded in the source. |
| `category` | VARCHAR | no | Issue or service category. |
| `service_type` | VARCHAR | no | Type of service (e.g. repair, inspection). |
| `team` | VARCHAR | no | Team or work centre that owns the request. |
| `region` | VARCHAR | no | Region, branch or site. |

## Metrics

### Requests created (`requests_created`)

Distinct service requests opened in the period.

| | |
|---|---|
| Kind | period |
| Counted by | `created_at` in the reporting period |
| Calculation | `COUNT(DISTINCT request_id)` |
| Records included | all |
| Unit | count |
| Better when | neutral |
| Requires | `request_id`, `created_at` |
| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |

Limitations:

- Counts requests by their opening time in the reporting time zone.

### Requests closed (`requests_closed`)

Distinct requests whose status is in the CLOSED class and whose close time falls in the period.

| | |
|---|---|
| Kind | period |
| Counted by | `closed_at` in the reporting period |
| Calculation | `COUNT(DISTINCT request_id)` |
| Records included | `status_class = 'CLOSED'` |
| Unit | count |
| Better when | higher is better |
| Requires | `request_id`, `closed_at`, `status_class` |
| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |

Limitations:

- Cancelled requests are excluded; they are a separate status class.

### Average resolution time (`avg_resolution_hours`)

Mean hours from creation to close for requests closed in the period.

| | |
|---|---|
| Kind | period |
| Counted by | `closed_at` in the reporting period |
| Calculation | `AVG(DATEDIFF('minute', created_at, closed_at)) / 60.0` |
| Records included | `status_class = 'CLOSED' AND closed_at >= created_at` |
| Unit | hours |
| Better when | lower is better |
| Requires | `created_at`, `closed_at`, `status_class` |
| Minimum sample | 10 records |
| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |

Limitations:

- Calendar hours, not business hours; no working calendar is available.
- Records closed before they were created are excluded and reported as a data-quality issue.
- Sensitive to a few very long requests; read it next to the median.

### Median resolution time (`median_resolution_hours`)

Median hours from creation to close for requests closed in the period.

| | |
|---|---|
| Kind | period |
| Counted by | `closed_at` in the reporting period |
| Calculation | `MEDIAN(DATEDIFF('minute', created_at, closed_at)) / 60.0` |
| Records included | `status_class = 'CLOSED' AND closed_at >= created_at` |
| Unit | hours |
| Better when | lower is better |
| Requires | `created_at`, `closed_at`, `status_class` |
| Minimum sample | 10 records |
| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |

Limitations:

- Calendar hours, not business hours.

### 90th percentile resolution time (`p90_resolution_hours`)

Hours within which 90% of requests closed in the period were resolved.

| | |
|---|---|
| Kind | period |
| Counted by | `closed_at` in the reporting period |
| Calculation | `PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY DATEDIFF('minute', created_at, closed_at)) / 60.0` |
| Records included | `status_class = 'CLOSED' AND closed_at >= created_at` |
| Unit | hours |
| Better when | lower is better |
| Requires | `created_at`, `closed_at`, `status_class` |
| Minimum sample | 20 records |
| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |

Limitations:

- Needs at least 20 closed requests to be meaningful.

### SLA compliance (`sla_compliance_rate`)

Share of requests closed in the period that were closed on or before their SLA target time.

| | |
|---|---|
| Kind | period |
| Counted by | `closed_at` in the reporting period |
| Calculation | `AVG(IFF(closed_at <= due_at, 1.0, 0.0))` |
| Records included | `status_class = 'CLOSED' AND due_at IS NOT NULL AND closed_at >= created_at` |
| Unit | ratio |
| Better when | higher is better |
| Requires | `created_at`, `closed_at`, `due_at`, `status_class` |
| Minimum sample | 10 records |
| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |

Limitations:

- Only requests with an SLA target time are included; the share without one is shown as coverage.
- The target comes from the mapping (a source column or an approved SLA policy); see the definition panel.

### SLA breach rate (`sla_breach_rate`)

Share of requests closed in the period that were closed after their SLA target time.

| | |
|---|---|
| Kind | period |
| Counted by | `closed_at` in the reporting period |
| Calculation | `AVG(IFF(closed_at > due_at, 1.0, 0.0))` |
| Records included | `status_class = 'CLOSED' AND due_at IS NOT NULL AND closed_at >= created_at` |
| Unit | ratio |
| Better when | lower is better |
| Requires | `created_at`, `closed_at`, `due_at`, `status_class` |
| Minimum sample | 10 records |
| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |

Limitations:

- Equals 1 minus SLA compliance over the same records.

### Open backlog (`open_backlog`)

Distinct requests in the OPEN status class as of the analysis time.

| | |
|---|---|
| Kind | current state |
| Calculation | `COUNT(DISTINCT request_id)` |
| Records included | `status_class = 'OPEN' AND created_at <= {as_of}` |
| Unit | count |
| Better when | lower is better |
| Requires | `request_id`, `created_at`, `status_class` |
| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |

Limitations:

- Current state only. Past backlog cannot be rebuilt without status history, so there is no backlog trend.

### Aged backlog (`aged_backlog`)

Open requests older than the aged-days threshold as of the analysis time.

| | |
|---|---|
| Kind | current state |
| Calculation | `COUNT(DISTINCT request_id)` |
| Records included | `status_class = 'OPEN' AND created_at <= {as_of} AND DATEDIFF('day', created_at, {as_of}) > {aged_days}` |
| Unit | count |
| Better when | lower is better |
| Requires | `request_id`, `created_at`, `status_class` |
| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |

Limitations:

- The threshold (aged_days) is a configurable assumption.

### Aged share of backlog (`aged_backlog_share`)

Aged open requests divided by all open requests, as of the analysis time.

| | |
|---|---|
| Kind | current state |
| Calculation | `AVG(IFF(DATEDIFF('day', created_at, {as_of}) > {aged_days}, 1.0, 0.0))` |
| Records included | `status_class = 'OPEN' AND created_at <= {as_of}` |
| Unit | ratio |
| Better when | lower is better |
| Requires | `created_at`, `status_class` |
| Minimum sample | 10 records |
| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |

Limitations:

- The threshold (aged_days) is a configurable assumption.

### Average backlog age (`avg_backlog_age_days`)

Mean age in days of open requests as of the analysis time.

| | |
|---|---|
| Kind | current state |
| Calculation | `AVG(DATEDIFF('day', created_at, {as_of}))` |
| Records included | `status_class = 'OPEN' AND created_at <= {as_of}` |
| Unit | days |
| Better when | lower is better |
| Requires | `created_at`, `status_class` |
| Minimum sample | 10 records |
| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |

Limitations:

- Calendar days.

### Open past SLA target (`open_past_due`)

Open requests whose SLA target time has already passed, as of the analysis time.

| | |
|---|---|
| Kind | current state |
| Calculation | `COUNT(DISTINCT request_id)` |
| Records included | `status_class = 'OPEN' AND created_at <= {as_of} AND due_at < {as_of}` |
| Unit | count |
| Better when | lower is better |
| Requires | `request_id`, `created_at`, `due_at`, `status_class` |
| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |

Limitations:

- Only requests with an SLA target time can be past due.

## Data-quality checks (Analysis page, `missing_data` answers, data-quality recommendation)

| Check | Rule |
|---|---|
| Completeness | `COUNT(column) / COUNT(*)` for every mapped contract column |
| Duplicate ids | `COUNT(*) - COUNT(DISTINCT request_id)` |
| Unmapped status | `status_class = 'UNMAPPED'` |
| Closed before created | `closed_at < created_at` |
| Closed without time | `status_class = 'CLOSED' AND closed_at IS NULL` |
| Open with close time | `status_class = 'OPEN' AND closed_at IS NOT NULL` |
| Freshness | `as_of` older than 36 hours is shown as stale |
