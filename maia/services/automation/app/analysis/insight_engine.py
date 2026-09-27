"""A small, extensible rule engine that turns analysis results into insights.

    @rule("duplicate_part")
    def _(ctx, cfg): ...  → list of insights

Rules are registered in code and switched on/off, tuned and typed from
config/analysis.yaml (`rules:`). Adding a rule is one function; disabling one is
one line of YAML.

Language rules for every message: describe the DATA ("Retrieved records
show…", "The dataset contains…"), never the machine's condition. A rule may
not claim a failure, a safety issue or a maintenance need — the SIS data this
engine reads does not contain those facts. A test enforces the wording.
"""
from __future__ import annotations

from typing import Any, Callable

from app.analysis.evidence import snapshot_ref

RuleFn = Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]]
REGISTRY: dict[str, RuleFn] = {}
SEVERITY_ORDER = {"warning": 0, "notice": 1, "info": 2}


def rule(rule_id: str) -> Callable[[RuleFn], RuleFn]:
    def register(fn: RuleFn) -> RuleFn:
        REGISTRY[rule_id] = fn
        return fn
    return register


def _insight(ctx: dict[str, Any], rcfg: dict[str, Any], rule_id: str, text: str, *,
             classification: str, calculation: str, values: Any = None,
             snaps: list[Any] | None = None) -> dict[str, Any]:
    evidence: dict[str, Any] = {"source": "SIS", "calculation": calculation}
    if snaps:
        evidence["snapshots"] = [snapshot_ref(s) for s in snaps]
    else:
        evidence.update(snapshot_ref(ctx["snap"]))
    if values is not None:
        evidence["values"] = values
    return {"rule": rule_id, "insight": text, "type": rcfg.get("type", "INFO"),
            "severity": rcfg.get("severity", "info"), "classification": classification,
            "evidence": evidence}


def _names(items: list[str], limit: int) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


