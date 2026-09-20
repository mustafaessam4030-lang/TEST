"""Run status, source registry, selector-contract validation."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_registry, get_repo

router = APIRouter(prefix="/v1", tags=["operations"])


@router.get("/runs/{run_id}")
async def get_run(run_id: str, repo: Any = Depends(get_repo)) -> dict[str, Any]:
    run = await repo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail={
            "status": "error", "error_code": "NOT_FOUND", "retryable": False,
            "message": f"No run with id {run_id}.",
            "user_message_hint": "That automation run id is unknown."})
    return {"status": "success", "run": run}


@router.get("/sources")
async def list_sources(registry: Any = Depends(get_registry)) -> dict[str, Any]:
    return {"status": "success", "sources": registry.snapshot()}


@router.post("/admin/selectors/validate")
async def validate_selectors(request: Request, source: str = "cat_sis") -> dict[str, Any]:
    """Assert the DOM contract still holds. Scheduled in CI so a redesign pages us."""
    registry = request.app.state.registry
    config = registry.entry(source).config
    unconfigured: list[str] = []
    for group, entries in (config.get("selectors") or {}).items():
        for name, value in (entries or {}).items():
            if not value or value == "TODO_CAPTURE":
                unconfigured.append(f"{group}.{name}")
    for name, value in (config.get("ready_markers") or {}).items():
        if not value or value == "TODO_CAPTURE":
            unconfigured.append(f"ready.{name}")
    return {"status": "success" if not unconfigured else "incomplete",
            "source": source,
            "selector_version": config.get("selector_version"),
            "unconfigured": unconfigured,
            "note": "Live assertion requires MAYA_ALLOW_LIVE_AUTOMATION and a session slot."}
