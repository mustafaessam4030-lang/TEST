"""Render SERVICE_METRIC_DEFINITIONS.md from config/metrics.yaml so the document cannot drift from the code.

    python -m si.metric_docs > SERVICE_METRIC_DEFINITIONS.md     (a test fails if the file is stale)
"""
from __future__ import annotations

from . import contract
from .config import ROOT
from .registry import load_registry

HEADER = """# Service metric definitions

Generated from `config/metrics.yaml` (registry version {version}) by `python -m si.metric_docs`.
Do not edit by hand; `tests/test_docs.py` fails if this file and the registry disagree.

These are the only KPI calculations in the application. The Analysis page, the
recommendation engine and the AI Assistant all compile them through
`si/query.py`, so the same question returns the same number everywhere.

**Status of these definitions.** No existing company KPI definitions were found
in the repository or in any accessible Snowflake object (see
SERVICE_INTELLIGENCE_AUDIT.md). These are proposed, conventional definitions over
the data contract below. Each needs confirmation from the service-operations
owner before it is reported as an official KPI.

## Conventions

- **Data source.** One governed view (`SI_CONTRACT_VIEW`) with the contract columns below, generated from a reviewed source mapping.
- **Time.** Contract timestamps are UTC (`TIMESTAMP_NTZ`). Reporting periods are whole days in the reporting time zone (`SI_REPORTING_TZ`); start inclusive, end exclusive. Trend buckets are truncated in the reporting time zone.
- **Period vs current-state.** *Period* metrics count records whose time column falls in the period, and are compared with the previous period of equal length. *Current-state* metrics describe open work as of the last data refresh (`as_of` = latest load time, else latest source update, else latest creation time). They have no history, because the source has no status history.
- **Nulls.** A record with a null dimension value is grouped as `(not set)`; it is never dropped silently. Records with a close time earlier than their creation time are excluded from duration metrics and counted under data quality.
- **Duplicates.** The contract view keeps one row per `request_id` when the mapping defines `dedupe.order_by`; duplicates are reported under data quality.
- **Small samples.** Figures based on fewer than `min_sample` records are flagged and never ranked or used by a recommendation detector.
- **Status classes.** `status_class` is OPEN, CLOSED or CANCELLED according to the reviewed status mapping; any other value is UNMAPPED, excluded from status-based metrics and counted under data quality.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
"""


def render() -> str:
    reg = load_registry(ROOT / "config" / "metrics.yaml")
    out = [HEADER.format(version=reg.version)]
    for name, p in reg.parameters.items():
        out.append(f"| `{name}` | {p['default']} | {' '.join(str(p.get('description', '')).split())} |\n")
    out.append("\n## Data contract\n\n| Column | Type | Required | Meaning |\n|---|---|---|---|\n")
    for c in contract.COLUMNS:
        out.append(f"| `{c.name}` | {c.sql_type} | {'yes' if c.required else 'no'} | {c.description} |\n")
    out.append("\n## Metrics\n")
    for m in reg.metrics.values():
        out.append(f"\n### {m.name} (`{m.id}`)\n\n{m.description}\n\n")
        out.append("| | |\n|---|---|\n")
        out.append(f"| Kind | {'period' if m.kind == 'period' else 'current state'} |\n")
        if m.time_column:
            out.append(f"| Counted by | `{m.time_column}` in the reporting period |\n")
        out.append(f"| Calculation | `{m.expression}` |\n")
        out.append(f"| Records included | {'`' + m.filter + '`' if m.filter else 'all'} |\n")
        out.append(f"| Unit | {m.unit} |\n| Better when | {m.direction.replace('_', ' ')} |\n")
        out.append(f"| Requires | {', '.join('`' + c + '`' for c in m.requires)} |\n")
        if m.min_sample:
            out.append(f"| Minimum sample | {m.min_sample} records |\n")
        out.append("| Tests | `tests/test_kpis.py` (hand-computed fixture), `tests/test_chat.py` / `tests/test_api.py` (dashboard = assistant) |\n")
        if m.limitations:
            out.append("\nLimitations:\n\n" + "".join(f"- {l}\n" for l in m.limitations))
    out.append("""
## Data-quality checks (Analysis page, `missing_data` answers, data-quality recommendation)

| Check | Rule |
|---|---|
| Completeness | `COUNT(column) / COUNT(*)` for every mapped contract column |
| Duplicate ids | `COUNT(*) - COUNT(DISTINCT request_id)` |
| Unmapped status | `status_class = 'UNMAPPED'` |
| Closed before created | `closed_at < created_at` |
| Closed without time | `status_class = 'CLOSED' AND closed_at IS NULL` |
| Open with close time | `status_class = 'OPEN' AND closed_at IS NOT NULL` |
| Freshness | `as_of` older than 36 hours is shown as stale |
""")
    return "".join(out)


if __name__ == "__main__":
    print(render(), end="")
