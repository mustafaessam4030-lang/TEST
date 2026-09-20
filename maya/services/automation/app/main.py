"""FastAPI application wiring. Nothing here knows about a prompt or a model."""
from __future__ import annotations

import logging
import os
import uuid
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
from app.domain.freshness import FreshnessPolicy
from app.services.equipment_service import EquipmentService

logger = logging.getLogger(__name__)


def resolve_secret(ref: str) -> dict[str, str]:
    """Resolve a credential reference. Never reads from a request and never logs.

    Production binds this to Vault/Key Vault. The env fallback exists so a
    developer can run locally without a vault; it is still never in a prompt.
    """
    if ref.startswith("env://"):
        prefix = ref.removeprefix("env://")
        return {"username": os.environ.get(f"{prefix}_USERNAME", ""),
                "password": os.environ.get(f"{prefix}_PASSWORD", "")}
    if ref.startswith("vault://"):
        try:
            import hvac  # optional dependency
        except ImportError as exc:  # pragma: no cover
            raise AutomationError(ErrorCode.LOGIN_FAILED,
                                  "Vault client is not installed.") from exc
        client = hvac.Client(url=os.environ["VAULT_ADDR"], token=os.environ["VAULT_TOKEN"])
        path = ref.removeprefix("vault://kv/")
        data = client.secrets.kv.v2.read_secret_version(path=path)["data"]["data"]
        return {"username": data.get("username", ""), "password": data.get("password", "")}
    raise AutomationError(ErrorCode.LOGIN_FAILED, f"Unsupported secret reference scheme: {ref[:12]}")


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
    from app.repositories.memory_repo import MemoryEquipmentRepository

    return MemoryEquipmentRepository()


def build_registry(settings: Any) -> SourceRegistry:
    registry = SourceRegistry()
    sis_cfg = settings.source_config("cat_sis")
    registry.register(
        "cat_sis",
        sis_cfg.get("label", "Caterpillar SIS"),
        sis_cfg,
        lambda cfg: CatSisAdapter(cfg, secret_provider=resolve_secret),
        enabled=bool(sis_cfg),
        precedence=int(sis_cfg.get("precedence", 10)),
    )
    # Adding cat_pcc / dealer_erp / telematics is one register() call each — no
    # change to the service, the API, Maya's tools, or the warehouse schema.
    return registry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    mlog.setup(settings.log_level)
    repo = build_repository(settings)
    registry = build_registry(settings)
    pool = BrowserPool(headless=settings.headless, max_contexts=settings.max_contexts,
                       artifact_dir=settings.artifact_dir)
    app.state.settings = settings
    app.state.repo = repo
    app.state.registry = registry
    app.state.pool = pool
    app.state.equipment_service = EquipmentService(
        repo=repo, registry=registry, pool=pool,
        freshness=FreshnessPolicy(settings.freshness_policy()), settings=settings)
    mlog.log(logger, logging.INFO, "service.started", environment=settings.environment,
             repository=settings.repository, live_automation=settings.allow_live_automation)
    try:
        yield
    finally:
        await pool.stop()


app = FastAPI(
    title="Maya Equipment Automation API",
    version="1.1.0",
    description="Deterministic tools for the Maya orchestrator: cache → freshness → "
                "source automation → normalize → validate → persist, fully audited.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("MAYA_CORS_ORIGINS", "").split(",") or [],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlate(request: Request, call_next: Any) -> Any:
    trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex
    mlog.clear()
    mlog.bind(trace_id=trace_id, path=request.url.path,
              actor=request.headers.get("x-maya-actor"))
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
