"""Investigation modes over verified stored SIS data: PARTS · TROUBLESHOOTING · FULL,
with the 3D model as a shared follow-up step.

    Maia → router → InvestigationState{serial, mode, filters}
         → PARTS            (parts analysis, part search)          ┐
         → TROUBLESHOOTING  (codes → components → conditions)      ├→ 3D model step
         → FULL             (overview + parts + troubleshooting)   ┘   (mapping, highlight)

This module reads the store only. When the data it needs has not been read
from SIS yet (e.g. troubleshooting was never opened for this serial), it says
so with status NEEDS_RETRIEVAL and exactly what to read — the caller runs the
existing SIS lookup with those extra sections, on the same session, and asks
again. The analysis side never drives a browser.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.analysis.component_mapper import map_targets
from app.analysis.evidence import snapshot_ref
from app.analysis.intent import FOCUSES, route
from app.analysis.report import RULE, render_analysis, render_comparison
from app.analysis.service import AnalysisService
from app.analysis.troubleshooting_analyzer import analyze_troubleshooting
from app.domain.parts import flatten_parts

NB = " "                                   # keeps tree indentation in the chat
NOTE = ("The troubleshooting data identifies the component associated with the reported "
        "condition. The 3D model is used to locate the corresponding component. No "
        "physical failure is claimed unless SIS states it.")


NOT_EXPOSED = "3D model available, but component-level mapping is not exposed by the viewer."
# What each mode needs read from SIS, when nothing is stored for the serial yet —
# one lookup reads the record, the parts and these sections together.
SECTIONS = {"TROUBLESHOOTING": ["troubleshooting", "model_3d"],
            "FULL_ANALYSIS": ["troubleshooting", "model_3d"], "MODEL_3D": ["model_3d"]}
WHAT = {"PARTS": "the parts list", "TROUBLESHOOTING": "the Troubleshooting section and the 3D model",
        "FULL_ANALYSIS": "the record, parts, Troubleshooting and the 3D model",
        "MODEL_3D": "the 3D model"}


def _latest_with(snaps: list[Any], kind: str) -> tuple[Any | None, dict[str, Any] | None]:
    for snap in reversed(snaps):
        inv = snap.investigation
        if inv and inv.get(kind):
            return snap, inv
    return None, None


def choices(serial: str) -> list[dict[str, str]]:
    return [{"label": "🔧 Parts", "ask": f"Show me parts for {serial}"},
            {"label": "⚠️ Troubleshooting", "ask": f"Check troubleshooting for {serial}"},
            {"label": "📊 Full Analysis", "ask": f"Full analysis of {serial}"}]


class InvestigationService:
    def __init__(self, source: Any, cfg: dict[str, Any] | None = None) -> None:
        self.analysis = AnalysisService(source, cfg)
        self.source = source

    # ── entry point ─────────────────────────────────────────────────────────
    def handle(self, utterance: str, *, active_serial: str | None = None,
               as_of: datetime | None = None) -> dict[str, Any]:
        as_of = as_of or datetime.now(timezone.utc)
        r = route(utterance, active_serial)
        base: dict[str, Any] = {"handled": r.intent != "NONE", "intent": r.intent,
                                "mode": r.mode, "serials": r.serials,
                                "serial_source": r.serial_source, "filters": r.filters,
                                "engine": "deterministic"}
        if r.intent == "NONE":
            return base
        if r.intent == "COMPARE_EQUIPMENT":
            if len(r.serials) < 2:
                return {**base, "status": "NEED_SERIAL",
                        "text": "Which two machines should I compare? For example: "
                                "compare JAZ01865 and JAZ01866."}
            out = self.analysis.compare(r.serials[0], r.serials[1], as_of=as_of)
            return {**base, **out, "text": out.get("text") or out.get("message")}
        if not r.serials:
            return {**base, "status": "NEED_SERIAL",
                    "text": "Which machine? Tell me the serial number, e.g. \"Analyze JAZ01865\"."}
        serial = r.serials[0]
        state = {"serial": serial, "mode": r.mode, "filters": r.filters}
        base["state"] = state
        if r.intent == "ASK_MODE":
            return {**base, "status": "ASK_MODE", "serial": serial, "choices": choices(serial),
                    "text": f"**{serial}**\n\nWhat would you like me to investigate?",
                    "suggestions": [c["ask"] for c in choices(serial)]}

        check = self.analysis.analyze(serial, as_of=as_of)
        if check["status"] == "INVALID_SERIAL":
            return {**base, "status": "INVALID_SERIAL", "text": check["message"]}
        if check["status"] == "DATA_CORRUPTED":
            return {**base, "status": "DATA_CORRUPTED", "text": check["message"],
                    "issues": check.get("issues")}
        if check["status"] == "NO_DATA":
            if check.get("candidates"):
                return {**base, "status": "NO_DATA", "serial": serial,
                        "candidates": check["candidates"], "text": check["message"]}
            return self._retrieve(base, serial, r.mode, r.filters, WHAT.get(r.intent, "the equipment record"),
                                  SECTIONS.get(r.intent, []))

        snaps = self.source.snapshots(serial)
        if r.intent in FOCUSES:
            return {**base, "status": "OK", "serial": serial, "result": check,
                    "text": render_analysis(check, focus=r.intent)}
        if r.intent == "PARTS":
            return self._parts(base, serial, snaps, check, r.filters)
        if r.intent == "TROUBLESHOOTING":
            return self._troubleshooting(base, serial, snaps, r.filters)
        if r.intent == "MODEL_3D":
            return self._model_3d(base, serial, snaps, r.filters)
        return self._full(base, serial, snaps, check, r.filters)

    # ── retrieval request (the caller runs the existing SIS lookup) ─────────
    @staticmethod
    def _retrieve(base: dict[str, Any], serial: str, mode: str | None, filters: dict[str, Any],
                  what: str, investigate: list[str] | None = None,
                  locate: list[str] | None = None) -> dict[str, Any]:
        return {**base, "status": "NEEDS_RETRIEVAL", "serial": serial,
                "retrieve": {"serial": serial, "investigate": sorted(set(investigate or [])),
                             "locate": list(dict.fromkeys(locate or []))},
                "text": f"Reading {what} for {serial} from Caterpillar SIS…"}

    # ── PARTS ───────────────────────────────────────────────────────────────
    def _parts(self, base: dict[str, Any], serial: str, snaps: list[Any],
               analysis: dict[str, Any], filters: dict[str, Any]) -> dict[str, Any]:
        latest = snaps[-1]
        search = {k: filters[k] for k in ("part_number", "group", "quantity", "text")
                  if k in filters}
        if not search:
            text = render_analysis(analysis, focus="SHOW_PARTS")
            m_snap, inv = _latest_with(snaps, "model_3d")
            text += "\n\n" + self._model_line(inv)
            return {**base, "status": "OK", "serial": serial, "result": analysis, "text": text,
                    "suggestions": ["Show duplicate parts", "Show missing data",
                                    f"Show {serial} in 3D"]}
        rows = flatten_parts(latest.parts_data)
        hits = rows
        if "part_number" in search:
            hits = [x for x in hits if (x["part_number"] or "").upper() == search["part_number"]]
        if "group" in search:
            g = search["group"].lower()
            hits = [x for x in hits if g in (x["group_name"] or "").lower()
                    or g in (x["group_part"] or "").lower()]
        if "quantity" in search:
            hits = [x for x in hits if x["quantity_required"] is not None
                    and float(x["quantity_required"]) == search["quantity"]]
        if "text" in search:
            t = search["text"].lower()
            hits = [x for x in hits if any(t in (x.get(k) or "").lower() for k in
                                           ("part_name", "description", "where_used"))]
        ref = snapshot_ref(latest)
        found = [{"part_number": x["part_number"], "part_name": x["part_name"],
                  "description": x["description"], "group": x["group_name"],
                  "quantity": x["quantity_text"], "component_serial": x["component_serial"],
                  "locator": x["locator"], "classification": "DIRECT", **ref} for x in hits]
        lines = [RULE, f"**PARTS · {serial}**", RULE,
                 f"**Search:** {', '.join(f'{k} = {v}' for k, v in search.items())}",
                 f"**Matches:** {len(found)} of {len(rows)} part records "
                 "(DERIVED: filter over the verified SIS parts extraction)", ""]
        for f in found[:25]:
            bits = [f"**{f['part_number'] or '(no part number)'}**"]
            for k, label in (("part_name", ""), ("description", ""), ("group", "in "),
                             ("quantity", "qty ")):
                if f.get(k):
                    bits.append(f"{label}{f[k]}")
            lines.append("• " + " · ".join(bits))
        if not found:
            lines.append("• No part record matches this search in the verified SIS data.")
        out: dict[str, Any] = {**base, "status": "OK", "serial": serial, "matches": found}
        if found and "part_number" in search:
            targets = [search["part_number"]] + sorted({f["part_name"] for f in found
                                                        if f["part_name"]})
            m_snap, inv = _latest_with(snaps, "model_3d")
            if inv is None:
                return self._retrieve(base, serial, "PARTS", search,
                                      "the 3D model for this part", ["model_3d"], targets)
            lines += ["", *self._model_section(inv, targets, m_snap)]
        lines += ["", f"**Evidence:** {ref['source_label']}, run {ref['run_id']}, retrieved "
                  f"{(ref['retrieved_at'] or '')[:16].replace('T', ' ')}. Part rows are DIRECT; "
                  "the match count is DERIVED.", RULE]
        return {**out, "text": "\n".join(lines),
                "suggestions": [f"Show {serial} in 3D", "Show duplicate parts"]}

    # ── TROUBLESHOOTING ─────────────────────────────────────────────────────
    def _troubleshooting(self, base: dict[str, Any], serial: str, snaps: list[Any],
                         filters: dict[str, Any], *, header: bool = True) -> dict[str, Any]:
        snap, inv = _latest_with(snaps, "troubleshooting")
        if inv is None:
            return self._retrieve(base, serial, "TROUBLESHOOTING", filters,
                                  "the Troubleshooting section and the 3D model",
                                  ["troubleshooting", "model_3d"])
        ref = snapshot_ref(snap)
        tr = analyze_troubleshooting(inv["troubleshooting"], code=filters.get("code"), ref=ref)
        when = (inv["troubleshooting"].get("retrieved_at") or ref["retrieved_at"] or "")[:19]
        stamp = (f"Source: {ref['source_label']} · Run ID: {ref['run_id']} · "
                 f"Retrieved: {when.replace('T', ' ')} UTC")
        lines = [RULE, f"**MAIA EQUIPMENT ANALYSIS · {serial}**", RULE] if header else []
        lead = self._lead(serial, tr, filters)
        if lead is None:                      # nothing usable came back: say exactly why
            why = self._why_empty(inv["troubleshooting"], tr, filters)
            return {**base, "status": "NO_TROUBLESHOOTING", "serial": serial, "troubleshooting": tr,
                    "model_3d": self._model_summary(inv, [], snap), "reason": why,
                    "text": "\n".join([*lines, f"**Troubleshooting for {serial}:** {why}", "",
                                       stamp]),
                    "suggestions": [f"Open 3D Model for {serial}", f"Show me parts for {serial}"]}
        lines += [*lead, "", stamp, "", "**TROUBLESHOOTING**"]
        for s in tr["sections"]:
            lines.append(f"• {s['section']}: {s['count_displayed']} listed by SIS · "
                         f"{s['items_read']} read")
        if filters.get("code"):
            lines.append(f"**Code searched:** {filters['code']} — "
                         f"{tr['entry_count']} matching entr{'y' if tr['entry_count'] == 1 else 'ies'}")
        lines += [f"**Codes:** {len(tr['codes'])} · **Components:** {len(tr['components'])} · "
                  f"**Systems:** {len(tr['systems']) or 'not published by SIS'}", ""]
        for system, comps in list(tr["tree"].items())[:8]:
            lines.append(f"**{system}**")
            items = list(comps.items())[:15]
            for n, (comp, codes) in enumerate(items):
                last = n == len(items) - 1
                lines.append(f"{NB}{'└──' if last else '├──'} {comp}")
                for c in codes[:5]:
                    lines.append(f"{NB * 4}{' ' if last else '│'}{NB * 3}└── {c['code']}"
                                 + (f" · {c['condition']}" if c.get("condition") else ""))
        rels = [e["relationship"] for e in tr["entries"] if e["kind"] == "code"][:10]
        if rels:
            lines += ["", "**What SIS lists**", *[f"• {x}" for x in rels]]
        if tr["symptoms"] and not filters.get("code"):
            lines += ["", f"**Symptoms listed:** {len(tr['symptoms'])} (e.g. "
                      + "; ".join(tr["symptoms"][:4]) + ")"]
        findings = []
        for comp, codes in list(tr["components_with_multiple_codes"].items())[:5]:
            findings.append(f"{comp} is identified by {len(codes)} codes: {', '.join(codes)}")
        if tr["duplicate_codes"]:
            findings.append(f"{len(tr['duplicate_codes'])} code(s) are listed more than once")
        if tr["conflicting_codes"]:
            findings.append(f"{len(tr['conflicting_codes'])} code(s) carry different "
                            "descriptions in different rows")
        if tr["fmi_differs"]:
            findings.append(f"{len(tr['fmi_differs'])} code(s) have a condition that differs "
                            "from the standard FMI wording")
        if findings:
            lines += ["", "**Findings**", *[f"• {f}" for f in findings]]
        targets = sorted({e["component"] for e in tr["entries"] if e.get("component")
                          and e["kind"] == "code"})
        lines += ["", *self._model_section(inv, targets[:30], snap)]
        lines += ["", f"**Evidence:** troubleshooting rows are DIRECT from {ref['source_label']} "
                  f"(run {ref['run_id']}, retrieved {(ref['retrieved_at'] or '')[:16].replace('T', ' ')}"
                  "); component/condition splitting, grouping and counts are DERIVED.",
                  f"_{NOTE}_", RULE]
        return {**base, "status": "OK", "serial": serial, "troubleshooting": tr,
                "model_3d": self._model_summary(inv, targets, snap), "text": "\n".join(lines),
                "suggestions": [f"Open 3D Model for {serial}", f"Show me parts for {serial}"]}

    @staticmethod
    def _lead(serial: str, tr: dict[str, Any], filters: dict[str, Any]) -> list[str] | None:
        """The first code SIS lists (the searched one, if a code was asked for),
        field by field — every value is the live page's text."""
        codes = [e for e in tr["entries"] if e["kind"] == "code" and e.get("code")]
        if not codes:
            return None
        order = {"Troubleshooting": 0, "Advanced Troubleshooting": 1}
        e = sorted(codes, key=lambda x: order.get(x["section"], 2))[0]
        more = len(codes) - 1
        return [f"**Troubleshooting found for {serial}:**", "",
                f"Code: {e['code']}",
                f"System: {e['system'] or 'not shown by SIS for this code'}",
                f"Component: {e['component'] or e['description'] or 'not shown'}",
                f"Condition: {e['condition'] or 'not shown as a separate condition'}",
                *([f"_(+{more} more code{'s' if more != 1 else ''} below · listed under "
                   f"{e['section']})_"] if more else [])]

    @staticmethod
    def _why_empty(raw: dict[str, Any], tr: dict[str, Any], filters: dict[str, Any]) -> str:
        if raw.get("status") == "NOT_AVAILABLE":
            return f"the SIS Troubleshooting page could not be read — {raw.get('reason') or 'no reason given'}."
        if raw.get("status") == "COUNTS_ONLY":
            listed = ", ".join(f"{s['section']} ({s['count_displayed']})" for s in tr["sections"])
            return (f"the Troubleshooting tab opened and SIS lists {listed}, but no rows could be "
                    "read (extraction failed). The screenshot and panel sketch are saved with the run.")
        if filters.get("code"):
            return f"SIS does not list code {filters['code']} for this machine."
        return "no troubleshooting codes found on the SIS Troubleshooting tab."

    # ── 3D (shared step) ────────────────────────────────────────────────────
    def _model_3d(self, base: dict[str, Any], serial: str, snaps: list[Any],
                  filters: dict[str, Any]) -> dict[str, Any]:
        snap, inv = _latest_with(snaps, "model_3d")
        targets: list[str] = []
        if filters.get("part_number"):
            targets.append(filters["part_number"])
        t_snap, t_inv = _latest_with(snaps, "troubleshooting")
        if t_inv and not targets:
            tr = analyze_troubleshooting(t_inv["troubleshooting"], code=filters.get("code"))
            targets = sorted({e["component"] for e in tr["entries"] if e.get("component")
                              and e["kind"] == "code"})[:30]
        if inv is None:
            return self._retrieve(base, serial, "MODEL_3D", filters, "the 3D model",
                                  ["model_3d"], targets)
        lines = [RULE, f"**3D MODEL · {serial}**", RULE, *self._model_section(inv, targets, snap),
                 RULE]
        return {**base, "status": "OK", "serial": serial,
                "model_3d": self._model_summary(inv, targets, snap), "text": "\n".join(lines)}

    @staticmethod
    def _model_line(inv: dict[str, Any] | None) -> str:
        if not inv:
            return "**3D model:** not inspected yet — say \"show it in 3D\"."
        m = inv["model_3d"]
        return f"**3D model:** {m.get('status', 'UNKNOWN').replace('_', ' ').lower()}"

    def _model_summary(self, inv: dict[str, Any] | None, targets: list[str],
                       snap: Any) -> dict[str, Any] | None:
        if not inv or not inv.get("model_3d"):
            return None
        m = inv["model_3d"]
        names = m.get("component_names") or []
        mapping = map_targets(targets, names) if targets else []
        return {"status": m.get("status"), "available": bool((m.get("tab") or {}).get("opened")
                                                             and (m.get("viewer") or {})
                                                             .get("canvases")),
                "names_source": m.get("component_names_source"), "names": len(names),
                "libs": sorted(((m.get("viewer") or {}).get("libs") or {}).keys()),
                "model_resources": len((m.get("viewer") or {}).get("model_resources") or []),
                "mapping": mapping, "highlight": inv.get("highlight"),
                "component_mapping": "VERIFIED" if any(x.get("status") == "VERIFIED" for x in mapping)
                                     and (inv.get("highlight") or {}).get("ok") else "NOT AVAILABLE",
                "source": "SIS 3D Model", "classification": "DIRECT",
                "evidence": snapshot_ref(snap)}

    def _model_section(self, inv: dict[str, Any] | None, targets: list[str],
                       snap: Any) -> list[str]:
        s = self._model_summary(inv, targets, snap)
        if s is None:
            return ["**3D MODEL**", "• Not inspected in this retrieval."]
        explain = {
            "METADATA_AVAILABLE": f"the viewer exposes component names ({s['names']}, via "
                                  f"{s['names_source']})",
            "API_DETECTED_NO_NAMES": "a 3D viewer is present, but it did not expose component "
                                     "names — component-level mapping needs more investigation",
            "VISUAL_ONLY": "the viewer renders the model as graphics only; it exposes no "
                           "component names, so component-level mapping is not currently "
                           "available",
            "NO_VIEWER_FOUND": "the tab opened but no 3D viewer canvas was found",
            "NOT_AVAILABLE": "the 3D Model tab could not be opened",
        }.get(s["status"], s["status"])
        lines = ["**3D MODEL**", f"• Model: {'Available' if s['available'] else 'Not available'}",
                 f"• Viewer: {explain}"]
        if s["libs"] or s["model_resources"]:
            lines.append(f"• Detected: {', '.join(s['libs']) or 'no known viewer library'}"
                         f" · {s['model_resources']} model file(s) loaded")
        if not targets:
            lines.append("• Component mapping: no component to locate")
        elif not s["names"]:
            lines.append(f"• {NOT_EXPOSED if s['available'] else 'Component mapping: not available (the 3D model could not be inspected).'}"
                         " The SIS troubleshooting component remains the verified source.")
        else:
            for m in s["mapping"][:8]:
                if m["status"] == "VERIFIED":
                    lines.append(f"• {m['target']} → 3D component '{m['component']['name']}' "
                                 f"(VERIFIED · {m['method']})")
                elif m["status"] == "AMBIGUOUS":
                    lines.append(f"• {m['target']} → 3D component mapping is ambiguous "
                                 f"({len(m['candidates'])} candidates) — not guessed")
                else:
                    lines.append(f"• {m['target']} → not found among the viewer's components")
        h = s.get("highlight")
        if h:
            lines.append(f"• 3D status: {'located and highlighted (' + h.get('how', '') + ')' if h.get('ok') else 'highlight not possible: ' + str(h.get('error'))}")
        return lines

    # ── FULL ────────────────────────────────────────────────────────────────
    def _full(self, base: dict[str, Any], serial: str, snaps: list[Any],
              analysis: dict[str, Any], filters: dict[str, Any]) -> dict[str, Any]:
        snap, inv = _latest_with(snaps, "troubleshooting")
        if inv is None:
            return self._retrieve(base, serial, "FULL_ANALYSIS", filters,
                                  "Troubleshooting and the 3D model",
                                  ["troubleshooting", "model_3d"])
        tr = self._troubleshooting(base, serial, snaps, filters, header=False)
        text = render_analysis(analysis) + "\n\n" + tr["text"]
        return {**base, "status": "OK", "serial": serial, "result": analysis,
                "troubleshooting": tr.get("troubleshooting"), "model_3d": tr.get("model_3d"),
                "text": text, "suggestions": [f"Show {serial} in 3D", "Show duplicate parts"]}


__all__ = ["InvestigationService", "choices", "render_comparison"]
