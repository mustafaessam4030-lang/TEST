# Runbook

Day-two operations for the Oracle → Snowflake ADF framework.

## Finding out what went wrong

Start in Snowflake, not in the ADF monitor — the audit table has the error
message and the table name in one row:

```sql
SELECT TARGET_OBJECT, PHASE, STATUS, ROWS_WRITTEN, ERROR_MESSAGE, LOGGED_AT
FROM CTL.LOAD_AUDIT
WHERE ADF_PARENT_RUN_ID = '<master run id from the ADF monitor>'
ORDER BY PHASE, LOGGED_AT;
```

`PHASE = 'DDL'` means the structure step failed, `PHASE = 'DATA'` the copy.

## Common failures

### `No column metadata returned for SCHEMA.TABLE`

The Lookup came back empty. Either the table does not exist under that owner,
or `ADF_READER` has no `SELECT` grant on it. Check from Oracle as the ADF
account:

```sql
SELECT COUNT(*) FROM all_tab_columns WHERE owner = 'SALES' AND table_name = 'ORDERS';
```

Zero rows means it is a grant problem — rerun the grant loop in
`sql/oracle/03_source_grants.sql` for that schema.

### `Numeric value out of range` / truncation on copy

The Oracle column holds values that do not fit the mapped Snowflake type. The
usual cause is an unconstrained `NUMBER` with more than 10 decimal places,
which maps to `NUMBER(38,10)`. Either widen the mapping in
`ora_meta.v_table_metadata`, or set `SOURCE_QUERY_OVERRIDE` for that table with
an explicit `ROUND()` or `CAST`. After changing the mapping you have to drop
the Snowflake table for the new type to take effect — the framework never
retypes a populated column on its own.

### `String is too long` on a VARCHAR column

Oracle byte-semantics columns can hold more bytes than characters. The view
already floors the length at `DATA_LENGTH` for that reason; if it still
overflows, the source is storing multi-byte data in a column declared with
byte semantics. Widen the target column by hand:

```sql
ALTER TABLE RAW.MY_TABLE ALTER COLUMN MY_COL SET DATA TYPE VARCHAR(4000);
```

### Copy times out on a large table

Set `PARTITION_COLUMN` on the control row to an indexed numeric or date column.
ADF then splits the read into ranges and pulls them in parallel. Raise
`parallelCopies` in `PL_COPY_TABLE_DATA` only after checking the source can
take the load.

### Staging errors mentioning SAS or authentication

The Snowflake staged copy supports SAS and account key only — not managed
identity. If the SAS in Key Vault has expired, mint a new one and update the
`adf-staging-blob-sas-uri` secret. No pipeline change is needed; the linked
service reads the current secret version.

## Reruns and backfills

**Rerun one table.** Run `PL_COPY_TABLE_DATA` directly with that table's
control-row values. For a FULL table this is safe at any time (it truncates
first). For an INCREMENTAL table it resumes from the stored watermark.

**Rerun a whole batch.** Rerun `PL_MASTER_ORACLE_TO_SNOWFLAKE` with
`pRunMode = DATA_ONLY` if the structures are already correct.

**Backfill an incremental table from scratch:**

```sql
-- 1. clear the target
TRUNCATE TABLE RAW.ORDERS;

-- 2. reset the watermark
UPDATE CTL.LOAD_WATERMARK
SET HIGH_WATERMARK = '1900-01-01 00:00:00'
WHERE CONTROL_ID = (SELECT CONTROL_ID FROM CTL.PIPELINE_CONTROL
                    WHERE SOURCE_SCHEMA = 'SALES' AND SOURCE_TABLE = 'ORDERS');
```

Then rerun. For a very large backfill, temporarily set `LOAD_TYPE = 'FULL'`
and a `PARTITION_COLUMN` instead — one partitioned full load beats a single
unindexed incremental scan.

**Load a subset tonight only.** Set `IS_ACTIVE = FALSE` on what you want to
skip, or run the master pipeline with a `pLoadGroup` that covers only the
tables you want.

## Schema drift

A new column at the source is added to the Snowflake table on the next run
that includes phase 1, as `NULL`able. Existing rows keep `NULL` for it — if
you need the history populated, backfill the table afterwards.

A changed type is **reported, not applied**. Look for it in the audit row:

```sql
SELECT TARGET_OBJECT, ERROR_MESSAGE
FROM CTL.LOAD_AUDIT
WHERE PHASE = 'DDL' AND ERROR_MESSAGE LIKE '%TYPE DRIFT%'
ORDER BY LOGGED_AT DESC;
```

To apply one, decide whether it is a widening (safe — `ALTER TABLE … SET DATA
TYPE`) or a narrowing/incompatible change (rebuild the table and reload).

A dropped source column is left in place in Snowflake and simply stops
receiving values. Drop it by hand when you are sure nothing reads it.

## Cost and performance notes

* The warehouse is `SMALL` with a 60-second auto-suspend. Ingest is
  `COPY INTO`, which is not warehouse-bound for the file load itself; a bigger
  warehouse mostly helps the `MAX(watermark)` scan on large tables.
* `pCopyParallelism` (default 5) is the number of tables copied at once. Every
  concurrent copy is a concurrent Oracle session — coordinate with the DBA
  before raising it.
* Staged files are compressed and deleted after a successful copy. Check the
  staging container occasionally for leftovers from failed runs, and keep the
  lifecycle rule in place.
