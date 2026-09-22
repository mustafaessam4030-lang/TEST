"""The chat page's model endpoint — now Snowflake Cortex, and only Cortex.

The page sends `{system, messages}` here, as it always has. The gateway
answers with Cortex (Agents REST or AI_COMPLETE, whichever the account
supports). There is no other provider behind this route: if Cortex cannot run,
the page is told so (503 CORTEX_UNAVAILABLE with the reason) and shows its
rules engine, labelled as such.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.errors import AutomationError

router = APIRouter(prefix="/v1/llm", tags=["llm"])
MAX_MESSAGES = 20
MAX_CHARS = 20_000


def _transcript(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for msg in (raw or [])[-MAX_MESSAGES:]:
        if not isinstance(msg, dict) or msg.get("role") not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            text = " ".join(str(b.get("text", "")) for b in content
                            if isinstance(b, dict) and b.get("type") == "text")
        else:
            text = str(content or "")
        if text.strip():
            out.append({"role": msg["role"], "text": text[:MAX_CHARS]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    s = request.app.state.cortex_runtime.status()
    return {"configured": s["configured"], "provider": "snowflake-cortex", "mode": s["mode"],
            "model": s["model"] if s["mode"] == "complete" else s["agent_model"],
            "reason": s["reason"], "fix": s["fix"]}


@router.post("/messages")
async def messages(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    transcript = _transcript(body.get("messages"))
    if not transcript:
        return JSONResponse({"error": "no_messages"}, status_code=400)
    system = str(body.get("system") or "")[:MAX_CHARS]
    maia = request.app.state.maia
    try:
        text = await maia.chat(system, transcript)
    except AutomationError as err:
        return JSONResponse(status_code=503, content={
            "error": err.code.value, "reason": err.details.get("reason"),
            "fix": err.details.get("fix"), "message": err.message})
    runtime = request.app.state.cortex_runtime
    return JSONResponse({"content": [{"type": "text", "text": str(text)}],
                         "model": f"snowflake-cortex/{runtime.status()['mode']}",
                         "stop_reason": "end_turn"})
