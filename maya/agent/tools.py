"""Maya's tool layer: JSON definitions + a deterministic HTTP dispatcher.

The dispatcher is the only bridge between the model and the platform. It
validates arguments again (the model is not trusted as an input source), calls
the automation API, and returns a uniform envelope in which `data` never appears
without `attribution`.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

TOOLS_PATH = Path(__file__).resolve().parents[1] / "schemas" / "maya_tools.json"
SERIAL_RE = re.compile(r"^[A-Z0-9]{3,17}$")
API_BASE = os.environ.get("MAYA_API_BASE", "http://localhost:8080")


def load_tools() -> list[dict[str, Any]]:
    """Tool definitions, unchanged every turn so the prompt prefix stays cacheable."""
    return json.loads(TOOLS_PATH.read_text())["tools"]


def normalize_serial(raw: str) -> str:
    arabic = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    return re.sub(r"[\s\-_/.]", "", (raw or "").translate(arabic)).upper()


def _envelope_error(code: str, message: str, run_id: str | None = None,
                    fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    # No `data` key. There is nothing here to paraphrase into a confident answer.
    out: dict[str, Any] = {"ok": False, "error_code": code, "user_message_hint": message}
    if run_id:
        out["automation_run_id"] = run_id
    if fallback and fallback.get("available"):
        out["fallback"] = {
            "age_days": fallback.get("age_days"),
            "freshness": fallback.get("freshness"),
            "attribution": fallback.get("attribution"),
            "data": fallback.get("data"),
        }
    return out


def _envelope_success(payload: dict[str, Any]) -> dict[str, Any]:
    attribution = payload.get("attribution")
    if not attribution:
        return _envelope_error("INVALID_DATA", "Result arrived without attribution; discarded.")
    return {
        "ok": True,
        "attribution": attribution,
        "cache": payload.get("cache"),
        "persisted": payload.get("persisted", True),
        "execution_time_ms": payload.get("execution_time_ms"),
        "data": payload["data"],
    }


class MayaToolDispatcher:
    def __init__(self, *, api_base: str = API_BASE, actor: str = "maya-agent",
                 scopes: str = "equipment.read equipment.search",
                 timeout_s: float = 45.0) -> None:
        self.api_base = api_base.rstrip("/")
        self.headers = {"x-maya-actor": actor, "x-maya-scopes": scopes}
        self.timeout_s = timeout_s

    async def dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = {
            "get_equipment_from_database": self.get_equipment_from_database,
            "search_equipment_in_sis": self.search_equipment_in_sis,
            "get_equipment_history": self.get_equipment_history,
            "get_automation_run_status": self.get_automation_run_status,
            "save_equipment_data": self.save_equipment_data,
        }.get(name)
        if handler is None:
            return _envelope_error("INTERNAL_ERROR", f"Unknown tool '{name}'.")
        try:
            return await handler(**args)
        except TypeError as exc:
            return _envelope_error("INVALID_SERIAL", f"Bad tool arguments: {exc}")
        except httpx.TimeoutException:
            return _envelope_error("TIMEOUT", "The automation API did not respond in time.")
        except httpx.HTTPError as exc:
            return _envelope_error("NETWORK_ERROR", f"Automation API unreachable: {exc}")

    # ── tools ───────────────────────────────────────────────────────────────
    async def get_equipment_from_database(self, serial_number: str,
                                          source: str | None = None) -> dict[str, Any]:
        serial = normalize_serial(serial_number)
        if not SERIAL_RE.match(serial):
            return _envelope_error("INVALID_SERIAL", "Serial must be 3-17 alphanumeric characters.")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.api_base}/v1/equipment/{serial}",
                                    params={"source": source} if source else None,
                                    headers=self.headers)
        body = resp.json()
        if resp.status_code == 200:
            return {**_envelope_success(body), "found": True}
        if body.get("error_code") == "SERIAL_NOT_FOUND":
            # A clean, unambiguous negative: not an error the user needs to hear about.
            return {"ok": True, "found": False, "serial_number": serial,
                    "reason": "NOT_IN_STORE",
                    "user_message_hint": "Not in the internal store; SIS lookup is needed."}
        return _envelope_error(body.get("error_code", "INTERNAL_ERROR"),
                               body.get("user_message_hint", "Store lookup failed."))

    async def search_equipment_in_sis(self, serial_number: str, reason: str = "user_request",
                                      mode: str = "auto") -> dict[str, Any]:
        serial = normalize_serial(serial_number)
        if not SERIAL_RE.match(serial):
            return _envelope_error("INVALID_SERIAL", "Serial must be 3-17 alphanumeric characters.")
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(
                f"{self.api_base}/v1/equipment/search",
                json={"serial_number": serial, "source": "cat_sis", "mode": mode,
                      "reason": reason, "wait": True,
                      "timeout_ms": int(self.timeout_s * 1000) - 5000},
                headers=self.headers)
        body = resp.json()
        if resp.status_code == 200:
            return {**_envelope_success(body), "found": True}
        if resp.status_code == 202:
            return {"ok": True, "status": "in_progress",
                    "automation_run_id": body.get("automation_run_id"),
                    "poll_after_ms": body.get("poll_after_ms", 4000),
                    "user_message_hint": "Automation is running; poll the run id."}
        return _envelope_error(body.get("error_code", "INTERNAL_ERROR"),
                               body.get("user_message_hint", body.get("message", "Lookup failed.")),
                               run_id=body.get("automation_run_id"),
                               fallback=body.get("fallback"))

    async def get_equipment_history(self, serial_number: str, limit: int = 10) -> dict[str, Any]:
        serial = normalize_serial(serial_number)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.api_base}/v1/equipment/{serial}/history",
                                    params={"limit": limit}, headers=self.headers)
        body = resp.json()
        if resp.status_code != 200:
            return _envelope_error(body.get("error_code", "INTERNAL_ERROR"),
                                   body.get("user_message_hint", "History lookup failed."))
        return {"ok": True, "serial_number": serial, "versions": body.get("versions", []),
                "count": body.get("count", 0)}

    async def get_automation_run_status(self, automation_run_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.api_base}/v1/runs/{automation_run_id}",
                                    headers=self.headers)
        body = resp.json()
        if resp.status_code != 200:
            return _envelope_error("INTERNAL_ERROR", "Unknown automation run id.")
        run = body["run"]
        return {"ok": True, "automation_run_id": automation_run_id,
                "run_status": run.get("status"), "error_code": run.get("error_code"),
                "execution_time_ms": run.get("execution_time_ms"),
                "steps": [{"step": s.get("step"), "status": s.get("status"),
                           "duration_ms": s.get("duration_ms")}
                          for s in (run.get("steps_executed") or [])],
                "retry_count": run.get("retry_count")}

    async def save_equipment_data(self, serial_number: str, source: str,
                                  data: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(f"{self.api_base}/v1/equipment/save",
                                     json={"data": {**data,
                                                    "serial_number": normalize_serial(serial_number)},
                                           "source": source},
                                     headers=self.headers)
        body = resp.json()
        if resp.status_code != 200:
            detail = body.get("detail", body)
            return _envelope_error(detail.get("error_code", "INTERNAL_ERROR"),
                                   detail.get("user_message_hint", "Save failed."))
        return {"ok": True, "saved": True, "changed": body.get("changed"),
                "quality_score": body.get("quality_score")}
