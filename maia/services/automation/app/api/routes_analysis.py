"""Maia's deterministic analysis — verified local SIS data only. No model.

    POST /v1/analysis/ask            {"utterance", "active_serial"?} → routed answer
    GET  /v1/analysis/{serial}       full structured analysis (JSON)
    GET  /v1/analysis/compare/{a}/{b}
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.analysis.service import AnalysisService
from app.analysis.snapshots import LocalStoreSnapshots
from app.config import ROOT

router = APIRouter(prefix="/v1/analysis", tags=["analysis"])


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    utterance: str = Field(max_length=2000)
    active_serial: str | None = Field(default=None, max_length=20)


def _service(request: Request) -> AnalysisService:
    settings = request.app.state.settings
    root = Path(settings.local_store_dir)
    return AnalysisService(LocalStoreSnapshots(root if root.is_absolute() else ROOT / root))


def _json(value: Any) -> JSONResponse:
    import json

    return JSONResponse(json.loads(json.dumps(value, default=str)))


@router.post("/ask")
async def ask(req: AskRequest, request: Request) -> JSONResponse:
    return _json(_service(request).handle(req.utterance, active_serial=req.active_serial))


@router.get("/compare/{a}/{b}")
async def compare(a: str, b: str, request: Request) -> JSONResponse:
    return _json(_service(request).compare(a, b))


@router.get("/{serial}")
async def analyze(serial: str, request: Request) -> JSONResponse:
    return _json(_service(request).analyze(serial))
