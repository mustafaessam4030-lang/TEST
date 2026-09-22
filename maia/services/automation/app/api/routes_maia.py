"""Maia's runtime intelligence, behind the gateway: Snowflake Cortex.

    POST /v1/maia/answer    one equipment turn: brain → tools → Cortex → verify
    GET  /v1/cortex/status  configuration only; no network call, no secret
    POST /v1/cortex/check   a live, minimal Cortex call that proves access
    POST /v1/tools/{name}   the same controlled tools, for operators and tests
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent.state import ConversationContext
from app.api.routes_agent import _trusted_context
from app.core.errors import AutomationError, http_status

router = APIRouter(tags=["maia"])


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    utterance: str = Field(max_length=4000)
    context: ConversationContext | None = None


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.post("/v1/maia/answer")
async def answer(req: AnswerRequest, request: Request) -> Any:
    state = request.app.state
    context = await _trusted_context(req.context, state.repo)
    result = await state.maia.answer(req.utterance, context)
    return JSONResponse(status_code=200, content=_jsonable(result))


@router.get("/v1/cortex/status")
async def cortex_status(request: Request) -> dict[str, Any]:
    return request.app.state.cortex_runtime.status()


@router.post("/v1/cortex/check")
async def cortex_check(request: Request) -> Any:
    runtime = request.app.state.cortex_runtime
    try:
        mode = runtime.mode()
        if mode == "agent":
            msg = await runtime.agent_run(
                [{"role": "user", "content": [{"type": "text", "text": "Reply with: OK"}]}],
                tools=[], instructions={"response": "Reply with exactly: OK"})
            text = " ".join(i.get("text", "") for i in msg.get("content") or []
                            if i.get("type") == "text")
        else:
            text = await runtime.complete("Reply with exactly: OK", max_tokens=5)
    except AutomationError as err:
        return JSONResponse(status_code=http_status(err.code), content=err.to_payload())
    return {"ok": True, "mode": mode, "reply": str(text)[:40]}


@router.post("/v1/tools/{name}")
async def call_tool(name: str, req: ToolRequest, request: Request) -> Any:
    from app.domain.validate import validate_serial
    from app.tools.equipment_tools import TOOLS, ToolContext, run_tool

    if name not in TOOLS:
        raise HTTPException(status_code=404, detail=f"unknown tool {name}")
    state = request.app.state
    serial = str(req.arguments.get("serial_number") or "")
    try:
        allowed = {validate_serial(serial)} if serial else set()
    except AutomationError as err:
        return JSONResponse(status_code=400, content=err.to_payload())
    ctx = ToolContext(service=state.equipment_service, repo=state.repo, allowed_serials=allowed,
                      wants_fresh=name == "refresh_equipment_from_sis")
    result = await run_tool(ctx, name, req.arguments)
    return JSONResponse(status_code=200, content=_jsonable({**result, "trace": ctx.trace}))


def _jsonable(value: Any) -> Any:
    import json

    return json.loads(json.dumps(value, default=str))
