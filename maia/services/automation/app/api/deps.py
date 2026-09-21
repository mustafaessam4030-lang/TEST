"""Request-scoped dependencies: identity, quota, correlation."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Header, HTTPException, Request

from app.core.errors import ErrorCode, USER_HINT

_actor_calls: dict[str, deque[float]] = defaultdict(deque)


def get_service(request: Request) -> Any:
    return request.app.state.equipment_service


def get_repo(request: Request) -> Any:
    return request.app.state.repo


def get_registry(request: Request) -> Any:
    return request.app.state.registry


def actor(x_maia_actor: str | None = Header(default=None)) -> str:
    """End-user identity is forwarded by the agent and recorded on every run."""
    return (x_maia_actor or "anonymous")[:120]


def enforce_quota(request: Request, actor_id: str) -> None:
    limit = request.app.state.settings.per_actor_hourly_quota
    window, now = 3600.0, time.monotonic()
    calls = _actor_calls[actor_id]
    while calls and now - calls[0] > window:
        calls.popleft()
    if len(calls) >= limit:
        raise HTTPException(status_code=429, detail={
            "status": "error", "error_code": ErrorCode.RATE_LIMITED.value, "retryable": True,
            "message": f"Automation quota reached ({limit}/hour).",
            "user_message_hint": USER_HINT[ErrorCode.RATE_LIMITED]})
    calls.append(now)
