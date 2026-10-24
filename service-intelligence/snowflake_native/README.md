# Service Intelligence: Snowflake-native app

This is a Streamlit-in-Snowflake app with three pages: **Analysis**, **Recommendation plan** and **Assistant**.
It runs only on the tables in `SERVICE_INTELLIGENCE`: the copies of Cat telematics (`DOMAIN_TELEMETRY`) and OLGA. Nothing leaves Snowflake.

```
SERVICE_INTELLIGENCE
├── DOMAIN_TELEMETRY, OLGA   copies of CAT_SHARE (re-copied daily by SI_ADMIN.REFRESH_DAILY)
├── CURATED                  cleaned views (logic) + daily materialized tables the app reads
│   ├── FAULT_EVENTS           one row per fault event (37 % repeat reports removed)
│   ├── ASSET_DAILY            one valid service-meter reading per asset per day
│   ├── OPPORTUNITIES          latest OLGA snapshot, placeholder dates removed
│   └── LOST_OPPORTUNITY_MONTHLY  per month / dealer / branch / SMCS group, duplicates removed
├── SI_APP                   app: recommendation plan, append-only audit, app users, chat log, Streamlit app
└── SI_ADMIN                 refresh procedure, task, copy log
```

## How it works

- **One source of truth.** `app/metrics.yaml` defines every KPI once. `app/si_native/metrics.py` is the only code that
  turns a definition into SQL. The dashboard, the recommendation detectors and the assistant all call it, so the same
  question gives the same SQL and the same number. The tests check this.
- **Safe execution.**
  - Every query is one parameterised SELECT over the four CURATED tables. User values are bind parameters.
  - A final guard (`check_spec`) refuses anything else.
  - Queries carry a `QUERY_TAG` (`{"app": "service_intelligence", ...}`), so cost and audit are visible in `QUERY_HISTORY`.
  - The warehouse limits statements to 120 s and the session to 60 s.
  - Results are cached for 15 minutes; the data changes once a day.
- **Assistant.**
  - Rules map a question to a registry metric, a period, a dimension and filters.
  - Cortex (`claude-sonnet-4-6`) is off by default. When switched on, it only *chooses* among approved metrics; its JSON is validated.
  - Answers are assembled from query results, so the model cannot invent numbers.
  - Questions outside the registry are refused.
- **Recommendations.**
  - The detector `repeat_high_faults` flags faults where 10 or more assets had the same HIGH-severity fault on 3 or more days in the last 30.
  - Score = 50 × impact + 30 × urgency + 20 × confidence.
  - Evidence = the exact registry queries and their query IDs.
  - Expected impact is labelled as a hypothesis.
  - Plans persist in `SI_APP.RECOMMENDATIONS` with an append-only `RECOMMENDATION_EVENTS` audit trail.
- **Daily top-100 opportunities.**
  - `SI_ADMIN.BUILD_DAILY_TOP_OPPORTUNITIES(100, 90)` runs at the end of each daily refresh and writes
    `SI_APP.DAILY_TOP_OPPORTUNITIES`, one list per day; earlier days are kept.
  - Rule: from the latest OLGA snapshot, take opportunities with value > 0 and a target date in the next 90 days. Rank by
    value, then OLGA lead score, and keep 100. `is_new_today` marks entries that were not on the previous day's list.
  - The Recommendations page opens on this list:
    - value of the list and the number of new entries;
    - filters by branch, job and product family;
    - CSV download;
    - "Save selected to plan", which creates tracked plan items with evidence. The branch is suggested as owner, and the
      same opportunity can't be saved twice while it is open.
  - The assistant answers "show the top 20 opportunities" and "value of the top 100 by branch" from the same table.
  - OLGA publishes opportunities monthly, so most day-to-day change comes from the 90-day window moving forward.
- **Access.**
  - Who can open the app: `GRANT USAGE ON STREAMLIT` to `SI_APP_VIEWER`.
  - What they can change: `SI_APP.APP_USERS` (`viewer` < `analyst` < `approver` < `admin`), checked server-side.
  - The app runs with its owner's rights (`SI_APP_OWNER`). That role can read CURATED (not the raw copies) and write only to SI_APP.

## Deploy (about 15 minutes)

1. In Snowsight, as ACCOUNTADMIN, run `sql/10_setup.sql`. It creates the roles, warehouse, schemas, views, tables, refresh
   procedure and task, and the grants.
