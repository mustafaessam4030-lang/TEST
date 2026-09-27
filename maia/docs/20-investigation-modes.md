# 20 · Investigation modes: Parts · Troubleshooting · Full, plus the 3D model

No Snowflake, no Cortex, no LLM. Everything is deterministic.

```
Maia → Investigation router (EN/AR keywords + serial) → state {serial, mode, filters}
        ├── PARTS            parts analysis · part / group / text search
        ├── TROUBLESHOOTING  codes → component → condition · code search
        └── FULL             overview + parts + troubleshooting + quality
                      └── 3D MODEL (shared step): viewer discovery → mapping → highlight
```

- **"Analyze JAZ01865"** → Maia asks which investigation you want: 🔧 Parts ·
  ⚠️ Troubleshooting · 📊 Full Analysis.
- **Data needed but not read yet.** The analysis side returns
  `NEEDS_RETRIEVAL`. The chat then runs the existing SIS lookup with
  `investigate: ["troubleshooting", "model_3d"]`: one run, the same browser,
  the same signed-in session, with an extra `INVESTIGATE` step after the
  record is extracted. After that, Maia answers from the stored result.
- **Troubleshooting** (`app/adapters/sis_investigation.py`):
  - Opens the Troubleshooting tab, found by its exact label in the record's
    tab bar.
  - Expands each "Codes, Events, & Symptoms" section, e.g.
    "Troubleshooting (30)".
  - Reads every row verbatim (DIRECT) and scrolls long lists.
  - `troubleshooting_analyzer.py` splits code / component / condition. The
    split is DERIVED and names its method. System is filled only if SIS
    shows it.
- **3D model**:
  - The tab is instrumented, not assumed. The investigation records canvases,
    WebGL, known viewer APIs (Autodesk, HOOPS, Babylon), model files on the
    network, DOM component trees, and a click probe.
  - Component names come only from the viewer itself.
    `component_mapper.py` maps them deterministically: exact, alias, same
    words, or part-number token.
  - Outcomes:
    - **VERIFIED**: the component is highlighted and focused through the
      viewer's own API.
    - **AMBIGUOUS**: "3D component mapping is ambiguous."
    - **NOT_AVAILABLE**: the viewer exposes no metadata, and Maia says so.
- **Wording**: "Troubleshooting code 36-1-5 identifies Cylinder #1 Injector
  with Current Below Normal." Never "has failed".

**Real check on your PC:** `INVESTIGATE.bat` (or
`python scripts/e2e/investigate_real.py --serial JAZ01865`). It prints a
PASS/FAIL table for REAL SIS, PARTS, TROUBLESHOOTING, 3D MODEL and 3D
COMPONENT MAPPING, and saves the 3D viewer discovery report to
`logs/sis-results/<SERIAL>_<RUN>/investigation.json`.

## Executing a turn against real SIS

`app/services/troubleshooting_service.py` — `InvestigationRunner`:

    get_troubleshooting("JAZ01865")                 # the troubleshooting answer
    ask("Check the troubleshooting for JAZ01865")   # whatever the router decides

1. validates the serial; routes the sentence (intent + serial)
2. answers from the stored, verified SIS result when it already holds the section
3. otherwise calls the existing `EquipmentService.lookup` (FORCE_REFRESH,
   `investigate=[troubleshooting, model_3d]`) — same adapter, browser pool and saved
   session; the existing store saves it
4. answers from that saved run, with a `trace` (intent, serial, tool, SIS page, sections)

Failures come back with the real reason (`SIS session unavailable…`, `Equipment not
found in SIS`, `Extraction failed…`, `the SIS Troubleshooting page could not be read — …`,
`no troubleshooting codes found…`), never a clarification. A result that is not from
Caterpillar SIS is refused.

HTTP: `POST /v1/investigation/ask`, `GET /v1/troubleshooting/{serial}`. The chat keeps
its live progress panel: it asks `/v1/analysis/ask`, runs the lookup with the sections
it names (up to two rounds), then asks again.

## Row reading on the live page

* a section that is already open is read first — its heading is clicked only when no
  rows show (a click would collapse an open section)
* one entry per code: a row is widened to the largest element still holding only that
  code, so lines SIS shows with it stay with it; each row keeps its visible `lines`
* **System** is filled only from SIS text: a label shown above a run of codes, or the one
  remaining line of the code's row — the method is recorded (`system_method`); otherwise
  "not shown by SIS"
* a structural `panel_sketch` (tags, roles, classes, short text — no URLs, no inputs) is
  saved with the run for diagnosing an unexpected layout

## Two kinds of test — never confused

| | What it proves |
|---|---|
| **Replica tests** (`tests/test_investigation_browser.py`, fixtures `sis_tabs_replica*.html`) | Maia's own browser code works in real Chromium on a layout built from screenshots. Says nothing about SIS. |
| **Real SIS integration** (`INVESTIGATE.bat` / `scripts/e2e/investigate_real.py`) | The live Caterpillar SIS run: page reached, rows extracted, 3D viewer inspected. Only this may be called "real SIS integration passed". |
