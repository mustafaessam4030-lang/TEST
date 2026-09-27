# 19 · Maia's deterministic analysis engine

Maia analyses the **verified SIS results already stored** in `logs/sis-results/`.
There is no AI model, no Snowflake and no browser in this path. The same
stored data and the same "as of" date always give the same result.

```
User → Maia chat → intent router (EN/AR keywords + serial extraction)
     → LocalStoreSnapshots (read-only, verified cat_sis results only)
     → analyzers → rule engine → report builder → Maia
```

| Module (`services/automation/app/analysis/`) | Purpose |
|---|---|
| `snapshots.py` | loads stored results; rejects corrupted files, test-fixture sources, `sample` files and not-found markers |
| `equipment_analyzer.py` | identity (DIRECT), age, freshness, completeness, consistency checks, SIS section coverage |
| `parts_analyzer.py` | totals, unique parts, groups, duplicates, repeats across groups, missing part numbers / descriptions / quantities, categories |
| `data_quality_analyzer.py` | transparent weighted score; refuses to score when there is too little data |
| `anomaly_analyzer.py` | group-size IQR / z-score, dominant groups, concentration of missing values — observations about the data only |
| `history_analyzer.py` | previous vs latest snapshot: fields, parts, quantities, groups, serials, dates, metadata |
| `comparison_analyzer.py` | two machines: IDENTICAL / DIFFERENT / MISSING per attribute, shared and unique parts |
| `insight_engine.py` | registered rules, switched on/off and tuned in `config/analysis.yaml` |
| `report.py` | business-readable report, and focused sections |
| `intent.py` | deterministic router: analyze, parts, missing, duplicates, quality, anomalies, changes, summary, compare, raw json |

**Evidence.** Values read from SIS are `DIRECT`. Every count, percentage,
score and comparison is `DERIVED` and names its calculation. Every insight
carries the source, run ID and retrieval time of the snapshot or snapshots
it came from.

**Wording.** Insights describe the dataset ("Retrieved records show…").
They never claim failure, safety, maintenance or condition, and a test
enforces this.

**Use it:**

- In the chat: *Analyze JAZ01865*, *show parts*, *what changed*,
  *data quality*, *show anomalies*, *compare JAZ01865 and JAZ01866*,
  *حلل المعدة JAZ01865*, *ايه القطع الناقصة؟*
- Double-click `ANALYZE.bat`.
- `python scripts/analysis/maia_analyze.py "Analyze JAZ01865"`
- Add `--sample` for clearly marked demo data.

If the serial has no stored result yet, the chat first retrieves it from SIS
through the existing flow, then analyses what was saved.

**API:** `POST /v1/analysis/ask`, `GET /v1/analysis/{serial}`,
`GET /v1/analysis/compare/{a}/{b}`.

**Later.** A Snowflake-backed `SnapshotSource` (`serials()`, `snapshots()`)
can replace the local one, and an optional AI layer can explain
`analyze()`'s structured output. The deterministic results remain the source
of truth.

**Not captured yet.** SIS Troubleshooting (codes, events, symptoms), service
and repair documents, and product status reports. Coverage reports them as
"not captured". Capturing them is a separate SIS-retrieval phase that needs a
guided capture; no selectors are guessed.
