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


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, request: Request) -> dict[str, Any]:
    """An operator signals that they have completed the verification at the source.

    The paused run continues in the same browser session; nothing about the
    verification itself is automated here.
    """
    service = request.app.state.equipment_service
    if not service.resume_run(run_id):
        raise HTTPException(status_code=409, detail={
            "status": "error", "error_code": "NOT_PAUSED", "retryable": False,
            "message": f"Run {run_id} is not waiting for a human.",
            "user_message_hint": "That run is not paused."})
    return {"status": "success", "automation_run_id": run_id, "resumed": True}


@router.get("/runs")
async def list_paused(request: Request) -> dict[str, Any]:
    return {"status": "success",
            "awaiting_human": request.app.state.equipment_service.paused_runs()}


@router.post("/sources/{source_id}/capture")
async def capture_source(source_id: str, request: Request,
                         serial: str, missing_serial: str = "ZZZ00000") -> dict[str, Any]:
    """Learn a source's page contract now. `serial` must be one that EXISTS there —
    the capture proves each selector by searching it."""
    from app.core.errors import AutomationError, http_status

    service = request.app.state.capture_service
    try:
        return {"status": "success",
                **await service.learn(source_id, good_serial=serial,
                                      missing_serial=missing_serial)}
    except AutomationError as err:
        raise HTTPException(status_code=http_status(err.code),
                            detail=err.to_payload()) from err


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
