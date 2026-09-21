"""FastAPI application wiring. Nothing here knows about a prompt or a model."""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.adapters.browser import BrowserPool
from app.adapters.cat_sis import CatSisAdapter
from app.adapters.registry import SourceRegistry
from app.api import routes_equipment, routes_health, routes_runs
from app.config import get_settings
from app.core import logging as mlog
from app.core.errors import AutomationError, ErrorCode, USER_HINT, http_status
from app.core.secrets import resolve_secret
from app.adapters import selector_store
from app.domain.freshness import FreshnessPolicy
from app.services.capture_service import CaptureService
from app.services.equipment_service import EquipmentService

logger = logging.getLogger(__name__)


def build_repository(settings: Any) -> Any:
    if settings.repository == "snowflake":
        from app.repositories.snowflake_repo import SnowflakeEquipmentRepository

        return SnowflakeEquipmentRepository({
            "account": settings.snowflake_account,
            "user": settings.snowflake_user,
            "role": settings.snowflake_role,
            "warehouse": settings.snowflake_warehouse,
            "database": settings.snowflake_database,
            "schema": settings.snowflake_schema,
            "private_key_file": (settings.snowflake_private_key_ref or "").removeprefix("file://")
            or None,
        })
    if settings.repository == "memory":
        from app.repositories.memory_repo import MemoryEquipmentRepository

        return MemoryEquipmentRepository()

    # Default: the temporary local JSON store. Every successful lookup lands in
    # logs/sis-results/ as JSON + TXT + screenshots, and a failed write fails
    # the lookup rather than being reported as a success.
    from app.repositories.local_json_repo import LocalJsonRepository

    store_dir = Path(settings.local_store_dir)
    if not store_dir.is_absolute():
        store_dir = Path(__file__).resolve().parents[3] / store_dir
    # The live credential values, held only so they can be searched for and
    # masked in anything written. They are never logged or returned.
    secrets = tuple(v for v in (os.environ.get("SIS_USERNAME"),
                                os.environ.get("SIS_PASSWORD")) if v)
    return LocalJsonRepository(store_dir, secrets=secrets)


def build_registry(settings: Any) -> SourceRegistry:
    registry = SourceRegistry()
    sis_cfg = settings.source_config("cat_sis")

    # Selectors captured from a real authenticated session override the YAML
    # placeholders. Only entries marked confidence=verified are taken; anything
    # still TODO_CAPTURE stays missing so the adapter fails loudly.
    captured_path = os.environ.get("MAIA_SIS_SELECTORS",
                                   str(Path(settings.sources_dir).parent / "sis_selectors.json"))
    try:
        captured = selector_store.load(captured_path)
    except selector_store.SelectorStoreError as exc:
        mlog.log(logger, logging.ERROR, "selectors.invalid", path=captured_path, error=str(exc))
        captured = {}
    if captured:
        problems = selector_store.validate(captured)
        missing = selector_store.missing_required(captured)
        sis_cfg = selector_store.merge_into_config(sis_cfg, captured)
        mlog.log(logger, logging.INFO, "selectors.loaded", path=captured_path,
                 profile=captured.get("capture_profile"),
                 version=captured.get("selector_version"),
                 verified=len(selector_store.verified_selectors(captured)),
                 missing_required=missing, invalid=list(problems))
    registry.register(
        "cat_sis",
        sis_cfg.get("label", "Caterpillar SIS"),
        sis_cfg,
        lambda cfg: CatSisAdapter(cfg, secret_provider=resolve_secret),
        enabled=bool(sis_cfg),
        precedence=int(sis_cfg.get("precedence", 10)),
    )
    # An offline source used to exercise the whole chain with a real browser when
    # SIS itself is not reachable. Opt-in only, lowest precedence, and its label
    # carries the warning into every answer and audit row.
    if os.environ.get("MAIA_ENABLE_FIXTURE_SOURCE", "").lower() == "true":
        fixture_cfg = settings.source_config("local_fixture")
        if fixture_cfg:
            registry.register(
                "local_fixture", fixture_cfg.get("label", "Local test fixture"),
                fixture_cfg,
                lambda cfg: CatSisAdapter(cfg, secret_provider=resolve_secret),
                enabled=True, precedence=int(fixture_cfg.get("precedence", 900)))
            mlog.log(logger, logging.WARNING, "source.fixture_enabled",
                     note="local_fixture is registered; it is NOT Caterpillar SIS")

    # Adding cat_pcc / dealer_erp / telematics is one register() call each — no
    # change to the service, the API, Maia's tools, or the warehouse schema.
    return registry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    mlog.setup(settings.log_level)
    repo = build_repository(settings)
    registry = build_registry(settings)
    pool = BrowserPool(headless=settings.headless, max_contexts=settings.max_contexts,
                       artifact_dir=settings.artifact_dir,
                       executable_path=settings.chromium_path)
    # The store labels each record with the name of the source that produced
    # it, taken from the registry. Nothing else may supply that name.
    if hasattr(repo, "source_labels"):
        repo.source_labels.update({e["source_id"]: e["label"] for e in registry.snapshot()})
    app.state.settings = settings
    app.state.repo = repo
    app.state.registry = registry
    app.state.pool = pool
    capture = CaptureService(registry=registry, settings=settings)
    app.state.capture_service = capture
    app.state.equipment_service = EquipmentService(
        repo=repo, registry=registry, pool=pool,
        freshness=FreshnessPolicy(settings.freshness_policy()), settings=settings,
        capture=capture)
    mlog.log(logger, logging.INFO, "service.started", environment=settings.environment,
             repository=settings.repository, live_automation=settings.allow_live_automation)
    try:
        yield
    finally:
        await pool.stop()


app = FastAPI(
    title="Maia Equipment Automation API",
    version="1.1.0",
    description="Deterministic tools for the Maia orchestrator: cache → freshness → "
                "source automation → normalize → validate → persist, fully audited.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("MAIA_CORS_ORIGINS", "").split(",") or [],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlate(request: Request, call_next: Any) -> Any:
    trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex
    mlog.clear()
    mlog.bind(trace_id=trace_id, path=request.url.path,
              actor=request.headers.get("x-maia-actor"))
    response = await call_next(request)
    response.headers["x-trace-id"] = trace_id
    return response


@app.exception_handler(AutomationError)
async def automation_error_handler(_: Request, err: AutomationError) -> JSONResponse:
    return JSONResponse(status_code=http_status(err.code), content=err.to_payload())


@app.exception_handler(Exception)
async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    mlog.log(logger, logging.ERROR, "unhandled_error", error=str(exc)[:500])
    return JSONResponse(status_code=500, content={
        "status": "error", "error_code": ErrorCode.INTERNAL_ERROR.value, "retryable": False,
        "message": "An internal error occurred.",
        "user_message_hint": USER_HINT[ErrorCode.INTERNAL_ERROR]})


app.include_router(routes_equipment.router)
app.include_router(routes_runs.router)
app.include_router(routes_health.router)
