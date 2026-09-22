"""One Maia turn about equipment: understand → tools → Cortex → verify → answer.

    utterance ─▶ MaiaBrain (deterministic: intent, serial, no silent substitution)
                 │  asks the user?  → clarification, no tool, no LLM
                 ▼
               Cortex
                 agent mode:    Cortex Agent ⇄ gateway tools (client-side execution)
                 complete mode: gateway runs the tool plan, then AI_COMPLETE
                 ▼
               verify  (every value in the answer must be in this turn's facts)
                 ▼
               MaiaAnswer  (answer + facts DIRECT/DERIVED + INFERRED + provenance)

The browser is reached only through `get_equipment_data` / `refresh_…`, which
call EquipmentService: store first, SIS on a miss or stale copy, validation,
persistence. Cortex never sees a credential, a selector or a SQL statement.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agent.brain import MaiaBrain, explain_error
from app.agent.state import ConversationContext, Intent, ResponseMode
from app.core.errors import AutomationError, ErrorCode
from app.core.logging import log
from app.cortex.client import CortexRuntime, parse_json_object
from app.cortex.verify import CORTEX_SCHEMA, verify
from app.domain.parts import PART_NUMBER_RE
from app.tools.equipment_tools import TOOLS, ToolContext, run_tool, tool_specs

logger = logging.getLogger(__name__)

_FRESH = re.compile(r"\b(refresh|re-?fetch|latest|fresh|live|from sis|re-?check|update)\b"
                    r"|حدّث|حدث|من SIS", re.I)
_ANALYZE = re.compile(r"\b(analy[sz]e|analysis|summar\w*|inconsisten\w*|duplicate\w*|missing"
                      r"|compare|changed?|difference|diff)\b", re.I)
_QTY = re.compile(r"\b(?:quantity|qty)\s*(?:required\s*)?(?:of|=|:|is)?\s*(\d+(?:\.\d+)?)\b"
                  r"|\brequir\w*\s+(?:a\s+)?(?:quantity\s+(?:of\s+)?)?(\d+(?:\.\d+)?)\b", re.I)
_GROUP = re.compile(r"\b(?:in|from|of|for)\s+the\s+([a-z][a-z /&-]{1,30}?)\s+group\b"
                    r"|\b([a-z]{3,20})\s+group\b(?<!entire group)"
                    r"|\bgroup\s+(?:called\s+|named\s+)?['\"]?([a-z][\w /&-]{1,30}?)['\"]?(?:\s|$|\?)",
                    re.I)

INSTRUCTIONS_RESPONSE = (
    "You are Maia, Mantrac's equipment assistant. Answer ONLY from the tool results of this "
    "conversation. Each tool result lists `facts` with ids (F1, F2, …) and an evidence type: "
    "DIRECT = returned by Snowflake/Caterpillar SIS, DERIVED = computed by the gateway from "
    "DIRECT values. Never state a model, date, serial, part number, quantity or specification "
    "that is not in those facts. If a value is missing, say it was not published by the source. "
    "Your own reasoning goes in `inferred`, never presented as a SIS fact. Mention the source, "
    "retrieval time and automation run id. Answer in the user's language. "
    "Your FINAL message must be one JSON object and nothing else: "
    '{"answer": str, "cited_fact_ids": [str], "derived_findings": [{"statement": str, '
    '"fact_ids": [str]}], "inferred": [{"statement": str, "basis_fact_ids": [str]}], '
    '"missing_fields": [str], "confidence": "high"|"medium"|"low"}')
INSTRUCTIONS_ORCHESTRATION = (
    "Always call a tool before answering about equipment; start with get_equipment_data. "
    "Use only the serial number the gateway gives you — never change, correct or guess a "
    "serial; if a tool refuses a serial, ask the user. Use get_equipment_parts with filters "
    "for parts questions, get_equipment_build_info for build/engine questions, "
    "analyze_equipment for summaries, inconsistencies, duplicates and changes, and "
    "get_automation_history for retrieval history. Call refresh_equipment_from_sis only when "
    "the user asks for fresh or latest SIS data. You cannot run SQL or a browser.")


def parse_parts_filters(utterance: str) -> dict[str, Any]:
    """Filters a parts question names, read deterministically from the sentence."""
    text = utterance or ""
    out: dict[str, Any] = {}
    pn = PART_NUMBER_RE.search(text)
    if pn:
        out["part_number"] = pn.group(0).upper()
    q = _QTY.search(text)
    if q:
        out["quantity_required"] = float(q.group(1) or q.group(2))
    g = _GROUP.search(text)
    if g:
        name = (g.group(1) or g.group(2) or g.group(3) or "").strip()
        if name and name.lower() not in ("entire", "the", "a", "this", "that", "part", "parts"):
            out["group_part"] = name
    return out


def plan_tools(intent: Intent, utterance: str, serial: str, wants_fresh: bool
               ) -> list[tuple[str, dict[str, Any]]]:
    """complete mode: the gateway picks the tools. Deterministic, never the LLM."""
    first = "refresh_equipment_from_sis" if wants_fresh else "get_equipment_data"
    plan: list[tuple[str, dict[str, Any]]] = [(first, {"serial_number": serial})]
    filters = parse_parts_filters(utterance)
    if intent in (Intent.ENGINE_DETAILS, Intent.BUILD_DATE):
        plan.append(("get_equipment_build_info", {"serial_number": serial}))
    if intent is Intent.PARTS_LOOKUP or filters or re.search(r"\bused\b", utterance, re.I):
        plan.append(("get_equipment_parts", {"serial_number": serial, **filters}))
    if intent is Intent.EQUIPMENT_HISTORY:
        plan.append(("get_automation_history", {"serial_number": serial}))
    if intent is Intent.EQUIPMENT_HISTORY or _ANALYZE.search(utterance):
        plan.append(("analyze_equipment", {"serial_number": serial}))
    return plan


class MaiaCortexService:
    def __init__(self, *, runtime: CortexRuntime, equipment_service: Any, repo: Any,
                 brain: MaiaBrain | None = None) -> None:
        self.runtime = runtime
        self.service = equipment_service
        self.repo = repo
        self.brain = brain or MaiaBrain(repo=repo)

    # ── the turn ────────────────────────────────────────────────────────────
    async def answer(self, utterance: str, context: ConversationContext | None = None
                     ) -> dict[str, Any]:
        state = await self.brain.understand(utterance, context)
        base = {"utterance": utterance, "context": state.context.model_dump(mode="json"),
                "intent": state.intent.value, "response_mode": state.response_mode.value}

        if state.response_mode is not ResponseMode.RUN_LOOKUP or not state.serial_number:
            # The brain has a question for the user, a refusal, small talk, or
            # a non-equipment turn. No tool runs and no fact is claimed.
            status = {"CHAT": "CHAT", "PASS": "PASS", "REFUSE": "REFUSED",
                      "HELP": "HELP"}.get(state.response_mode.value, "CLARIFICATION_NEEDED")
            return {**base, "status": status, "serial_number": state.serial_number,
                    "answer": state.message,
                    "candidates": [c.serial_number for c in state.serial_candidates],
                    "suggestions": state.suggestions, "facts": [], "derived_findings": [],
                    "inferred": [], "missing_fields": [], "evidence": [], "tools": [],
                    "cortex": {"used": False}}

        serial = state.serial_number
        allowed = {serial}
        ctx = ToolContext(service=self.service, repo=self.repo, allowed_serials=allowed,
                          wants_fresh=bool(_FRESH.search(utterance)))
        try:
            mode = self.runtime.mode()
        except AutomationError as err:
            return await self._without_cortex(base, ctx, serial, state, err)

        try:
            if mode == "agent":
                output, rounds = await self._agent_turn(ctx, serial, utterance, state.language)
            else:
                output, rounds = await self._complete_turn(ctx, serial, utterance, state)
        except AutomationError as err:
            if err.code is ErrorCode.CORTEX_UNAVAILABLE:
                return await self._without_cortex(base, ctx, serial, state, err)
            raise
        return self._assemble(base, ctx, serial, state, output, mode=mode, rounds=rounds)

    # ── agent mode: Cortex decides which tools, we execute them ─────────────
    async def _agent_turn(self, ctx: ToolContext, serial: str, utterance: str,
                          language: str) -> tuple[dict[str, Any], int]:
        prompt = (f"Equipment serial for this turn (resolved and confirmed by the gateway): "
                  f"{serial}\nUser language: {language}\nUser: {utterance}")
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"type": "text", "text": prompt}]}]
        instructions = {"response": INSTRUCTIONS_RESPONSE,
                        "orchestration": INSTRUCTIONS_ORCHESTRATION}
        specs = tool_specs()
        for round_no in range(1, self.runtime.settings.cortex_max_tool_rounds + 1):
            message = await self.runtime.agent_run(messages, tools=specs,
                                                   instructions=instructions)
            items = message.get("content") or []
            answered = {(i.get("tool_result") or {}).get("tool_use_id") for i in items
                        if i.get("type") == "tool_result"}
            calls = [i["tool_use"] for i in items if i.get("type") == "tool_use"
                     and (i.get("tool_use") or {}).get("tool_use_id") not in answered
                     and (i["tool_use"].get("client_side_execute", True))
                     and i["tool_use"].get("name") in TOOLS]
            if not calls:
                text = "\n".join(i.get("text", "") for i in items if i.get("type") == "text")
                return _as_output(text), round_no
            messages.append({"role": "assistant", "content": [
                i for i in items if i.get("type") in ("text", "tool_use")]})
            results = []
            for call in calls:
                result = await run_tool(ctx, call["name"], call.get("input") or {})
                results.append({"type": "tool_result", "tool_result": {
                    "tool_use_id": call["tool_use_id"], "name": call["name"],
                    "content": [{"type": "json", "json": _for_model(result)}],
                    "status": "success" if result.get("ok") else "error"}})
            messages.append({"role": "user", "content": results})
        from app.cortex.errors import unavailable

        raise unavailable("bad_response", "the agent did not finish within the tool budget",
                          mode="agent")

    # ── complete mode: the gateway runs the plan, Cortex writes the answer ──
    async def _complete_turn(self, ctx: ToolContext, serial: str, utterance: str,
                             state: Any) -> tuple[dict[str, Any], int]:
        results = []
        for name, args in plan_tools(state.intent, utterance, serial, ctx.wants_fresh):
            results.append(await run_tool(ctx, name, args))
            if not results[0].get("ok"):
                break                         # no record: nothing further to ask for
        if not results[0].get("ok"):
            return {"answer": "", "cited_fact_ids": [], "derived_findings": [],
                    "inferred": [], "missing_fields": [], "confidence": "low",
                    "_tool_failure": results[0]}, 0
        facts = "\n".join(f"{f['id']} [{f['evidence']}] {f['statement']}"
                          for f in ctx.facts.facts)
        prompt = (f"{INSTRUCTIONS_RESPONSE}\n\nFACTS (the only evidence you may use):\n{facts}"
                  f"\n\nEquipment serial: {serial}\nUser language: {state.language}\n"
                  f"User question: {utterance}\n")
        output = await self.runtime.complete(prompt, schema=CORTEX_SCHEMA)
        return output, 1

    # ── assembling the answer ───────────────────────────────────────────────
    def _assemble(self, base: dict[str, Any], ctx: ToolContext, serial: str, state: Any,
                  output: dict[str, Any], *, mode: str, rounds: int) -> dict[str, Any]:
        primary = next((t for t in ctx.trace if t["tool"] in
                        ("get_equipment_data", "refresh_equipment_from_sis",
                         "get_equipment_build_info", "get_equipment_parts",
                         "analyze_equipment")), None)
        failure = output.get("_tool_failure")
        if failure is None and primary and primary["status"] not in ("SUCCESS",):
            failure = {"status": primary["status"], "error_code": primary["status"],
                       "automation_run_id": primary.get("automation_run_id")}
        record = ctx.records.get(serial)
        meta = (record or {}).get("meta") or {}

        if failure is not None or record is None:
            code = (failure or {}).get("error_code") or "INTERNAL_ERROR"
            status = (failure or {}).get("status") or "EXTRACTION_FAILED"
            answer = explain_error(code if code in ErrorCode.__members__ else "INTERNAL_ERROR",
                                   serial, state.language)
            return {**base, "status": status, "serial_number": serial, "answer": answer,
                    "facts": [], "derived_findings": [], "inferred": [], "missing_fields": [],
                    "evidence": [], "automation_run_id": (failure or {}).get(
                        "automation_run_id"),
                    "tools": ctx.trace, "cortex": {"used": True, "mode": mode,
                                                   "rounds": rounds}}

        check = verify(output, ctx.facts)
        clean = check["output"]
        by_id = ctx.facts.by_id()
        cited = clean["cited_fact_ids"] or [f["id"] for f in ctx.facts.facts
                                            if f["evidence"] == "DIRECT"][:40]
        answer = clean["answer"]
        if not check["passed"]:
            log(logger, logging.WARNING, "cortex.answer_blocked", serial_number=serial,
                blocked=check["blocked_tokens"][:10])
            answer = deterministic_answer(serial, ctx, state.language)
        facts = [by_id[i] for i in cited if by_id[i]["evidence"] == "DIRECT"]
        derived = [{"statement": by_id[i]["statement"], "evidence": "DERIVED",
                    "fact_ids": [i], "by": "gateway"}
                   for i in cited if by_id[i]["evidence"] == "DERIVED"]
        derived += [{**d, "evidence": "DERIVED", "by": "cortex"}
                    for d in clean["derived_findings"]]
        inferred = [{**i, "evidence": "INFERRED"} for i in clean["inferred"]]
        missing = sorted(set(clean["missing_fields"]) | set(next(
            (f["value"] for f in ctx.facts.facts if f["field"] == "missing_fields"), []) or []))
        return {**base, "status": "SUCCESS", "serial_number": serial, "answer": answer,
                "facts": facts, "derived_findings": derived, "inferred": inferred,
                "missing_fields": missing,
                "source": meta.get("source_label"), "origin": meta.get("origin"),
                "retrieved_at": meta.get("retrieved_at"),
                "automation_run_id": meta.get("automation_run_id"),
                "freshness": meta.get("freshness"),
                "confidence": clean["confidence"] if check["passed"] else "low",
                "evidence": [{"fact_id": f["id"], "type": f["evidence"]} for f in facts]
                + [{"fact_id": d["fact_ids"][0], "type": "DERIVED"} for d in derived
                   if d.get("fact_ids")],
                "verification": {"passed": check["passed"],
                                 "blocked_tokens": check["blocked_tokens"],
                                 "dropped_fact_ids": check["dropped_fact_ids"]},
                "tools": ctx.trace, "sis_runs": ctx.sis_runs,
                "cortex": {"used": True, "mode": mode, "rounds": rounds,
                           "model": (self.runtime.settings.cortex_model if mode == "complete"
                                     else self.runtime.settings.cortex_agent_model or "auto")}}

    async def _without_cortex(self, base: dict[str, Any], ctx: ToolContext, serial: str,
                              state: Any, err: AutomationError) -> dict[str, Any]:
        """Cortex cannot run. Say so, with the reason. The verified values are
        still shown — labelled as the gateway's, not as an AI answer."""
        result = await run_tool(ctx, "get_equipment_data", {"serial_number": serial})
        facts = [f for f in ctx.facts.facts if f["evidence"] == "DIRECT"] if result.get("ok") \
            else []
        reason, fix = err.details.get("reason"), err.details.get("fix")
        head = (f"Snowflake Cortex is unavailable ({reason}), so there is no AI answer. {fix}")
        body = deterministic_answer(serial, ctx, state.language) if result.get("ok") else \
            explain_error(result.get("error_code") or "INTERNAL_ERROR", serial, state.language)
        meta = (ctx.records.get(serial) or {}).get("meta") or {}
        return {**base, "status": "CORTEX_UNAVAILABLE", "serial_number": serial,
                "answer": f"{head}\n\n{body}",
                "cortex": {"used": False, "reason": reason, "fix": fix,
                           "detail": err.details.get("detail")},
                "data_status": result.get("status"),
                "facts": facts, "derived_findings": [], "inferred": [], "missing_fields": [],
                "source": meta.get("source_label"), "origin": meta.get("origin"),
                "retrieved_at": meta.get("retrieved_at"),
                "automation_run_id": meta.get("automation_run_id"),
                "evidence": [{"fact_id": f["id"], "type": "DIRECT"} for f in facts],
                "tools": ctx.trace, "sis_runs": ctx.sis_runs}

    # ── general (non-equipment) chat, still Cortex ──────────────────────────
    async def chat(self, system: str, transcript: list[dict[str, Any]]) -> str:
        mode = self.runtime.mode()
        lines = [f"{m['role'].upper()}: {m['text']}" for m in transcript[-20:]]
        if mode == "agent":
            messages = [{"role": m["role"], "content": [{"type": "text", "text": m["text"]}]}
                        for m in transcript[-20:]]
            message = await self.runtime.agent_run(
                messages, tools=[], instructions={"response": system[:8000]})
            return "\n".join(i.get("text", "") for i in message.get("content") or []
                             if i.get("type") == "text")
        prompt = f"{system}\n\nCONVERSATION:\n" + "\n".join(lines) + "\nASSISTANT:"
        return await self.runtime.complete(prompt)


