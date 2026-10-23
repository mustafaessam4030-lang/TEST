# Service Intelligence — architecture

## 1. Decision

**Snowflake stays the data platform and holds every calculation's source of truth.** The application tier is deliberately thin: a Python/FastAPI server plus a static HTML/JS UI.

| Option | Verdict | Why |
|---|---|---|
| **FastAPI + static UI on a company host (chosen)** | ✔ | Same stack as this repository's assistants (engineering assistant, ATLAS, Maia), so it reuses their Snowflake connection conventions and test patterns. It is testable end to end without Snowflake. It controls query safety, caching and audit in one place. It integrates with company SSO through a reverse proxy. |
| Streamlit in Snowflake (SiS) | Viable later | It needs no host or secrets, and the data never leaves Snowflake. However, SiS apps run with the **owner's rights**, so row-access policies keyed on `CURRENT_ROLE()` see the owner, not the viewer. Its per-user plan workflow and audit would need extra work. It could not be developed or verified without account access. The analytics core (`si/`) is UI-independent, so a SiS front end can call the same modules. |
| External DB, vector store or paid LLM API | ✘ | Not needed. Plan state lives in a Snowflake schema (`SI_APP`), and the optional AI uses Snowflake Cortex inside the account. |

## 2. Components

```
Browser (static HTML/JS; no credentials, never talks to Snowflake)
   │  HTTPS, session via SSO proxy header or bearer token
   ▼
FastAPI  si/api.py ── auth.py (roles → permissions, per-user data scope)
   │
   ├── analysis.py      KPI value / previous period / series / breakdown / drill-down / quality / freshness
   ├── recommend.py     7 detectors → ranked, evidence-backed candidates (uses analysis.py only)
   ├── plans.py         save / review / approve / assign / comment; append-only audit (writer role)
   └── chat/            intent.py (rules) → [cortex.py, optional] → assistant.py (uses analysis.py, recommend.py, plans.py)
            │
            ▼
   query.py  ← registry.py (config/metrics.yaml)  ← contract.py (view shape) ← mapping.py (reviewed YAML → view DDL)
            │  parameterised Snowflake SQL, `?` binds
            ▼
   executor.py   guard.validate → cache → single-flight → backend → query log
            │
            ├── backends/snowflake.py   reader role: SELECT on the contract view (+ CORTEX_USER if enabled)
            │                           writer role: INSERT/UPDATE on SI_APP plan tables only
            └── backends/fixture.py     DuckDB + SYNTHETIC data, same SQL transpiled (dev/test only)
```

## 3. One source of truth for every number

- `config/metrics.yaml` defines each KPI once: expression, filter, time column, required columns, minimum sample and limitations. The registry loader rejects definitions that reference non-contract columns or fail to declare what they use.
- `si/query.py` is the only code that turns a definition into SQL.
- The Analysis page, every recommendation detector and every assistant answer call the same `Analytics` methods. Tests assert that chat figures equal dashboard figures and that recommendation evidence equals the dashboard metric.
- `SERVICE_METRIC_DEFINITIONS.md` is generated from the registry, and a test fails if it drifts.

## 4. Vertical slice (implemented end to end)

Take *median resolution time*:

1. **KPI card.** A card on Analysis shows the value with its previous-period delta and record count.
2. **Breakdown.** The card's breakdown by region shows South at 2.4× the overall median.
3. **Drill-down.** Selecting the South bar opens the drill-down: the actual records, contract fields only, paginated.
4. **Recommendation.** "Investigate slow resolution in region South" quotes the same two numbers with their query ids and SQL.
5. **Plan.** It is saved as `R-1xx` and moved through review and approval, with each step in the audit trail.
6. **Assistant.** The question "Which regions have the longest resolution times?" returns the same breakdown from the same SQL.
7. **Evidence lookup.** "Show the evidence behind recommendation R-1xx" returns the frozen evidence.

The same slice runs on real data after the mapping is applied.

## 5. Assistant pipeline

