"""The controlled tools Cortex may ask for. Deterministic, validated, audited.

The model names a tool and its arguments. This module decides everything else:

  * which serials are allowed this turn — only the one the gateway resolved and
    the user confirmed. A model that "corrects" JAZ01856 to JAZ01865 is refused;
    the user is asked instead.
  * whether a browser runs. `get_equipment_data` is store-first with SIS as the
    fallback (EquipmentService: store → freshness → SIS → validate → persist).
    `refresh_equipment_from_sis` runs the browser only when the user asked for
    fresh data or the stored copy is missing or stale, and at most once a turn.
    Retries belong to the SIS worker, never to the model.
  * what counts as a fact. Every value returned carries an evidence type:
      DIRECT   — a value exactly as Snowflake / SIS returned it
      DERIVED  — counted, filtered or compared from DIRECT values, by code here
    INFERRED is only ever the model's own reasoning and is labelled at the end.

No tool takes SQL, a URL, a selector or a credential as an argument.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable

from app.core.errors import AutomationError, ErrorCode
from app.core.logging import log
from app.domain.parts import flatten_parts, product_of
from app.domain.validate import validate_serial

logger = logging.getLogger(__name__)

#: platform error code → the status vocabulary Maia reports
STATUS_OF: dict[str, str] = {
    "SERIAL_NOT_FOUND": "NOT_FOUND", "LOGIN_FAILED": "LOGIN_FAILED",
    "SESSION_EXPIRED": "LOGIN_FAILED", "MFA_REQUIRED": "MFA_REQUIRED",
    "CAPTCHA_DETECTED": "CAPTCHA_REQUIRED", "WEBSITE_CHANGED": "WEBSITE_CHANGED",
    "EXTRACTION_ERROR": "EXTRACTION_FAILED", "INVALID_DATA": "VALIDATION_FAILED",
    "PERSISTENCE_FAILED": "PERSISTENCE_FAILED", "CORTEX_UNAVAILABLE": "CORTEX_UNAVAILABLE",
}

CORE_FIELDS = ("equipment_model", "equipment_type", "manufacturer", "build_date",
               "machine_serial_number", "machine_build_date", "engine_serial_number",
               "engine_build_date")
BUILD_FIELDS = ("machine_serial_number", "machine_build_date", "engine_serial_number",
                "engine_build_date", "build_date")
MAX_PART_FACTS = 150


# ── the turn's evidence ─────────────────────────────────────────────────────
@dataclass
class FactBook:
    """Every fact the tools produced this turn, numbered F1, F2, … The answer
    may only cite these; nothing else is evidence."""

    facts: list[dict[str, Any]] = field(default_factory=list)

    def add(self, statement: str, *, evidence: str, field_name: str | None = None,
            value: Any = None, meta: dict[str, Any] | None = None,
            locator: str | None = None) -> str:
        fid = f"F{len(self.facts) + 1}"
        meta = meta or {}
        self.facts.append({
            "id": fid, "statement": statement, "evidence": evidence,
            "field": field_name, "value": value, "locator": locator,
            "source": meta.get("source_label"), "retrieved_at": meta.get("retrieved_at"),
            "automation_run_id": meta.get("automation_run_id")})
        return fid

    def by_id(self) -> dict[str, dict[str, Any]]:
        return {f["id"]: f for f in self.facts}

    def known_values(self) -> set[str]:
        """Every literal a correct answer could quote: values, and the tokens
        inside each statement (part numbers, serials, dates, counts)."""
        out: set[str] = set()
        for f in self.facts:
            for text in (f.get("value"), f.get("statement")):
                if text is None:
                    continue
                if isinstance(text, (list, tuple)):
                    out.update(str(t).upper() for t in text)
                    continue
                s = str(text)
                out.add(s.upper())
                out.update(tok.upper() for tok in re.findall(r"[A-Za-z0-9./-]+", s))
        return out


@dataclass
class ToolContext:
    service: Any                       # EquipmentService
    repo: Any
    #: serials the gateway resolved/confirmed this turn — the only ones allowed
    allowed_serials: set[str]
    #: the user explicitly asked for fresh data ("refresh", "latest from SIS")
    wants_fresh: bool = False
    source: str = "cat_sis"
    facts: FactBook = field(default_factory=FactBook)
    trace: list[dict[str, Any]] = field(default_factory=list)
    refreshed: bool = False
    #: records read this turn, by serial — so a second tool never re-drives SIS
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    sis_runs: int = 0


class ToolRefused(Exception):
    """The request broke a rule (wrong serial, bad argument). Returned to the
    model as an error result, never executed."""


# ── helpers ─────────────────────────────────────────────────────────────────
def _serial(ctx: ToolContext, args: dict[str, Any]) -> str:
    raw = str(args.get("serial_number") or "").strip()
    if not raw:
        raise ToolRefused("serial_number is required")
    try:
        serial = validate_serial(raw)
    except AutomationError as err:
        raise ToolRefused(err.message) from err
    if serial not in ctx.allowed_serials:
        # The model may not pick a machine the user did not name or confirm.
        raise ToolRefused(
            f"{serial} is not the machine the user asked about "
            f"({', '.join(sorted(ctx.allowed_serials)) or 'none confirmed'}). "
            "Do not substitute serials; ask the user instead.")
    return serial


def _meta(resp: Any, origin: str) -> dict[str, Any]:
    a = resp.attribution
    return {"source": a.source, "source_label": a.source_label,
            "retrieved_at": a.retrieved_at.isoformat(), "automation_run_id":
            a.automation_run_id, "freshness": a.freshness.value, "age_days": a.age_days,
            "origin": origin}


def _error(tool: str, serial: str | None, err: AutomationError) -> dict[str, Any]:
    code = err.code.value
    return {"tool": tool, "ok": False, "serial_number": serial,
            "status": STATUS_OF.get(code, code), "error_code": code,
            "message": err.message,
            "automation_run_id": err.details.get("automation_run_id"),
            "hint": err.details.get("fix") or err.details.get("user_message_hint")}


async def _record(ctx: ToolContext, serial: str, *, force: bool = False) -> dict[str, Any]:
    """Store-first read with SIS fallback, once per serial per turn."""
    from app.models.schemas import EquipmentSearchRequest, InProgressResponse, SearchMode

    if serial in ctx.records and not force:
        return ctx.records[serial]
    req = EquipmentSearchRequest(
        serial_number=serial, source=ctx.source, wait=True, timeout_ms=120_000,
        mode=SearchMode.FORCE_REFRESH if force else SearchMode.AUTO,
        reason="user_request", requested_by="cortex-agent")
    resp = await ctx.service.lookup(req)
    if isinstance(resp, InProgressResponse):
        raise AutomationError(ErrorCode.TIMEOUT,
                              "A lookup for this serial is already running.",
                              details={"automation_run_id": resp.automation_run_id,
                                       "in_progress": True})
    origin = "store" if resp.cache.hit else "sis"
    if origin == "sis":
        ctx.sis_runs += 1
    entry = {"record": resp.data.model_dump(mode="json"), "meta": _meta(resp, origin)}
    ctx.records[serial] = entry
    return entry


def _record_facts(ctx: ToolContext, serial: str, rec: dict[str, Any], meta: dict[str, Any],
                  fields: tuple[str, ...]) -> tuple[list[str], list[str]]:
    ids, missing = [], []
    for name in fields:
        value = rec.get(name)
        if value in (None, "", [], {}):
            missing.append(name)
            continue
        ids.append(ctx.facts.add(f"{serial} {name} = {value}", evidence="DIRECT",
                                 field_name=name, value=value, meta=meta))
    engine = (rec.get("engine_family") or {}).get("model") if rec.get("engine_family") else None
    if engine and "engine_family" in fields:
        ids.append(ctx.facts.add(f"{serial} engine model = {engine}", evidence="DIRECT",
                                 field_name="engine_family.model", value=engine, meta=meta))
    if missing:
        ids.append(ctx.facts.add(
            f"{serial}: not published by the source this retrieval: {', '.join(missing)}",
            evidence="DERIVED", field_name="missing_fields", value=missing, meta=meta))
    return ids, missing


def _summary(rec: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {k: rec.get(k) for k in fields}


# ── the tools ───────────────────────────────────────────────────────────────
async def get_equipment_data(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    serial = _serial(ctx, args)
    entry = await _record(ctx, serial)
    rec, meta = entry["record"], entry["meta"]
    ids, missing = _record_facts(ctx, serial, rec, meta, CORE_FIELDS + ("engine_family",))
    product = product_of(rec.get("parts_data"))
    if product:
        ids.append(ctx.facts.add(f"{serial} product = {product}", evidence="DIRECT",
                                 field_name="product", value=product, meta=meta))
    parts = flatten_parts(rec.get("parts_data"))
    groups = sorted({p["group_name"] for p in parts if p["group_name"]})
    ids.append(ctx.facts.add(
        f"{serial} has {len(parts)} part rows in {len(groups)} group(s): {', '.join(groups)}",
        evidence="DERIVED", field_name="parts_summary",
        value={"rows": len(parts), "groups": groups}, meta=meta))
    for spec in (rec.get("specifications") or [])[:20]:
        text = spec.get("value_raw") or spec.get("value")
        ids.append(ctx.facts.add(f"{serial} spec {spec.get('name')} = {text}",
                                 evidence="DIRECT", field_name=f"spec.{spec.get('name')}",
                                 value=text, meta=meta))
    ids.append(ctx.facts.add(
        f"{serial} data came from {meta['source_label']} "
        f"({'internal store' if meta['origin'] == 'store' else 'live SIS retrieval'}), "
        f"retrieved {meta['retrieved_at']}, run {meta['automation_run_id']}, "
        f"freshness {meta['freshness']}", evidence="DIRECT", field_name="provenance",
        value=meta["automation_run_id"], meta=meta))
    return {"ok": True, "status": "SUCCESS", "serial_number": serial, **meta,
            "data": {**_summary(rec, CORE_FIELDS), "product": product,
                     "engine_model": (rec.get("engine_family") or {}).get("model")
                     if rec.get("engine_family") else None,
                     "parts_rows": len(parts), "parts_groups": groups,
                     "missing_fields": missing},
            "fact_ids": ids}


async def get_equipment_build_info(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    serial = _serial(ctx, args)
    entry = await _record(ctx, serial)
    rec, meta = entry["record"], entry["meta"]
    ids, missing = _record_facts(ctx, serial, rec, meta, BUILD_FIELDS + ("engine_family",))
    return {"ok": True, "status": "SUCCESS", "serial_number": serial, **meta,
            "data": {**_summary(rec, BUILD_FIELDS), "missing_fields": missing},
            "fact_ids": ids}


def _match(value: Any, needle: str) -> bool:
    return bool(value) and needle.lower() in str(value).lower()


async def get_equipment_parts(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    serial = _serial(ctx, args)
    group = str(args.get("group_part") or args.get("group") or "").strip()[:60]
    part_number = str(args.get("part_number") or "").strip().upper()[:20]
    text = str(args.get("text") or "").strip()[:60]
    quantity = args.get("quantity_required")
    try:
        quantity = float(quantity) if quantity not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise ToolRefused("quantity_required must be a number") from exc
    limit = max(1, min(int(args.get("limit") or 50), MAX_PART_FACTS))

    entry = await _record(ctx, serial)
    rec, meta = entry["record"], entry["meta"]
    rows = flatten_parts(rec.get("parts_data"))
    selected = rows
    filters = []
    if group:
        filters.append(f"group matches '{group}'")
        selected = [r for r in selected if _match(r["group_name"], group)
                    or _match(r["group_part"], group) or _match(r["group_title"], group)]
    if part_number:
        filters.append(f"part number = {part_number}")
        selected = [r for r in selected if str(r["part_number"] or "").upper() == part_number]
    if quantity is not None:
        filters.append(f"quantity required = {quantity:g}")
        selected = [r for r in selected if r["quantity_required"] is not None
                    and float(r["quantity_required"]) == quantity]
    if text:
        filters.append(f"text contains '{text}'")
        selected = [r for r in selected if any(_match(r.get(k), text) for k in
                    ("part_name", "description", "where_used", "service_article",
                     "group_name"))]

    ids = []
    quantity_published = any(r["quantity_text"] for r in rows)
    ids.append(ctx.facts.add(
        f"{serial}: {len(selected)} of {len(rows)} part rows"
        + (f" where {' and '.join(filters)}" if filters else ""),
        evidence="DERIVED", field_name="parts_filter",
        value={"matched": len(selected), "total": len(rows), "filters": filters}, meta=meta))
    if quantity is not None and not quantity_published:
        ids.append(ctx.facts.add(
            f"{serial}: the source did not publish a quantity column for these parts",
            evidence="DERIVED", field_name="missing_fields", value=["quantity_required"],
            meta=meta))
    if group and not selected:
        groups = sorted({r["group_name"] for r in rows if r["group_name"]})
        ids.append(ctx.facts.add(
            f"{serial}: no group matches '{group}'. Groups on the page: {', '.join(groups)}",
            evidence="DERIVED", field_name="groups", value=groups, meta=meta))
    out_rows = []
    for r in selected[:limit]:
        bits = [f"part {r['part_number'] or '(no part number)'}"]
        if r["part_name"]:
            bits.append(f"'{r['part_name']}'")
        if r["quantity_text"]:
            bits.append(f"qty {r['quantity_text']}")
        if r["where_used"]:
            bits.append(f"where used: {r['where_used']}")
        if r["service_article"]:
            bits.append(f"service article: {r['service_article']}")
        if r["description"]:
            bits.append(f"description: {r['description']}")
        bits.append(f"in {r['group_name']}")
        ids.append(ctx.facts.add(" ".join(bits), evidence="DIRECT", field_name="part",
                                 value=r["part_number"], meta=meta, locator=r["locator"]))
        out_rows.append({k: r[k] for k in ("group_name", "group_part", "part_number",
                                           "part_name", "quantity_text", "where_used",
                                           "service_article", "description",
                                           "component_serial", "locator")})
    if len(selected) > limit:
        ids.append(ctx.facts.add(f"{serial}: {len(selected) - limit} more matching rows not "
                                 "listed", evidence="DERIVED", field_name="truncated",
                                 value=len(selected) - limit, meta=meta))
    return {"ok": True, "status": "SUCCESS", "serial_number": serial, **meta,
            "data": {"filters": filters, "matched": len(selected), "total": len(rows),
                     "rows": out_rows}, "fact_ids": ids}


async def get_automation_history(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    serial = _serial(ctx, args)
    versions = await ctx.repo.history(serial, ctx.source, 20)
    ids = []
    rows = []
    for v in versions:
        run = v.get("automation_run_id")
        at = v.get("version_at") or v.get("retrieved_at")
        rows.append({"version_at": str(at), "automation_run_id": run,
                     "data_hash": (v.get("data_hash") or "")[:12]})
        ids.append(ctx.facts.add(f"{serial} stored version at {at} from run {run}",
                                 evidence="DIRECT", field_name="history", value=run,
                                 meta={"source_label": "internal store",
                                       "automation_run_id": run, "retrieved_at": str(at)}))
    ids.append(ctx.facts.add(f"{serial} has {len(rows)} stored version(s)", evidence="DERIVED",
                             field_name="history_count", value=len(rows)))
    return {"ok": True, "status": "SUCCESS", "serial_number": serial,
            "data": {"versions": rows}, "fact_ids": ids}


async def search_equipment(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Does the store know this serial? Near matches are offered, never used."""
    from app.agent.resolve import find_candidates

    raw = str(args.get("serial_number") or "").strip()
    try:
        serial = validate_serial(raw)
    except AutomationError as err:
        raise ToolRefused(err.message) from err
    rows = await ctx.repo.get_any_source(serial)
    exists = any(r.get("status") != "NOT_FOUND" for r in rows)
    candidates = [] if exists else [c.serial_number for c in
                                    await find_candidates(ctx.repo, serial)]
    fid = ctx.facts.add(
        f"{serial} {'is' if exists else 'is not'} in the internal store"
        + (f"; similar stored serials (NOT substitutes, ask the user): {', '.join(candidates)}"
           if candidates else ""), evidence="DIRECT", field_name="exists", value=exists)
    return {"ok": True, "status": "SUCCESS" if exists else "NOT_FOUND",
            "serial_number": serial, "data": {"exists": exists, "candidates": candidates},
            "fact_ids": [fid]}


