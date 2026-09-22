"""The understanding endpoint.

One brain, two callers: the browser chat (which has no model of its own for
this) and the Python agent loop (whose model proposes, and is then checked
here). Putting it behind HTTP rather than duplicating it in JavaScript is what
keeps the evaluation suite honest — what the tests measure is what ships.

Nothing here touches a browser, a credential or the store's write path. It
reads the store to tell a real serial from a plausible one, and it returns a
decision.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent.brain import MaiaBrain, explain_error, verify_result
from app.agent.state import AgentState, ConversationContext

router = APIRouter(prefix="/v1/agent", tags=["agent"])


class UnderstandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    utterance: str = Field(max_length=4000)
    #: the caller hands back what it was given last turn; there is no server session
    context: ConversationContext | None = None


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: AgentState
    result: dict[str, Any] | None = None


@router.post("/understand", response_model=None,
             summary="Intent, serial, candidates, plan — before any tool runs")
async def understand(req: UnderstandRequest, request: Request) -> Any:
    # A store is an advantage, not a requirement: without one the brain still
    # reads intent and identifiers, it simply cannot offer near-matches.
    repo = getattr(request.app.state, "repo", None)
    context = await _trusted_context(req.context, repo)
    state = await MaiaBrain(repo).understand(req.utterance, context)
    return JSONResponse(status_code=200, content=state.model_dump(mode="json"))


async def _trusted_context(context: ConversationContext | None,
                           repo: Any) -> ConversationContext | None:
    """Re-derive the one field a caller must not be able to assert.

    The context travels through the caller — a browser, or a model deciding
    what to pass back. `confirmed` is what lets a bare "what about the engine?"
    resolve to a machine without asking, so a caller that could simply declare
    it true could steer the conversation onto a machine nobody named. It is
    therefore never believed: it holds only while the store still has that
    record. Everything else in the context is a convenience and is harmless.
    """
    if context is None or not context.active_serial:
        return context
    trusted = context.model_copy(deep=True)
    if not trusted.confirmed:
        return trusted
    exists = False
    if repo is not None:
        try:
            exists = bool(await repo.get_any_source(trusted.active_serial))
        except Exception:
            exists = False
    trusted.confirmed = exists
    return trusted


@router.post("/verify", response_model=None,
             summary="Check a tool result before any of it is repeated")
async def verify(req: VerifyRequest) -> Any:
    state = verify_result(req.state, req.result)
    return JSONResponse(status_code=200, content=state.model_dump(mode="json"))


@router.get("/errors/{code}", response_model=None,
            summary="What a failure code means, in the user's language")
async def error_text(code: str, lang: str = "en", serial: str | None = None) -> Any:
    return {"error_code": code, "message": explain_error(code, serial, lang)}
