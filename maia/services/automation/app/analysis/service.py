"""The analysis facade: route → load verified snapshots → analyze → report.

    Maia ─▶ intent router ─▶ SnapshotSource (local store today) ─▶ analyzers
          ─▶ rule engine ─▶ report builder ─▶ Maia

It reads stored results only. It never imports the SIS adapter, never opens a
browser and never calls a model. `as_of` fixes "now", so the same stored data
and the same `as_of` always give the same answer — byte for byte.

Later, a Snowflake-backed SnapshotSource can replace the local one, and an
optional AI layer can take `analyze()`'s structured output as its facts —
neither requires a change here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.agent.resolve import MEDIUM_CONFIDENCE, similarity
from app.analysis.anomaly_analyzer import analyze_anomalies
from app.analysis.comparison_analyzer import compare_equipment
from app.analysis.config import load_config
from app.analysis.data_quality_analyzer import analyze_quality
from app.analysis.equipment_analyzer import SERIAL_RE, analyze_equipment
from app.analysis.evidence import snapshot_ref
from app.analysis.history_analyzer import analyze_history
from app.analysis.insight_engine import run_rules
from app.analysis.parts_analyzer import analyze_parts
from app.analysis.report import render_comparison
from app.analysis.snapshots import SnapshotSource

FOCUS_SUGGESTIONS = ["Show parts", "Show missing data", "Show duplicate parts", "What changed",
                     "Data quality", "Show anomalies"]


class AnalysisService:
    def __init__(self, source: SnapshotSource, cfg: dict[str, Any] | None = None) -> None:
        self.source = source
        self.cfg = cfg or load_config()

    # ── one machine ─────────────────────────────────────────────────────────
    def analyze(self, serial: str, *, as_of: datetime | None = None) -> dict[str, Any]:
        as_of = as_of or datetime.now(timezone.utc)
        serial = (serial or "").strip().upper()
        if not SERIAL_RE.match(serial):
            return {"status": "INVALID_SERIAL", "serial": serial,
                    "message": f"'{serial}' is not a valid equipment serial number."}
        snaps = self.source.snapshots(serial)
        issues = getattr(self.source, "issues_for", lambda s: {})(serial)
        if not snaps:
            if issues:
                return {"status": "DATA_CORRUPTED", "serial": serial, "issues": issues,
                        "message": f"Stored SIS results for {serial} exist but none could be "
                                   f"read as a verified result ({len(issues)} file(s))."}
            return self._no_data(serial)
        latest = snaps[-1]
        parts = analyze_parts(latest, self.cfg)
        equipment = analyze_equipment(latest, parts, as_of, self.cfg)
        quality = analyze_quality(latest, equipment, parts, as_of, self.cfg)
        anomalies = analyze_anomalies(parts, self.cfg)
        history = analyze_history(snaps)
        ctx = {"snap": latest, "snaps": snaps, "equipment": equipment, "parts": parts,
               "quality": quality, "anomalies": anomalies, "history": history, "cfg": self.cfg}
        insights = run_rules(ctx, self.cfg)
        coverage = self._coverage(latest)

        s = {
            "equipment_age_years": (equipment["age"] or {}).get("value"),
            "freshness_days": equipment["freshness"]["value"],
            "freshness_status": equipment["freshness"]["status"],
            "fields_expected": equipment["fields"]["expected"]["value"],
            "fields_populated": equipment["fields"]["populated"]["value"],
            "fields_missing": equipment["fields"]["missing"]["value"],
            "completeness_pct": equipment["fields"]["completeness_pct"]["value"] or 0,
            "structural_completeness_pct": equipment["structure"]["completeness_pct"]["value"],
            "part_records": parts["total_records"]["value"],
            "unique_part_numbers": parts["unique_part_numbers"]["value"],
            "part_groups": parts["groups"]["value"],
            "duplicate_part_numbers": len(parts["duplicate_part_numbers"]["value"]),
            "duplicate_entries": parts["duplicate_entries"]["value"],
            "missing_quantities": parts["missing_quantities"]["value"],
            "missing_descriptions": parts["missing_descriptions"]["value"],
            "missing_part_numbers": parts["missing_part_numbers"]["value"],
            "quality_score_pct": quality["score_pct"],
            "insights": len(insights), "snapshots": len(snaps),
        }
        parts_public = {k: v for k, v in parts.items() if k != "rows"}
        result = {
            "status": "OK", "serial": serial, "as_of": as_of.isoformat(),
            "snapshot": snapshot_ref(latest),
            "equipment": equipment["equipment"],
            "summary": s,
            "statistics": {k: parts[k] for k in ("total_records", "unique_part_numbers", "groups",
                                                 "group_size_stats", "largest_group",
                                                 "records_per_group", "quantity_total")},
            "equipment_analysis": equipment,
            "parts_analysis": parts_public,
            "data_quality": quality,
            "anomalies": anomalies,
            "history": history,
            "changes": history.get("comparison"),
            "insights": insights,
            "coverage": coverage,
            "classification": self._classification(equipment, s),
            "issues": issues,
        }
        # the report reads parts rows through parts_analysis for missing lines
        result["parts_analysis"]["rows_count"] = len(parts["rows"])
        return result

    def _coverage(self, snap: Any) -> list[dict[str, Any]]:
        out = []
        for area in self.cfg.get("coverage") or []:
            if area.get("visual_only"):
                status = "VISUAL_ONLY"
            else:
                status = "CAPTURED" if any(snap.record.get(f) or snap.document.get(f)
                                           for f in area.get("fields") or []) else "NOT_CAPTURED"
            out.append({"area": area["area"], "status": status})
        return out

    @staticmethod
    def _classification(equipment: dict[str, Any], s: dict[str, Any]) -> dict[str, Any]:
        direct = [{"field": k, "value": v["value"]} for k, v in
                  sorted(equipment["equipment"].items())]
        calc = {"equipment_age_years": "(as_of − build date) / 365.25",
                "freshness_days": "as_of − retrieved_at",
                "completeness_pct": "populated / expected × 100",
                "part_records": "COUNT(part_rows)",
                "unique_part_numbers": "COUNT(DISTINCT part_number)",
                "part_groups": "COUNT(DISTINCT group)",
                "duplicate_part_numbers": "COUNT(part_number HAVING COUNT > 1)",
                "quality_score_pct": "Σ(weight × component) / Σ(weight)"}
        derived = [{"metric": k, "value": s[k], "calculation": c} for k, c in calc.items()]
        return {"direct": direct, "derived": derived}

    def _no_data(self, serial: str) -> dict[str, Any]:
        candidates = []
        for known in self.source.serials():
            score, why = similarity(serial, known)
            if score >= MEDIUM_CONFIDENCE and known != serial:
                candidates.append({"serial": known, "similarity": round(score, 2),
                                   "why": why})
        candidates.sort(key=lambda c: (-c["similarity"], c["serial"]))
        msg = f"There is no verified SIS result stored for {serial} yet."
        if candidates:
            msg += (" Stored serials that look similar (not substituted): "
                    + ", ".join(c["serial"] for c in candidates[:3])
                    + f". Did you mean {candidates[0]['serial']}?")
        return {"status": "NO_DATA", "serial": serial, "candidates": candidates[:3],
                "message": msg}

    # ── two machines ────────────────────────────────────────────────────────
    def compare(self, a: str, b: str, *, as_of: datetime | None = None) -> dict[str, Any]:
        as_of = as_of or datetime.now(timezone.utc)
        ra, rb = self.analyze(a, as_of=as_of), self.analyze(b, as_of=as_of)
        for r in (ra, rb):
            if r["status"] != "OK":
                return {**r, "status": r["status"], "compare": [a, b]}
        sa = self.source.snapshots(a)[-1]
        sb = self.source.snapshots(b)[-1]
        comp = compare_equipment(sa, sb, ra["data_quality"], rb["data_quality"],
                                 ra["equipment_analysis"]["freshness"],
                                 rb["equipment_analysis"]["freshness"])
        return {"status": "OK", "as_of": as_of.isoformat(), "comparison": comp,
                "text": render_comparison(comp)}

    # ── natural language, deterministically ─────────────────────────────────
    def handle(self, utterance: str, *, active_serial: str | None = None,
               as_of: datetime | None = None) -> dict[str, Any]:
        """Routed through the investigation modes (PARTS / TROUBLESHOOTING / FULL)."""
        from app.analysis.investigation import InvestigationService

        return InvestigationService(self.source, self.cfg).handle(
            utterance, active_serial=active_serial, as_of=as_of)