def _iso(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


async def analyze_equipment(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Findings computed by code from verified values — DERIVED, never guessed."""
    serial = _serial(ctx, args)
    entry = await _record(ctx, serial)
    rec, meta = entry["record"], entry["meta"]
    rows = flatten_parts(rec.get("parts_data"))
    ids: list[str] = []
    findings: dict[str, Any] = {}

    missing = [f for f in CORE_FIELDS if rec.get(f) in (None, "")]
    findings["missing_fields"] = missing
    ids.append(ctx.facts.add(f"{serial} missing fields: {', '.join(missing) or 'none'}",
                             evidence="DERIVED", field_name="missing_fields", value=missing,
                             meta=meta))

    if rec.get("machine_serial_number") and rec["machine_serial_number"] != serial:
        findings["serial_mismatch"] = rec["machine_serial_number"]
        ids.append(ctx.facts.add(
            f"INCONSISTENT: machine serial on the page is {rec['machine_serial_number']}, "
            f"not {serial}", evidence="DERIVED", field_name="inconsistency", meta=meta))
    mismatched = (rec.get("parts_data") or {}).get("serial_mismatched_groups") or []
    if mismatched:
        findings["mismatched_groups"] = mismatched
        ids.append(ctx.facts.add(f"{serial}: parts groups naming another serial: "
                                 f"{', '.join(map(str, mismatched))}", evidence="DERIVED",
                                 field_name="inconsistency", value=mismatched, meta=meta))

    mb, eb = _iso(rec.get("machine_build_date")), _iso(rec.get("engine_build_date"))
    if mb and eb:
        days = (mb - eb).days
        findings["engine_before_machine_days"] = days
        ids.append(ctx.facts.add(
            f"{serial}: engine built {abs(days)} days {'before' if days >= 0 else 'AFTER'} "
            f"the machine ({eb.isoformat()} vs {mb.isoformat()})", evidence="DERIVED",
            field_name="build_gap_days", value=days, meta=meta))

    counts: dict[str, list[str]] = {}
    for r in rows:
        if r["part_number"]:
            counts.setdefault(r["part_number"], []).append(r["group_name"] or "?")
    dupes = {pn: groups for pn, groups in counts.items() if len(groups) > 1}
    findings["duplicate_parts"] = dupes
    ids.append(ctx.facts.add(
        f"{serial}: {len(dupes)} part number(s) appear more than once"
        + (": " + "; ".join(f"{pn} ×{len(g)} ({', '.join(sorted(set(g)))})"
                             for pn, g in list(dupes.items())[:15]) if dupes else ""),
        evidence="DERIVED", field_name="duplicate_parts", value=list(dupes), meta=meta))

    by_group: dict[str, int] = {}
    for r in rows:
        by_group[r["group_name"] or "?"] = by_group.get(r["group_name"] or "?", 0) + 1
    findings["rows_per_group"] = by_group
    ids.append(ctx.facts.add(f"{serial}: part rows per group: "
                             + ", ".join(f"{g} {n}" for g, n in by_group.items()),
                             evidence="DERIVED", field_name="rows_per_group", value=by_group,
                             meta=meta))
    quantities = [r["quantity_required"] for r in rows if r["quantity_required"] is not None]
    if quantities:
        ids.append(ctx.facts.add(
            f"{serial}: quantities range {min(quantities):g}–{max(quantities):g} across "
            f"{len(quantities)} rows", evidence="DERIVED", field_name="quantity_range",
            value=[min(quantities), max(quantities)], meta=meta))

    # Changes between the two most recent stored versions.
    history = await ctx.repo.history(serial, ctx.source, 5)
    # Snowflake history carries the record under `snapshot`; the local and
    # memory stores return the record itself.
    snaps = [h["snapshot"] if isinstance(h.get("snapshot"), dict) else h for h in history]
    if len(snaps) >= 2:
        new, old = snaps[0], snaps[1]
        changed = sorted(k for k in set(new) | set(old)
                         if k not in ("retrieved_at", "automation_run_id", "data_hash",
                                      "field_provenance", "quality")
                         and new.get(k) != old.get(k))
        findings["changed_since_previous"] = changed
        ids.append(ctx.facts.add(
            f"{serial}: fields changed between the last two SIS retrievals: "
            f"{', '.join(changed) or 'none'}", evidence="DERIVED", field_name="changes",
            value=changed, meta=meta))
    else:
        ids.append(ctx.facts.add(f"{serial}: only one stored retrieval, nothing to compare",
                                 evidence="DERIVED", field_name="changes", value=[],
                                 meta=meta))
    return {"ok": True, "status": "SUCCESS", "serial_number": serial, **meta,
            "data": findings, "fact_ids": ids}


async def refresh_equipment_from_sis(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """A request for fresh SIS data. The gateway decides whether a browser runs."""
    serial = _serial(ctx, args)
    stored = ctx.records.get(serial) or None
    if stored is None:
        rows = await ctx.repo.get_any_source(serial)
        stored_fresh = False
        if rows:
            decision = ctx.service.freshness.evaluate(rows[0], source=ctx.source)
            stored_fresh = decision.freshness.value == "FRESH"
    else:
        stored_fresh = stored["meta"]["freshness"] == "FRESH"
    if ctx.refreshed:
        raise ToolRefused("SIS was already refreshed for this turn")
    if stored_fresh and not ctx.wants_fresh:
        # Not the model's call: fresh data and no request for a refresh means
        # no browser. The stored record is returned instead.
        result = await get_equipment_data(ctx, {"serial_number": serial})
        result["data"]["refresh"] = "not performed: stored data is fresh and the user " \
                                    "did not ask for a refresh"
        return result
    ctx.refreshed = True
    await _record(ctx, serial, force=True)
    result = await get_equipment_data(ctx, {"serial_number": serial})
    result["data"]["refresh"] = "performed"
    return result


# ── registry: what the model is told, and what runs ─────────────────────────
_SERIAL = {"type": "string", "description": "Equipment serial number, e.g. JAZ01865"}

TOOLS: dict[str, dict[str, Any]] = {
    "get_equipment_data": {
        "fn": get_equipment_data,
        "description": "Verified equipment record for a serial: model, type, machine and "
                       "engine serial numbers and build dates, product, parts summary, "
                       "provenance. Reads Snowflake first; if the stored copy is missing or "
                       "stale the gateway retrieves it from Caterpillar SIS automatically.",
        "properties": {"serial_number": _SERIAL}, "required": ["serial_number"]},
    "get_equipment_build_info": {
        "fn": get_equipment_build_info,
        "description": "Machine/engine serial numbers and build dates for a serial.",
        "properties": {"serial_number": _SERIAL}, "required": ["serial_number"]},
    "get_equipment_parts": {
        "fn": get_equipment_parts,
        "description": "Parts rows published by SIS for a serial, optionally filtered by "
                       "group (e.g. 'exhaust'), exact part number (e.g. 286-4915), required "
                       "quantity, or text in the name/description/where-used.",
        "properties": {"serial_number": _SERIAL,
                       "group_part": {"type": "string", "description": "group name contains"},
                       "part_number": {"type": "string"},
                       "quantity_required": {"type": "number"},
                       "text": {"type": "string"},
                       "limit": {"type": "integer"}},
        "required": ["serial_number"]},
    "get_automation_history": {
        "fn": get_automation_history,
        "description": "Stored versions of this serial's record: when each SIS retrieval "
                       "happened and which automation run produced it.",
        "properties": {"serial_number": _SERIAL}, "required": ["serial_number"]},
    "search_equipment": {
        "fn": search_equipment,
        "description": "Whether a serial exists in the internal store, with similar stored "
                       "serials if it does not. Similar serials are NOT substitutes: ask "
                       "the user which one they meant.",
        "properties": {"serial_number": _SERIAL}, "required": ["serial_number"]},
    "analyze_equipment": {
        "fn": analyze_equipment,
        "description": "Findings computed from the verified record: missing fields, "
                       "inconsistencies, build-date gap, duplicate part numbers, rows per "
                       "group, quantity range, and what changed since the previous SIS "
                       "retrieval.",
        "properties": {"serial_number": _SERIAL}, "required": ["serial_number"]},
    "refresh_equipment_from_sis": {
        "fn": refresh_equipment_from_sis,
        "description": "Ask for a fresh retrieval from Caterpillar SIS. The gateway runs it "
                       "only when the user asked for fresh data or the stored copy is "
                       "missing/stale; otherwise the stored record is returned.",
        "properties": {"serial_number": _SERIAL}, "required": ["serial_number"]},
}


def tool_specs() -> list[dict[str, Any]]:
    """Cortex Agents `tools` entries: generic tools with NO tool_resources, so
    Cortex returns each call to the gateway to execute (client-side)."""
    return [{"tool_spec": {"type": "generic", "name": name, "description": t["description"],
                           "input_schema": {"type": "object", "properties": t["properties"],
                                            "required": t["required"]}}}
            for name, t in TOOLS.items()]


async def run_tool(ctx: ToolContext, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Execute one tool call. Never raises: every outcome is a result the model
    can read — including a refusal."""
    started = time.monotonic()
    args = args if isinstance(args, dict) else {}
    tool: Callable[..., Awaitable[dict[str, Any]]] | None = (TOOLS.get(name) or {}).get("fn")
    serial = str(args.get("serial_number") or "")[:20]
    if tool is None:
        result: dict[str, Any] = {"tool": name, "ok": False, "status": "REFUSED",
                                  "message": f"unknown tool {name!r}"}
    else:
        try:
            result = {"tool": name, **await tool(ctx, args)}
        except ToolRefused as exc:
            result = {"tool": name, "ok": False, "status": "REFUSED", "message": str(exc)}
        except AutomationError as err:
            result = _error(name, serial, err)
    entry = {"tool": name, "serial_number": serial, "status": result.get("status"),
             "origin": result.get("origin"), "ms": int((time.monotonic() - started) * 1000),
             "automation_run_id": result.get("automation_run_id")}
    ctx.trace.append(entry)
    log(logger, logging.INFO, "tool.executed", **entry)
    if result.get("fact_ids"):
        by_id = ctx.facts.by_id()
        result["facts"] = [{"id": i, "evidence": by_id[i]["evidence"],
                            "statement": by_id[i]["statement"]} for i in result["fact_ids"]]
    return result
