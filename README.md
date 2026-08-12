# Oracle → Snowflake ingestion framework for Azure Data Factory

A metadata-driven ADF solution that does three things, in order, for every
table you declare:

1. **Define the structure** — read the column list, types, precision and
   nullability out of the Oracle data dictionary.
2. **Create it in Snowflake** — translate those Oracle types to Snowflake
   types and issue the DDL. Re-runnable: an existing table is extended with
   new columns rather than recreated.
3. **Transfer the data** — copy the rows, full or incremental, and record
   what happened.

Adding a table means inserting one row in a control table. No pipeline is
edited, no dataset is cloned, no DDL is hand-written.

---

## How it fits together

```
CTL.PIPELINE_CONTROL  ──►  PL_MASTER_ORACLE_TO_SNOWFLAKE
   (what to load)                    │
                                     ├── Phase 1 ── ForEach ──► PL_CREATE_SNOWFLAKE_TABLE
                                     │                              │
                                     │        Oracle ALL_TAB_COLUMNS ┤ read structure
                                     │        (ora_meta.v_table_metadata: type mapping)
                                     │                              │
                                     │        Snowflake SP_CREATE_TABLE_FROM_METADATA
                                     │                              └─► CREATE / ALTER TABLE
                                     │
                                     └── Phase 2 ── ForEach ──► PL_COPY_TABLE_DATA
                                                                    │
                                                       Oracle SELECT ┤ full or > watermark
                                                                    │
                                                       Copy activity ┤ via blob stage
                                                                    │
                                                       Snowflake     └─► COPY INTO + audit
```

Phase 2 does not start until phase 1 has finished for every table, so a copy
can never hit a table that does not exist yet.

## Repository layout

| Path | Contents |
|---|---|
| `adf/linkedService/` | Oracle, Snowflake, Key Vault, blob staging connections |
| `adf/dataset/` | Two parameterised datasets — one per side |
| `adf/pipeline/` | Master orchestrator, two child pipelines, one onboarding helper |
| `adf/trigger/` | Nightly schedule (deployed stopped) |
| `sql/oracle/` | Metadata view holding the type mapping, and the read-account grants |
| `sql/snowflake/` | Database setup, control/audit tables, stored procedures, seed data |
| `deploy/deploy.sh` | Deploys `adf/` to a factory with the az CLI |
| `docs/runbook.md` | Day-two operations: failures, backfills, schema drift |

The `adf/` layout matches ADF's Git format, so you can also just connect the
factory to this repository and the assets show up in the authoring canvas.

---

## Setup

### 1. Snowflake

Run in order, as a role that can create databases:

```sql
sql/snowflake/01_setup_database.sql        -- warehouse, database, RAW + CTL schemas, ADF role and user
sql/snowflake/02_control_tables.sql        -- PIPELINE_CONTROL, LOAD_WATERMARK, LOAD_AUDIT
sql/snowflake/03_sp_create_table_from_metadata.sql
sql/snowflake/04_sp_audit_and_watermark.sql
sql/snowflake/05_seed_control_table.sql    -- edit first: this is your table list
```

### 2. Oracle

```sql
sql/oracle/03_source_grants.sql   -- ADF_READER account, SELECT on the source schemas
sql/oracle/01_metadata_view.sql   -- ora_meta.v_table_metadata (the type mapping)
```

If the ADF account may not create objects in Oracle, skip the view and paste
`sql/oracle/02_metadata_query_inline.sql` into the Lookup activity of
`PL_CREATE_SNOWFLAKE_TABLE` instead.

### 3. Azure

* A **self-hosted integration runtime** named `SHIR-OnPrem` with line of sight
  to the Oracle listener. Rename it in `LS_Oracle_Source.json` if yours differs.
* A **Key Vault** holding three secrets:

  | Secret | Value |
  |---|---|
  | `oracle-adf-reader-password` | password for `ADF_READER` |
  | `snowflake-adf-private-key` | PEM body of the key pair for `SVC_ADF` |
  | `adf-staging-blob-sas-uri` | SAS URI of the staging container |

  Grant the factory's managed identity **Key Vault Secrets User**.
* A **staging container** on ADLS/Blob. Give it a lifecycle rule that deletes
  blobs after one day — ADF cleans up after itself, but a failed run can leave
  files behind.
* Edit the placeholders in `adf/linkedService/*.json` (Key Vault URL, Oracle
  host/service, Snowflake account identifier).

### 4. Deploy and run

```bash
RESOURCE_GROUP=rg-data-platform FACTORY_NAME=adf-oracle-snowflake ./deploy/deploy.sh
```

Then run `PL_MASTER_ORACLE_TO_SNOWFLAKE` manually once with
`pRunMode = DDL_ONLY`, check the tables that appear in `RAW`, and run again
with `pRunMode = FULL`. Start the trigger when you are happy.

---

## Onboarding tables

One row per table in `CTL.PIPELINE_CONTROL`:

```sql
CALL CTL.SP_REGISTER_TABLE('SALES', 'ORDERS', 'RAW', 'INCREMENTAL', 'LAST_UPDATED', 'NIGHTLY');
```

