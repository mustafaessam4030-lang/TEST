"""Equipment endpoints. Success carries attribution; failure carries no data."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.api.deps import actor, enforce_quota, get_service
from app.core.errors import AutomationError, http_status
from app.models.schemas import (
    EquipmentSearchRequest, EquipmentSearchResponse, InProgressResponse, SaveEquipmentRequest,
    SearchMode,
)

router = APIRouter(prefix="/v1/equipment", tags=["equipment"])


def _error_response(err: AutomationError) -> JSONResponse:
    payload = err.to_payload(run_id=err.details.pop("automation_run_id", None))
    fallback = err.details.pop("fallback", None)
    if fallback and fallback.get("available"):
        payload["fallback"] = fallback
    payload["serial_number"] = err.details.get("serial_number")
    return JSONResponse(status_code=http_status(err.code), content=payload)


@router.post("/search", response_model=None, summary="Cache → freshness → source → save")
async def search_equipment(req: EquipmentSearchRequest, request: Request,
                           service: Any = Depends(get_service),
                           actor_id: str = Depends(actor)) -> Any:
    req.requested_by = req.requested_by if req.requested_by != "unknown" else actor_id
    if req.mode != SearchMode.CACHE_ONLY:
        enforce_quota(request, actor_id)
    try:
        result = await service.lookup(req)
    except AutomationError as err:
        err.details["serial_number"] = req.serial_number
        return _error_response(err)
    if isinstance(result, InProgressResponse):
        return JSONResponse(status_code=202, content=result.model_dump(mode="json"))
    return JSONResponse(status_code=200, content=result.model_dump(mode="json"))


@router.get("/{serial_number}", response_model=None, summary="Store-only read")
async def get_equipment(serial_number: str, source: str | None = Query(default=None),
                        service: Any = Depends(get_service)) -> Any:
    try:
        result: EquipmentSearchResponse = await service.get_from_store(serial_number, source)
    except AutomationError as err:
        err.details["serial_number"] = serial_number
        return _error_response(err)
    return JSONResponse(status_code=200, content=result.model_dump(mode="json"))


@router.get("/{serial_number}/history", response_model=None)
async def get_history(serial_number: str, source: str | None = Query(default=None),
                      limit: int = Query(default=20, ge=1, le=100),
                      service: Any = Depends(get_service)) -> Any:
    try:
        rows = await service.history(serial_number, source, limit)
    except AutomationError as err:
        err.details["serial_number"] = serial_number
        return _error_response(err)
    return {"status": "success", "serial_number": serial_number, "versions": rows,
            "count": len(rows)}


@router.post("/save", response_model=None, summary="Explicit upsert (requires equipment.write)")
async def save_equipment(req: SaveEquipmentRequest, request: Request,
                         service: Any = Depends(get_service),
                         actor_id: str = Depends(actor)) -> Any:
    from app.domain.validate import validate_equipment_data
    from app.models.schemas import EquipmentRecord

    scopes = set((request.headers.get("x-maya-scopes") or "").split())
    if "equipment.write" not in scopes:
        raise HTTPException(status_code=403, detail={
            "status": "error", "error_code": "FORBIDDEN", "retryable": False,
            "message": "Scope equipment.write is required.",
            "user_message_hint": "This action needs elevated permissions."})
    try:
        record = EquipmentRecord.model_validate({**req.data, "source_system": req.source})
        record = validate_equipment_data(record)
        changed = await service.repo.upsert(record)
    except AutomationError as err:
        return _error_response(err)
    except Exception as exc:
        raise HTTPException(status_code=422, detail={
            "status": "error", "error_code": "INVALID_DATA", "retryable": False,
            "message": str(exc)[:300],
            "user_message_hint": "The submitted record does not match the schema."}) from exc
    return {"status": "success", "serial_number": record.serial_number,
            "changed": changed, "saved_by": actor_id,
            "quality_score": record.quality.score}