# ── rules ───────────────────────────────────────────────────────────────────
@rule("missing_critical_field")
def _missing_critical(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for f in ctx["equipment"]["fields"]["missing_critical"]:
        label = f.replace("_", " ").capitalize()
        out.append(_insight(ctx, rcfg, "missing_critical_field",
                            f"{label} is missing from the retrieved SIS data.",
                            classification="DIRECT", calculation=f"{f} IS NULL",
                            values={"field": f}))
    return out


@rule("duplicate_part")
def _duplicate(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    dup = ctx["parts"]["duplicate_part_numbers"]["value"]
    if not dup:
        return []
    top = sorted(dup.items(), key=lambda kv: (-kv[1], kv[0]))
    listed = _names([f"{pn} (×{n})" for pn, n in top], int(rcfg.get("max_listed", 10)))
    return [_insight(ctx, rcfg, "duplicate_part",
                     f"{len(dup)} part number(s) appear more than once: {listed}.",
                     classification="DERIVED", calculation="COUNT(part_number) > 1",
                     values=dict(top))]


@rule("repeated_across_groups")
def _across(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    across = ctx["parts"]["repeated_across_groups"]["value"]
    if not across:
        return []
    items = [f"{pn} ({len(gs)} groups)" for pn, gs in across.items()]
    return [_insight(ctx, rcfg, "repeated_across_groups",
                     f"{len(across)} part number(s) appear across multiple SIS groups: "
                     f"{_names(items, int(rcfg.get('max_listed', 10)))}.",
                     classification="DERIVED",
                     calculation="COUNT(DISTINCT group) > 1 per part_number", values=across)]


@rule("missing_description")
def _missing_desc(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    parts = ctx["parts"]["missing_description_parts"]
    n = ctx["parts"]["missing_descriptions"]["value"]
    if not n:
        return []
    return [_insight(ctx, rcfg, "missing_description",
                     f"{n} part record(s) have no description in the retrieved data"
                     + (f" (e.g. {_names(parts, int(rcfg.get('max_listed', 5)))})." if parts
                        else "."), classification="DERIVED",
                     calculation="part_number IS NOT NULL AND description IS NULL",
                     values=parts)]


@rule("missing_quantity")
def _missing_qty(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    n = ctx["parts"]["missing_quantities"]["value"]
    if not n:
        return []
    parts = ctx["parts"]["missing_quantity_parts"]
    return [_insight(ctx, rcfg, "missing_quantity",
                     f"{n} part record(s) have no quantity recorded"
                     + (f" (e.g. {_names(parts, int(rcfg.get('max_listed', 5)))})." if parts
                        else "."), classification="DERIVED",
                     calculation="part_number IS NOT NULL AND quantity IS NULL", values=parts)]


@rule("quantity_column_absent")
def _no_qty_col(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    p = ctx["parts"]
    if not p["rows"] or "quantity_required" in p["columns_published"]:
        return []
    return [_insight(ctx, rcfg, "quantity_column_absent",
                     "The SIS parts table for this result does not publish a quantity column, "
                     "so quantities cannot be analysed.", classification="DIRECT",
                     calculation="no 'Quantity' header in any parts table",
                     values=sorted(p["columns_published"]))]


@rule("missing_part_number")
def _missing_pn(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    n = ctx["parts"]["missing_part_numbers"]["value"]
    if not n:
        return []
    return [_insight(ctx, rcfg, "missing_part_number",
                     f"{n} part record(s) have no part number.", classification="DERIVED",
                     calculation="COUNT(rows WHERE part_number IS NULL)",
                     values=ctx["parts"]["missing_part_number_rows"][:20])]


@rule("exact_duplicate_rows")
def _exact(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    rows = ctx["parts"]["exact_duplicate_rows"]["value"]
    if not rows:
        return []
    return [_insight(ctx, rcfg, "exact_duplicate_rows",
                     f"{len(rows)} part row(s) are repeated identically within the same group "
                     "in the retrieved data.", classification="DERIVED",
                     calculation="identical cells within one group, COUNT > 1", values=rows)]


@rule("stale_data")
def _stale(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    fr = ctx["equipment"]["freshness"]
    if fr.get("status") != "STALE":
        return []
    return [_insight(ctx, rcfg, "stale_data",
                     f"The stored SIS result is stale: retrieved {fr['value']:g} days ago "
                     f"(threshold {ctx['cfg']['freshness']['stale_after_days']} days).",
                     classification="DERIVED", calculation="as_of − retrieved_at > "
                     "stale_after_days", values={"age_days": fr["value"]})]


@rule("serial_mismatch")
def _serial_mismatch(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for c in ctx["equipment"]["consistency"]:
        if c["result"] == "FAIL":
            out.append(_insight(ctx, rcfg, "serial_mismatch",
                                f"Consistency check failed: {c['check']}.",
                                classification="DERIVED", calculation=c["check"],
                                values=c["values"]))
    return out


@rule("engine_group_match")
def _engine_group(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    for c in ctx["equipment"]["consistency"]:
        if c["check"].startswith("engine serial") and c["result"] == "PASS":
            es = c["values"]["engine_serial_number"]
            return [_insight(ctx, rcfg, "engine_group_match",
                             f"The engine serial {es} matches a parts group in the retrieved "
                             "data, so the engine and machine records are consistent.",
                             classification="DERIVED",
                             calculation="engine_serial_number ∈ group serials",
                             values=c["values"])]
    return []


@rule("large_group")
def _large(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [_insight(ctx, rcfg, "large_group",
                     f"Group '{g['group']}' contains significantly more records than the "
                     f"median group ({g['records']} vs median {g['median']:g}"
                     + (f", z = {g['z']}" if g.get("z") is not None else "") + ").",
                     classification="DERIVED",
                     calculation=ctx["anomalies"]["method"].get("rule", "IQR / z-score"),
                     values=g) for g in ctx["anomalies"]["large_groups"]]


@rule("small_group")
def _small(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [_insight(ctx, rcfg, "small_group",
                     f"Group '{g['group']}' contains unusually few records "
                     f"({g['records']} vs median {g['median']:g}).", classification="DERIVED",
                     calculation=ctx["anomalies"]["method"].get("rule", "IQR / z-score"),
                     values=g) for g in ctx["anomalies"]["small_groups"]]


@rule("dominant_group")
def _dominant(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [_insight(ctx, rcfg, "dominant_group",
                     f"Group '{g['group']}' contains {g['share_pct']:g}% of all retrieved part "
                     f"records ({g['records']} of {ctx['parts']['total_records']['value']}).",
                     classification="DERIVED", calculation="records in group / total records",
                     values=g) for g in ctx["anomalies"]["dominant_groups"]]


@rule("empty_group")
def _empty(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    groups = ctx["parts"]["empty_groups"]["value"]
    if not groups:
        return []
    return [_insight(ctx, rcfg, "empty_group",
                     f"{len(groups)} group(s) were retrieved with no part rows: "
                     f"{_names(groups, 5)}.", classification="DERIVED",
                     calculation="COUNT(part_rows) = 0 per group", values=groups)]


@rule("missing_concentration")
def _concentration(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [_insight(ctx, rcfg, "missing_concentration",
                     f"{m['share_pct']:g}% of the missing {m['column'].replace('_', ' ')} "
                     f"values are in group '{m['group']}' ({m['gaps_in_group']} of "
                     f"{m['gaps_total']}).", classification="DERIVED",
                     calculation="gaps in group / gaps in column", values=m)
            for m in ctx["anomalies"]["missing_concentration"]]


@rule("history_changes")
def _history(ctx: dict[str, Any], rcfg: dict[str, Any]) -> list[dict[str, Any]]:
    comp = (ctx.get("history") or {}).get("comparison")
    if not comp:
        return []
    snaps = ctx["snaps"][-2:]
    if comp["identical_data"]:
        text = "Compared with the previous snapshot, the retrieved data is identical."
    else:
        bits = []
        if comp["parts_added"]:
            bits.append(f"+{len(comp['parts_added'])} parts")
        if comp["parts_removed"]:
            bits.append(f"−{len(comp['parts_removed'])} parts")
        if comp["quantity_changes"]:
            bits.append(f"{len(comp['quantity_changes'])} quantities changed")
        if comp["fields_changed"]:
            bits.append(f"{len(comp['fields_changed'])} field(s) changed")
        if comp["fields_added"]:
            bits.append(f"{len(comp['fields_added'])} new field(s)")
        if comp["fields_removed"]:
            bits.append(f"{len(comp['fields_removed'])} removed field(s)")
        text = ("Compared with the previous snapshot: " + ", ".join(bits) + "."
                if bits else "Compared with the previous snapshot, only metadata changed.")
    return [_insight(ctx, rcfg, "history_changes", text, classification="DERIVED",
                     calculation=comp["calculation"], snaps=snaps)]


# ── running the rules ───────────────────────────────────────────────────────
def run_rules(ctx: dict[str, Any], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    insights: list[dict[str, Any]] = []
    for rule_id, rcfg in (cfg.get("rules") or {}).items():
        rcfg = rcfg or {}
        if not rcfg.get("enabled", True) or rule_id not in REGISTRY:
            continue
        insights.extend(REGISTRY[rule_id](ctx, rcfg))
    # Deterministic order: severity, then type, then text.
    insights.sort(key=lambda i: (SEVERITY_ORDER.get(i["severity"], 9), i["type"], i["insight"]))
    for n, item in enumerate(insights, 1):
        item["id"] = f"I{n}"
    return insights