Or onboard a whole schema at once by running `PL_DISCOVER_ORACLE_SCHEMA` with
`pSourceSchema = SALES`; it registers every table as a FULL load, and you then
switch the large ones to incremental.

The columns that matter:

| Column | Effect |
|---|---|
| `LOAD_TYPE` | `FULL` truncates and reloads; `INCREMENTAL` appends rows above the watermark |
| `WATERMARK_COLUMN` | Oracle column driving the incremental predicate — needs an index |
| `PARTITION_COLUMN` | When set, ADF reads the source in parallel ranges on this column |
| `SOURCE_QUERY_OVERRIDE` | Replaces the generated `SELECT` entirely |
| `LOAD_GROUP` | Lets one trigger run a subset (`pLoadGroup` parameter) |
| `IS_ACTIVE` | The off switch for one table |
| `PRIORITY` | Ordering of the control rows (large tables first is usually right) |

## Run modes

`PL_MASTER_ORACLE_TO_SNOWFLAKE` takes `pRunMode`:

| Value | Behaviour |
|---|---|
| `FULL` | structures, then data (the normal nightly run) |
| `DDL_ONLY` | mirror structures only — use it to preview a new schema |
| `DATA_ONLY` | skip the dictionary read; fastest when nothing has changed structurally |

## Type mapping

Defined once, in `ora_meta.v_table_metadata`. Change it there and the next run
picks it up.

| Oracle | Snowflake | Note |
|---|---|---|
| `VARCHAR2(n)`, `CHAR(n)`, `NVARCHAR2`, `NCHAR` | `VARCHAR(n)` | length from `CHAR_LENGTH`, floored at `DATA_LENGTH` |
| `CLOB`, `NCLOB`, `LONG`, `XMLTYPE` | `VARCHAR(16777216)` | Snowflake's maximum |
| `NUMBER(p,s)` | `NUMBER(p,s)` | precision capped at 38 |
| `NUMBER` (unconstrained) | `NUMBER(38,10)` | Oracle allows more scale than Snowflake can hold |
| `NUMBER(p,-s)` | `NUMBER(38,0)` | Snowflake has no negative scale |
| `FLOAT`, `BINARY_FLOAT`, `BINARY_DOUBLE` | `FLOAT` | |
| `DATE` | `TIMESTAMP_NTZ(0)` | **not** `DATE` — Oracle `DATE` carries a time |
| `TIMESTAMP(n)` | `TIMESTAMP_NTZ(n)` | |
| `TIMESTAMP WITH [LOCAL] TIME ZONE` | `TIMESTAMP_TZ` / `TIMESTAMP_LTZ` | |
| `INTERVAL …` | `VARCHAR(64)` | no Snowflake equivalent |
| `RAW`, `LONG RAW`, `BLOB` | `BINARY` | |
| `ROWID`, `UROWID` | `VARCHAR(64)` | |

`BFILE`, `ANYDATA` and `SDO_GEOMETRY` are excluded from the view: ADF cannot
read them, and including one would fail the whole table. Use
`SOURCE_QUERY_OVERRIDE` to cast such a column to something transferable.

## Design decisions worth knowing

**Why the copy goes through a blob stage.** Snowflake ingests with `COPY INTO`,
which reads from cloud storage. A Copy activity whose source is not already in
blob storage therefore has to stage the extract first — ADF does this for you
when `enableStaging` is on, and the sink is still an ordinary Snowflake table.
This is not an extra hop you can configure away with the Snowflake connector.

**Why the watermark is read back from Snowflake.** After a load, the new
high-water mark is `MAX(watermark_column)` of the rows that actually landed,
not the time the pipeline started. If a copy moves half the delta and then
fails, the next run picks up exactly where the data stops.

**Why structure changes are additive only.** A new source column is added to
the Snowflake table automatically. A changed or dropped column is reported in
the audit row and left alone — applying it would mean dropping or rewriting a
populated column, which is a decision for a person.

**Why phases are separate pipelines.** ADF does not allow a `ForEach` inside an
`If`, and nested `If` activities are also rejected. Splitting the per-table work
into two child pipelines keeps each one flat, and has the useful side effect
that you can run either phase on its own.

## Monitoring

```sql
-- current state of every table
SELECT * FROM CTL.V_LAST_LOAD_STATUS ORDER BY LAST_STATUS, SOURCE_OBJECT;

-- everything that happened in one master run
SELECT * FROM CTL.LOAD_AUDIT WHERE ADF_PARENT_RUN_ID = '<run id>' ORDER BY LOGGED_AT;

-- failures in the last day
SELECT TARGET_OBJECT, PHASE, ERROR_MESSAGE, LOGGED_AT
FROM CTL.LOAD_AUDIT
WHERE STATUS = 'FAILED' AND LOGGED_AT > DATEADD(day, -1, CURRENT_TIMESTAMP())
ORDER BY LOGGED_AT DESC;
```

A failed table fails its own ForEach iteration and is recorded in
`CTL.LOAD_AUDIT`; the other tables in the batch keep going.

See `docs/runbook.md` for what to do when one does fail.