# ── helpers ─────────────────────────────────────────────────────────────────
def _as_output(text: str) -> dict[str, Any]:
    try:
        return parse_json_object(text)
    except AutomationError:
        # Not JSON: keep the prose, cite nothing; verification still applies.
        return {"answer": text.strip(), "cited_fact_ids": [], "derived_findings": [],
                "inferred": [], "missing_fields": [], "confidence": "medium"}


def _for_model(result: dict[str, Any]) -> dict[str, Any]:
    """What the agent sees of a tool result: facts and data, bounded in size."""
    out = {k: v for k, v in result.items() if k not in ("fact_ids",)}
    text = json.dumps(out, default=str)
    if len(text) > 60_000:
        out["data"] = {"truncated": True}
    return out


def deterministic_answer(serial: str, ctx: ToolContext, language: str) -> str:
    """The facts, stated plainly, with provenance. Used when Cortex's answer
    failed verification or Cortex is unavailable — never presented as AI."""
    record = (ctx.records.get(serial) or {}).get("record") or {}
    meta = (ctx.records.get(serial) or {}).get("meta") or {}
    labels = [("Model", "equipment_model"), ("Type", "equipment_type"),
              ("Machine serial number", "machine_serial_number"),
              ("Machine build date", "machine_build_date"),
              ("Engine serial number", "engine_serial_number"),
              ("Engine build date", "engine_build_date")]
    rows = [f"• {label}: {record.get(key)}" for label, key in labels if record.get(key)]
    return (f"Verified data for {serial}:\n" + "\n".join(rows)
            + f"\nSource: {meta.get('source_label')} · retrieved {meta.get('retrieved_at')} · "
              f"run {meta.get('automation_run_id')}")
