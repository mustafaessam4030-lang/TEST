# 16 · Snowflake as Maia's store

Every successful Caterpillar SIS lookup is saved to Snowflake. The local folder
(`logs/sis-results/`) stays next to it as evidence: JSON, TXT and screenshots.

```
Maia chat → gateway → EquipmentService
                         └── MirroredRepository
                               ├── primary: SnowflakeEquipmentRepository   ← the record
                               └── mirror : LocalJsonRepository            ← evidence
```

**The rule:** a lookup succeeds only if Snowflake saved it. If the Snowflake
write fails, the lookup fails with `PERSISTENCE_FAILED` ("retrieved but could
not be saved"). It is never reported as a success. If only the local mirror
fails, that is logged and the lookup still succeeds.

## Connect it (Windows, 4 steps)

1. Copy `snowflake.example.txt` to `snowflake.txt` in `C:\SIS\maia\`.
2. Fill in `account`, `user`, and **one** sign-in method (SSO, key pair or password/PAT).
   Set `role`, `warehouse`, `database` and `schema` to where the tables should live.
3. Double-click **SNOWFLAKE-SETUP.bat**. It checks the connection, creates the tables
   if they are missing, and copies lookups you already ran into Snowflake. It
   should end with `READY`.
4. Double-click **START-MAIA.bat** as usual. It finds `snowflake.txt` and switches to
   Snowflake. From then on every lookup lands in `EQUIPMENT_DATA`.

With no `snowflake.txt`, nothing changes: results stay in the local folder.

## Sign-in options

| `snowflake.txt` | When to use | Stored on the PC |
|---|---|---|
| `authenticator=externalbrowser` | Your company SSO (Azure AD / Okta). A browser opens the first time. | nothing secret |
| `private_key_file=…p8` (+ `private_key_passphrase=`) | A service account | the key file |
| `password=` | Password, or a Snowflake programmatic access token (PAT) | the password/PAT |

Environment variables override the file: `MAIA_SNOWFLAKE_ACCOUNT`, `…_USER`,
`…_ROLE`, `…_WAREHOUSE`, `…_DATABASE`, `…_SCHEMA`, `…_AUTHENTICATOR`, and for
secrets only *references*: `MAIA_SNOWFLAKE_PASSWORD_REF=env://VAR` or
`file://path`, `MAIA_SNOWFLAKE_PRIVATE_KEY_REF=file://path`.

`snowflake.txt`, `*.p8` and `keys/` are excluded from git and from the release
package. The scripts print only the account, user, sign-in *method*, role,
warehouse, database and schema. Driver error text is scrubbed of the configured
secrets before it is logged.

## What lands where

| Table | One row per | Written |
|---|---|---|
| `EQUIPMENT_DATA` | serial × source | MERGE on every successful lookup (and `NOT_FOUND` markers) |
| `EQUIPMENT_DATA_HISTORY` | change | only when `DATA_HASH` changes, so it records changes, not repeat lookups |
| `AUTOMATION_RUNS` | lookup run | at start and at the outcome (the per-second progress stays local) |
| `AUTOMATION_RUN_CLAIMS` | in-flight lookup | while a lookup runs, so two users share one browser run |

`RAW_DATA` holds everything the page gave (`extracted_fields`, `parts_data`,
`final_url`, …) plus `normalized_record`, the validated record itself. Reads hand
that record back exactly as it was validated, so quality, provenance,
specifications and parts survive the round trip. The typed columns
(`MACHINE_SERIAL_NUMBER`, `ENGINE_BUILD_DATE`, …) are there for SQL and BI.

Only `cat_sis` results are copied in by the backfill. The offline test fixture is
never written to the warehouse.

## Commands

```
python scripts/snowflake/snowflake_setup.py            # setup = apply + check + backfill
python scripts/snowflake/snowflake_setup.py check      # connection, tables, columns, write probe
python scripts/snowflake/snowflake_setup.py apply      # create/upgrade tables and views (idempotent)
python scripts/snowflake/snowflake_setup.py backfill [--dry-run]
python scripts/snowflake/snowflake_setup.py show --serial JAZ01865
python scripts/snowflake/snowflake_setup.py apply --with-grants   # admin: roles, grants, row policy
```

`SIS-LOOKUP.bat` also saves to Snowflake when `snowflake.txt` is present, then
reads the row back from `EQUIPMENT_DATA` and says so. If the row is not there,
it fails.

## Permissions the Maia role needs

```sql
GRANT USAGE ON WAREHOUSE <wh> TO ROLE <role>;
GRANT USAGE ON DATABASE <db> TO ROLE <role>;
GRANT USAGE, CREATE TABLE, CREATE VIEW ON SCHEMA <db>.<schema> TO ROLE <role>;
-- if an admin creates the tables instead:
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA <db>.<schema> TO ROLE <role>;
```

`DELETE` is required, because a finished run releases its claim row.

Roles, grants and the tenant row-access policy in `002_views_and_governance.sql`
run only with `--with-grants`. Once attached, the row policy hides every row from
any role it does not list, and that includes Maia if Maia connects with a
different role.

## Behaviour under failure

| Situation | What happens |
|---|---|
| `snowflake.txt` incomplete | The service refuses to start and names the missing field. START-MAIA offers to continue with the local folder only. |
| Session expired while idle | Reconnect once, retry the statement, continue. |
| Warehouse suspended / network down during a lookup | `PERSISTENCE_FAILED`. The data is not reported as saved. It is still in the local folder as evidence. |
| Snowflake down before a lookup starts | `DATABASE_ERROR` ("data store unavailable"), before any browser is driven. |
| Local folder unwritable | Logged. The lookup still succeeds because Snowflake holds it. |

## Tests

- `tests/test_snowflake_store.py` covers config precedence, secrets never printable,
  reconnect, error masking, `DATABASE_ERROR` at PERSIST → `PERSISTENCE_FAILED`,
  and the mirror rules.
- `tests/test_snowflake_emulator.py` runs the repository's real SQL, the setup
  script, and a full lookup against **fakesnow**, a local Snowflake emulator on
  DuckDB. This proves the SQL holds together. It is not the live account.
  `snowflake_setup.py check` is the live proof.
