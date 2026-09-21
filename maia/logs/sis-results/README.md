# logs/sis-results — the temporary local store

Written by `LocalJsonRepository` (`services/automation/app/repositories/local_json_repo.py`)
after every **successful** lookup. It is the active `EquipmentRepository` while
the Snowflake warehouse is being built; swapping it out is one setting
(`MAIA_REPOSITORY=snowflake`) and changes nothing in Maia or the automation.

```
<SERIAL>_<RUN_ID>.json     the record + the COMPLETE raw extraction
<SERIAL>_<RUN_ID>.txt      the same values, laid out for a person
<SERIAL>_<RUN_ID>/
    page.png               the detail page as it first rendered
    details.png            the equipment-details section, after scrolling
    parts.png              the "Product - …" parts group
_index.json                (source, serial) -> the newest result file
_runs/<RUN_ID>.json        the run record: every step, its timing and outcome
```

Three things this folder guarantees:

* **Nothing is discarded.** `raw_data.extracted_fields` holds every field the
  page gave, including ones the canonical schema has no column for.
* **A missing value is `null`** — never an empty string, never a guess — and
  `field_provenance` records *why* it is null.
* **A failed write is a failed lookup.** If the JSON cannot be saved the run
  returns `PERSISTENCE_FAILED`; Maia is never told a lookup succeeded when
  nothing was stored.

Nothing secret is ever written here: every payload is scrubbed of
credential-shaped keys and text before it is saved.

**These files are real equipment data.** They are git-ignored and excluded from
the release package: they stay on the machine that produced them.