1. **Classify (`intent.py`).** Deterministic rules map the question to a plan. A plan can name only:
   - a registry metric;
   - a registry dimension;
   - a period (named or explicit dates);
   - a comparison or trend flag;
   - a saved recommendation id.

   It contains no SQL. Requests for SQL, credentials, or customer and free-text fields are refused before any query runs.
2. **Fallback (optional, `cortex.py`).** If the rules cannot classify the question and `SI_CORTEX_ENABLED=1`, `SNOWFLAKE.CORTEX.COMPLETE` maps it to the same JSON plan.
   - The model sees only the question (fenced as data) and the list of approved ids.
   - Its output is validated against the registry; anything else is discarded.
   - It never sees data rows, never writes SQL and never composes numbers.
3. **Execute.** The plan runs through `Analytics` with the user's filters, period and data scope.
4. **Compose.** The answer is built from templates around the returned values. It carries:
   - the period and as-of time;
   - the filters and scope;
   - the metric definitions and limitations;
   - its sources (query id, SQL and bind values);
   - follow-up questions.

   On a failed or empty query it says so and shows no figures.
5. **Context.** Only the previous plan is kept, per user and conversation, for two hours, so "and by team?" works. No data and no long-term memory are stored.

## 6. Recommendation engine

| Detector | Signal | Minimum evidence |
|---|---|---|
| backlog_aging | A segment's aged share of open requests ≥ overall + 15 pts and ≥ 30% | ≥ 20 open requests in the segment |
| past_due | Open requests past their SLA target ≥ 20% of the backlog | ≥ 5 requests |
| sla_hotspot | A segment's SLA compliance ≤ overall − 10 pts | ≥ 20 closed requests |
| slow_segment | A segment's median resolution ≥ 1.5× overall | ≥ 20 closed requests |
| resolution_regression | Median resolution up ≥ 25% against the previous equal period | ≥ 20 in both periods |
| volume_surge | Category or service-type volume up ≥ 40% against the previous equal period | ≥ 20 requests |
| data_quality | A mapped field < 95% complete, or a check affecting ≥ 1% of rows | — |

**Ranking.** `score = 0.45 × impact + 0.30 × urgency + 0.25 × confidence`, each scored 1–5.

- **Impact** is the affected share of the population.
- **Urgency** is the size of the gap.
- **Confidence** comes from sample size, field completeness and staleness.

**Labelling.** Effort is never estimated, because no source supports it. An owner is suggested only when the segment is a team taken from the source. Expected impact is always labelled as a projection, with its assumptions. Every candidate states that it shows where, not why.

**Persistence.** Candidates are held server-side for an hour. Saving copies the server's candidate, so a client cannot forge evidence. Saved items freeze their evidence and snapshot id.

## 7. Persistence and audit

- **`SI_APP.RECOMMENDATIONS`** holds the current state, with a version number for optimistic concurrency.
- **`SI_APP.RECOMMENDATION_EVENTS`** is append-only (the writer role has no UPDATE or DELETE on it). It records created, status-changed, assigned and commented events with actor, time, from and to status, and note.
- **Status workflow:** proposed → reviewed → approved → in progress → completed, with rejected or blocked possible from the open states. Rejecting or blocking needs a note.
- **Query log:** time, user, purpose, metric, query id, duration, status and row count. It never records bind values, credentials or row data. It is written as JSONL under `SI_DATA_DIR`, with the most recent entries also available at `/api/admin/queries`.
- **Never automated:** no recommendation triggers an operational action or writes to a source system.

## 8. Environments

| `SI_ENV` | Backend | Auth | Notes |
|---|---|---|---|
| dev | fixture (SYNTHETIC) or snowflake | dev, token or trusted_header | Plans in `data/plans-dev.duckdb` |
| test | fixture | token or trusted_header | CI; in-memory plans |
| prod | snowflake only | token or trusted_header | Refuses fixture, dev auth and password auth; API docs disabled |
