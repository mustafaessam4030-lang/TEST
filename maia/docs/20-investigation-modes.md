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
