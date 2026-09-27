"""Equipment intelligence: identity, age, freshness, completeness, consistency."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from app.analysis.evidence import derived, direct
from app.analysis.stats import pct

IDENTITY_FIELDS = ("machine_serial_number", "machine_build_date", "engine_serial_number",
                   "engine_build_date", "equipment_model", "equipment_type", "manufacturer",
                   "build_date", "parts_manual_url", "operation_manual_url", "source_url")
SERIAL_RE = re.compile(r"^[A-Z0-9]{3,17}$")


def as_date(value: Any) -> date | None:
    """ISO dates as stored ("2014-08-02"; partial "2014-08" / "2014" → first day)."""
    text = str(value or "").strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            return datetime.strptime(text[:n], fmt).date()
        except ValueError:
            continue
    return None


def freshness(snap: Any, as_of: datetime, cfg: dict[str, Any]) -> dict[str, Any]:
    fcfg = cfg.get("freshness") or {}
    if snap.retrieved_at is None:
        return derived(None, calculation="retrieved_at not recorded", snap=snap,
                       status="UNKNOWN")
    age_days = round((as_of - snap.retrieved_at).total_seconds() / 86400.0, 2)
    status = ("FRESH" if age_days <= fcfg.get("fresh_days", 30)
              else "STALE" if age_days > fcfg.get("stale_after_days", 90) else "AGING")
    return derived(age_days, calculation="as_of − retrieved_at (days)", snap=snap,
                   status=status, unit="days")


def analyze_equipment(snap: Any, parts: dict[str, Any], as_of: datetime,
                      cfg: dict[str, Any]) -> dict[str, Any]:
    rec = snap.record
    expected = list((cfg.get("fields") or {}).get("expected") or [])
    critical = list((cfg.get("fields") or {}).get("critical") or [])

    equipment = {"serial_number": direct(snap.serial, field="serial_number", snap=snap)}
    for f in IDENTITY_FIELDS:
        if rec.get(f) not in (None, "", [], {}):
            equipment[f] = direct(rec[f], field=f, snap=snap)
    engine = rec.get("engine_family") or {}
    if isinstance(engine, dict) and engine.get("model"):
        equipment["engine_model"] = direct(engine["model"], field="engine_family.model",
                                           snap=snap)

    populated = [f for f in expected if rec.get(f) not in (None, "")]
    missing = [f for f in expected if rec.get(f) in (None, "")]
    missing_critical = [f for f in critical if rec.get(f) in (None, "")]

    build = as_date(rec.get("machine_build_date")) or as_date(rec.get("build_date"))
    build_field = "machine_build_date" if as_date(rec.get("machine_build_date")) else "build_date"
    age = None
    if build:
        years = round((as_of.date() - build).days / 365.25, 1)
        age = derived(years, calculation=f"(as_of − {build_field}) / 365.25", snap=snap,
                      unit="years", as_of=as_of.date().isoformat())

    mb, eb = as_date(rec.get("machine_build_date")), as_date(rec.get("engine_build_date"))
    build_gap = (derived((mb - eb).days, calculation="machine_build_date − engine_build_date",
                         snap=snap, unit="days") if mb and eb else None)

    # Consistency checks: each PASS / FAIL / UNKNOWN with the values compared.
    checks = []
    ms = rec.get("machine_serial_number")
    checks.append({"check": "machine serial equals the searched serial",
                   "result": "UNKNOWN" if not ms else ("PASS" if ms == snap.serial else "FAIL"),
                   "values": {"searched": snap.serial, "machine_serial_number": ms}})
    es = rec.get("engine_serial_number")
    group_serials = sorted({str(g.get("group_serial")) for g in
                            (snap.parts_data or {}).get("groups") or [] if g.get("group_serial")})
    checks.append({"check": "engine serial names one of the parts groups",
                   "result": "UNKNOWN" if not es or not group_serials
                   else ("PASS" if es in group_serials else "NOT_FOUND"),
                   "values": {"engine_serial_number": es, "group_serials": group_serials}})
    mismatched = parts.get("serial_mismatched_groups") or []
    checks.append({"check": "every parts group belongs to this machine",
                   "result": "PASS" if not mismatched else "FAIL",
                   "values": {"mismatched_groups": mismatched}})

    # Structural completeness of the SIS result: what sections were captured.
    pd = snap.parts_data or {}
    sections = {
        "equipment details block": any(rec.get(f) for f in critical),
        "parts groups": bool(pd.get("groups")),
        "entire-group section": bool(pd.get("entire_group_title")) or any(
            g.get("is_entire_group") for g in pd.get("groups") or []),
        "parts table columns": any(g.get("columns") for g in pd.get("groups") or []),
        "parts table rows": parts["total_records"]["value"] > 0,
        "specifications": bool(rec.get("specifications")),
    }
    return {
        "equipment": equipment,
        "fields": {
            "expected": derived(len(expected), calculation="COUNT(expected fields in config)"),
            "populated": derived(len(populated), calculation="COUNT(expected fields NOT NULL)",
                                 snap=snap, fields=populated),
            "missing": derived(len(missing), calculation="COUNT(expected fields IS NULL)",
                               snap=snap, fields=missing),
            "completeness_pct": derived(pct(len(populated), len(expected)),
                                        calculation="populated / expected × 100", snap=snap),
            "missing_critical": missing_critical,
        },
        "age": age,
        "build_gap_days": build_gap,
        "freshness": freshness(snap, as_of, cfg),
        "consistency": checks,
        "structure": {
            "sections": sections,
            "completeness_pct": derived(pct(sum(sections.values()), len(sections)),
                                        calculation="sections present / sections checked × 100",
                                        snap=snap),
        },
        "serial_valid": bool(SERIAL_RE.match(snap.serial)),
    }
