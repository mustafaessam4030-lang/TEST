"""Investigation turns executed end to end on the server — real SIS when needed.

    Maia ─▶ intent router ─▶ InvestigationService (stored, verified data)
                                 │ NEEDS_RETRIEVAL
                                 ▼
          EquipmentService.lookup(FORCE_REFRESH, investigate=[troubleshooting, model_3d])
             (the existing SIS adapter, browser pool and saved session — no second browser)
                                 │ saved by the existing store
                                 ▼
          InvestigationService again ─▶ verified answer + trace

    get_troubleshooting("JAZ01865")                  → the troubleshooting answer
    ask("Check the troubleshooting for JAZ01865")    → whatever the router decides

It never reads fixture or sample data as SIS: a retrieval whose saved result is
not from Caterpillar SIS is reported as a failure, not answered from.
"""
from __future__ import annotations

import time
from typing import Any

from app.analysis.equipment_analyzer import SERIAL_RE
from app.analysis.investigation import SECTIONS, InvestigationService
from app.core.errors import AutomationError, ErrorCode
from app.models.schemas import EquipmentSearchRequest, SearchMode

# What went wrong, in the words the user sees. The error code is kept alongside.
REASON = {
    ErrorCode.LOGIN_FAILED: "SIS session unavailable: the sign-in did not succeed",
    ErrorCode.SESSION_EXPIRED: "SIS session unavailable: the saved session expired",
    ErrorCode.MFA_REQUIRED: "SIS session unavailable: SIS is asking for an MFA code — "
                            "enter it in the SIS browser window, then ask again",
    ErrorCode.CAPTCHA_DETECTED: "SIS session unavailable: SIS is showing a CAPTCHA — "
                                "a person must complete it",
    ErrorCode.SERIAL_NOT_FOUND: "Equipment not found in SIS",
    ErrorCode.SEARCH_FAILED: "the SIS search did not complete",
    ErrorCode.WEBSITE_CHANGED: "the SIS page did not look as expected (layout changed)",
    ErrorCode.TIMEOUT: "SIS did not respond in time",
    ErrorCode.NETWORK_ERROR: "SIS could not be reached (network)",
    ErrorCode.EXTRACTION_ERROR: "Extraction failed on the SIS page",
    ErrorCode.CIRCUIT_OPEN: "SIS lookups are paused after repeated failures — try again shortly",
    ErrorCode.PERSISTENCE_FAILED: "the SIS result was read but could not be saved",
}


class InvestigationRunner:
    def __init__(self, equipment_service: Any, source: Any, *, source_id: str = "cat_sis",
                 timeout_ms: int = 120_000, requested_by: str = "maia") -> None:
        self.equipment = equipment_service
        self.source = source                    # a SnapshotSource over the same store
        self.source_id = source_id
        self.timeout_ms = timeout_ms
        self.requested_by = requested_by

    async def get_troubleshooting(self, serial: str, *, code: str | None = None) -> dict[str, Any]:
        serial = (serial or "").strip().upper()
        if not SERIAL_RE.match(serial):
            return {"status": "INVALID_SERIAL", "serial": serial, "handled": True,
                    "text": f"'{serial}' is not a valid equipment serial number.", "trace": []}
        return await self.ask(f"Check troubleshooting for {serial}" + (f" code {code}" if code else ""))

    async def ask(self, utterance: str, *, active_serial: str | None = None,
                  live: bool = False) -> dict[str, Any]:
        """`live=True` reads SIS now even when a stored result could answer —
        used by the real-SIS integration check, so nothing is answered from old data."""
        maia = InvestigationService(self.source)
        trace: list[dict[str, Any]] = []
        out = maia.handle(utterance, active_serial=active_serial)
        trace.append({"step": "route", "intent": out.get("intent"), "serial": out.get("serial")
                      or (out.get("serials") or [None])[0], "status": out.get("status"),
                      "serial_source": out.get("serial_source")})
        serial = out.get("serial") or (out.get("serials") or [None])[0]
        if live and serial and out.get("intent") in ("PARTS", "TROUBLESHOOTING", "FULL_ANALYSIS",
                                                     "MODEL_3D") \
                and out.get("status") not in ("INVALID_SERIAL", "NEEDS_RETRIEVAL"):
            out = {**out, "status": "NEEDS_RETRIEVAL",
                   "retrieve": {"serial": serial, "investigate": SECTIONS.get(out["intent"], []),
                                "locate": []}}
        if out.get("status") != "NEEDS_RETRIEVAL":
            trace.append({"step": "answer", "from": "stored verified SIS result"})
            return {**out, "trace": trace}

        rq = out["retrieve"]
        started = time.monotonic()
        step: dict[str, Any] = {"step": "sis_lookup", "tool": "EquipmentService.lookup",
                                "source": self.source_id, "serial": rq["serial"],
                                "investigate": rq["investigate"], "mode": "FORCE_REFRESH"}
        trace.append(step)
        try:
            result = await self.equipment.lookup(EquipmentSearchRequest(
                serial_number=rq["serial"], source=self.source_id, wait=True,
                timeout_ms=self.timeout_ms, mode=SearchMode.FORCE_REFRESH,
                reason="user_request", requested_by=self.requested_by,
                investigate=rq["investigate"], locate=rq["locate"]))
        except AutomationError as err:
            why = REASON.get(err.code, err.message)
            step.update(ok=False, error_code=err.code.value, ms=int((time.monotonic() - started) * 1000))
            return {**out, "status": "SIS_FAILED", "error_code": err.code.value, "reason": why,
                    "text": f"I could not read {rq['serial']} from Caterpillar SIS: {why}.",
                    "trace": trace}
        run_id = getattr(result, "automation_run_id", None)
        step.update(ok=True, run_id=run_id, ms=int((time.monotonic() - started) * 1000))

        snap = next((s for s in reversed(self.source.snapshots(rq["serial"]))
                     if s.run_id == run_id), None)
        if snap is None or snap.source_system != self.source_id:
            step["saved"] = False
            return {**out, "status": "SIS_FAILED", "reason": "the SIS run finished but its "
                    "saved result is not a Caterpillar SIS result", "trace": trace,
                    "text": f"I could not confirm a Caterpillar SIS result for {rq['serial']}."}
        step.update(saved=True, file=snap.file, final_url=snap.metadata.get("final_url"),
                    sections=_section_report(snap.investigation))

        again = maia.handle(utterance, active_serial=active_serial)
        trace.append({"step": "answer", "from": f"SIS run {run_id}", "status": again.get("status")})
        return {**again, "retrieved": {"run_id": run_id, "file": snap.file}, "trace": trace}


def _section_report(inv: dict[str, Any] | None) -> dict[str, Any]:
    inv = inv or {}
    tr, m = inv.get("troubleshooting") or {}, inv.get("model_3d") or {}
    return {
        "troubleshooting": None if not tr else {
            "tab_opened": bool((tr.get("tab") or {}).get("opened")), "status": tr.get("status"),
            "reason": tr.get("reason"), "url": tr.get("url"),
            "sections": [(s.get("section"), s.get("count_displayed"), s.get("items_read"))
                         for s in tr.get("sections") or []]},
        "model_3d": None if not m else {
            "tab_opened": bool((m.get("tab") or {}).get("opened")), "status": m.get("status"),
            "reason": m.get("reason")},
    }


__all__ = ["InvestigationRunner", "REASON"]
