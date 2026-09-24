# DSP → Snowflake daily sync

Every day this job downloads the full **Manage Assets** list from the Cat Dealer
Services Portal ([dsp.cat.com/asset](https://dsp.cat.com/asset)) and loads it
into Snowflake as a dated snapshot.

It gets the same file you get by clicking the export (download) icon on
Manage Assets and choosing **CSV**, and it calls the same DSP backend the page
calls:

| Step | Call |
| --- | --- |
| 1. Request the export | `POST https://prod-bff-dsp.cat.com/assetdata/dealerFleet/export` |
| 2. Wait until it's built | `GET  …/assetdata/export/{exportID}` until `status = Completed` |
| 3. Download the file | `GET  …/assetdata/download/{fileId}` |

DSP only accepts calls from a signed-in Cat.com user. The job keeps a saved
browser profile, and each morning it opens the portal headless and reuses
that user's sign-in. No password is stored in the job.

## Setup (once)

Use a machine that is on every day (a server or an always-on PC). For
step 3 it also needs a screen.

```sh
cd dsp_sync
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip ...
.venv/bin/playwright install chromium
cp .env.example .env                              # then fill in the Snowflake values
.venv/bin/python main.py login                    # a browser opens: sign in to DSP, MFA included
.venv/bin/python main.py run                      # first real run
```

For Snowflake, a dedicated service user with **key-pair auth** is best,
because password users are now pushed onto MFA. The role needs `USAGE` on the
warehouse, database and schema, plus `CREATE TABLE` and `CREATE VIEW` on the
schema.

## Schedule it

**Linux (cron).** This runs at 06:00 every day:

```
0 6 * * * /opt/dsp_sync/schedule/run_daily.sh
```

**Windows (Task Scheduler).**

```
schtasks /Create /SC DAILY /ST 06:00 /TN "DSP to Snowflake" /TR "C:\dsp_sync\schedule\run_daily.bat"
```

The exit code tells you how the run went. `0` means success. `2` means the
Cat.com sign-in has expired, so run `python main.py login` again. `1` is any
other failure. Details are written to `logs/dsp_sync.log`.

## What lands in Snowflake

- **`DSP_ASSETS`** holds one row per asset per day. Every CSV column becomes a
  `VARCHAR` column with an UPPER_SNAKE_CASE name (`Serial Number` →
  `SERIAL_NUMBER`). Two columns are added: `SNAPSHOT_DATE` (DATE) and
  `LOADED_AT` (TIMESTAMP_NTZ).
- **`DSP_ASSETS_LATEST`** is a view of the most recent day only.
- **Safe to re-run.** Running the same day again replaces that day's rows
  instead of duplicating them. The swap happens in one transaction.
- **New columns are handled.** If DSP adds a column, the table gets it
  automatically. An empty export is refused, so it can't wipe out a day.

The raw CSVs are also kept in `downloads/` for `DSP_KEEP_DAYS` days. To reload
one by hand, run `python main.py load downloads/dsp_assets_2026-09-24.csv --date 2026-09-24`.

## Things to know

- **Sign-in lifetime.** The saved sign-in renews itself on each daily run,
  but Cat.com can still expire it, for example after a password change or a
  policy limit. When that happens the job exits with code `2`. Run `login`
  again and the next run continues as normal.
- **Use a dedicated DSP account** if you can, such as a service mailbox. The
  export contains whatever that user can see in DSP.
- **Cat may change the portal.** This relies on DSP's internal API, which Cat
  can change without notice. For a supported long-term feed, ask your Cat
  Digital contact about official API access to this data.
- **Tests.** `pytest tests` runs offline against a fake DSP backend and a fake
  Snowflake connection.
