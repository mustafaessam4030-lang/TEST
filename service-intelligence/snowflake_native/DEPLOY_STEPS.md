# Service Intelligence: deploy on Snowflake (step by step)

The package contains:
- `sql/10_setup.sql`: the setup script (run once, re-runnable).
- `app/`: the Streamlit app (upload as is).

## Step 1: Run the setup script (Snowsight, about 10–20 minutes)
1. In Snowsight, go to **Projects → Worksheets → + SQL Worksheet**.
2. Paste the whole of `sql/10_setup.sql` and set the role to **ACCOUNTADMIN**.
3. Click **Run All**.

Then check it:
```sql
SELECT 'fault_events' t, COUNT(*) FROM SERVICE_INTELLIGENCE.CURATED.FAULT_EVENTS
UNION ALL SELECT 'asset_daily', COUNT(*) FROM SERVICE_INTELLIGENCE.CURATED.ASSET_DAILY
UNION ALL SELECT 'opportunities', COUNT(*) FROM SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES
UNION ALL SELECT 'top100_today', COUNT(*) FROM SERVICE_INTELLIGENCE.SI_APP.DAILY_TOP_OPPORTUNITIES WHERE overall_rank IS NOT NULL;
```
Expect about 4.9M, 7.7M, 13.5M and 100.

## Step 2: Deploy the app
**Option A: Snowflake CLI.** From the `app/` folder:
```bash
snow streamlit deploy --replace --role SI_APP_OWNER --warehouse SI_WH --database SERVICE_INTELLIGENCE --schema SI_APP
```

**Option B: Snowsight only.**
1. Run:
   ```sql
   USE ROLE SI_APP_OWNER;
   CREATE STAGE IF NOT EXISTS SERVICE_INTELLIGENCE.SI_APP.APP_STAGE;
   ```
2. Go to **Data → Databases → SERVICE_INTELLIGENCE → SI_APP → Stages → APP_STAGE → + Files** and upload:

   | Files from `app/` | Upload path |
   |---|---|
   | `streamlit_app.py`, `environment.yml`, `metrics.yaml` | `si` |
   | `pages/2_Recommendations.py`, `pages/3_Assistant.py` | `si/pages` |
   | all files in `si_native/` (8) | `si/si_native` |

3. Run:
   ```sql
   USE ROLE SI_APP_OWNER;
   CREATE OR REPLACE STREAMLIT SERVICE_INTELLIGENCE.SI_APP.SERVICE_INTELLIGENCE_APP
     FROM '@SERVICE_INTELLIGENCE.SI_APP.APP_STAGE/si'
     MAIN_FILE = 'streamlit_app.py'
     QUERY_WAREHOUSE = SI_WH
     TITLE = 'Service Intelligence';
   ```

## Step 3: Give people access
```sql
USE ROLE ACCOUNTADMIN;
GRANT USAGE ON STREAMLIT SERVICE_INTELLIGENCE.SI_APP.SERVICE_INTELLIGENCE_APP TO ROLE SI_APP_VIEWER;
GRANT ROLE SI_APP_VIEWER TO ROLE <their_role>;
INSERT INTO SERVICE_INTELLIGENCE.SI_APP.APP_USERS (email, app_role)
VALUES ('colleague@mantracgroup.com', 'analyst');   -- viewer / analyst / approver / admin
```

## Step 4: Turn on the daily refresh (13:30 UTC every day)
```sql
USE ROLE DEV_ADMIN;
ALTER TASK SERVICE_INTELLIGENCE.SI_ADMIN.REFRESH_DAILY RESUME;
```

## Step 5: Open
In Snowsight, go to **Projects → Streamlit → Service Intelligence**. It has three pages:
- **Analysis**;
- **Recommendations** (Top 100 machines today, fleet-health findings, saved plan);
- **Assistant**.

If a step fails, send the exact error message and the step number.
