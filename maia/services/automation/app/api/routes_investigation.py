"""Investigation turns that may drive real SIS — through the existing lookup.

    POST /v1/investigation/ask        {"utterance", "active_serial"?}
    GET  /v1/troubleshooting/{serial}  ?code=36-1-5

Unlike /v1/analysis/ask (store only), these run the existing SIS lookup with the
Troubleshooting / 3D sections when the store does not have them yet, on the
service's own browser pool and saved session. The reply carries a `trace`:
intent, serial, the tool called, the SIS page reached, and what was read.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.analysis.snapshots import LocalStoreSnapshots
from app.config import ROOT
from app.services.troubleshooting_service import InvestigationRunner

router = APIRouter(tags=["investigation"])


class InvestigateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    utterance: str = Field(max_length=2000)
    active_serial: str | None = Field(default=None, max_length=20)


def _runner(request: Request) -> InvestigationRunner:
    settings = request.app.state.settings
    root = Path(settings.local_store_dir)
    return InvestigationRunner(request.app.state.equipment_service,
                               LocalStoreSnapshots(root if root.is_absolute() else ROOT / root),
                               requested_by="maia-chat")


def _json(value: Any) -> JSONResponse:
    return JSONResponse(json.loads(json.dumps(value, default=str)))


@router.post("/v1/investigation/ask")
async def investigation_ask(req: InvestigateRequest, request: Request) -> JSONResponse:
    return _json(await _runner(request).ask(req.utterance, active_serial=req.active_serial))


@router.get("/v1/troubleshooting/{serial}")
async def troubleshooting(serial: str, request: Request, code: str | None = None) -> JSONResponse:
    return _json(await _runner(request).get_troubleshooting(serial, code=code))