2. Upload and create the app, **as SI_APP_OWNER**. Use either of these:
   - **Snowflake CLI:** from this folder, run `snow streamlit deploy --replace --role SI_APP_OWNER --warehouse SI_WH`.
   - **Snowsight:**
     1. `USE ROLE SI_APP_OWNER; CREATE STAGE IF NOT EXISTS SERVICE_INTELLIGENCE.SI_APP.APP_STAGE;`
     2. Upload the contents of `app/` to `@SERVICE_INTELLIGENCE.SI_APP.APP_STAGE/si/`, keeping the `pages/` and `si_native/` folders.
     3. Run:
        ```sql
        CREATE OR REPLACE STREAMLIT SERVICE_INTELLIGENCE.SI_APP.SERVICE_INTELLIGENCE_APP
          FROM '@SERVICE_INTELLIGENCE.SI_APP.APP_STAGE/si' MAIN_FILE = 'streamlit_app.py'
          QUERY_WAREHOUSE = SI_WH TITLE = 'Service Intelligence';
        ```
3. Give people access:
   ```sql
   GRANT USAGE ON STREAMLIT SERVICE_INTELLIGENCE.SI_APP.SERVICE_INTELLIGENCE_APP TO ROLE SI_APP_VIEWER;
   GRANT ROLE SI_APP_VIEWER TO ROLE <their role>;
   INSERT INTO SERVICE_INTELLIGENCE.SI_APP.APP_USERS (email, app_role) VALUES ('someone@mantracgroup.com', 'analyst');
   ```
4. Turn on the daily refresh: `ALTER TASK SERVICE_INTELLIGENCE.SI_ADMIN.REFRESH_DAILY RESUME;`

## Tests

```bash
cd service-intelligence/snowflake_native
pip install pytest pyyaml pandas streamlit
python -m pytest tests            # FAKE data only; no Snowflake
```

The tests prove SQL construction, safety, consistency, ranking and that the pages render. Company figures were checked
separately, live and read-only (below).

## Live verification (24 Oct 2026, read-only connector, role DEV_ADMIN)

**Method.** The app's builder compiled each query. Because the CURATED tables did not exist yet, each table name was replaced by
the SELECT of its view from `sql/10_setup.sql`. Each query ran unchanged otherwise.

| Query (app SQL) | Live result | Independent check on the raw copy |
|---|---|---|
| Fault events after de-duplication | 4,946,230 events, 3,960 assets, unique keys | 7,853,364 rows − 2,907,134 repeats ✔ |
| Assets with high-severity faults, Q3 2026 | **1,974** | 1,976 on raw rows; see the note below ✔ |
| Assets with repeat high-severity faults, last 30 days | 736 | n/a |
| Repeat-fault breakdown: low line-neutral voltage | 427 assets | the same metric with that filter = 427 ✔ |
| High-severity assets per month, Apr–Sep 2026 | 1,559 / 1,384 / 1,398 / 1,397 / 1,392 / 1,428 | n/a |
| Assets reporting, 7 days to 23 Oct | 9,083 | n/a |
| Open opportunity value (Sep 2026 snapshot) | 1.708bn | 1.708bn in the profiling query ✔ |
| Opportunity value due in the next 90 days (from 16 Oct) | 124.4M (493,060 opportunities) | n/a |
| Lost opportunity, Q3 2026 | 41,242,205 | 41,224,204 on raw rows; the 18,001 difference is exactly the duplicate rows removed ✔ |
| Capture rate, Sep 2026 | 70.8 % | n/a |
| Daily top-100 selection, run date 24 Oct 2026 | 100 unique opportunities, total 11.47M, values 77,932–638,984, 22 branches, 39 customers, target dates 25 Oct 2026 – 21 Jan 2027 | the 12 largest checked by hand against OLGA rows ✔ |

**Note on the 2-asset gap.** 23,860 fault events (0.8 % of repeated events) were reported again with a *different* severity.
The view keeps the most recently received classification; the raw count includes an asset if any report said HIGH.

## Not done yet / known limits

- **Not deployed or run as an app.** The connector used here is read-only, so it can't create objects or deploy. Every
  query has run live; the pages have only run in tests on fake data.
- **Out of scope:** service orders, SLA, resolution time and backlog. No such data is in `SERVICE_INTELLIGENCE`.
- **Recommendations so far:** repeat high-severity faults (fleet health) and the daily top-100 opportunities.
- **Currency of OLGA values** is not confirmed. `PART_COST` equals `PART_COST_USD`, but `TOTAL_VALUE` and the SCP columns are unlabelled.
