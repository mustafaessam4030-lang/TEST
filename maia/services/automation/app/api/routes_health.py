from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import get_repo

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, repo: Any = Depends(get_repo)) -> dict[str, Any]:
    return {"status": "ok" if await repo.health() else "degraded",
            "repository": request.app.state.settings.repository,
            "live_automation": request.app.state.settings.allow_live_automation,
            "sources": request.app.state.registry.snapshot()}


@router.get("/metrics")
async def metrics() -> Response:
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    except ImportError:
        return Response("prometheus_client not installed\n", media_type="text/plain")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
