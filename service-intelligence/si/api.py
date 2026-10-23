"""HTTP API and static UI (FastAPI).

    GET  /api/health                       liveness, backend kind, synthetic flag (no auth)
    GET  /api/me                           the signed-in user, roles, permissions, data scope
    GET  /api/meta                         metric registry with availability, dimensions and values, freshness
    GET  /api/overview?start&end&filters   KPI cards, period notes, data quality
    GET  /api/series?metric&grain&...      metric trend
    GET  /api/breakdown?metric&dimension&...
    GET  /api/drilldown?metric&...&limit&offset   the records behind a figure (analysis.drilldown)
    POST /api/recommendations/generate     evidence-backed candidates for a period and filters
    GET  /api/plan[?status]                saved recommendations visible to the user
    POST /api/plan                         save a generated candidate {snapshot_id, key, note}
    GET  /api/plan/{rec_id}                one recommendation with its audit history
    POST /api/plan/{rec_id}/status         {to, version, note}
    POST /api/plan/{rec_id}/assign         {owner, version, note}
    POST /api/plan/{rec_id}/comment        {note}
    POST /api/chat                         {message, conversation_id?, start?, end?, filters?}
    GET  /api/admin/queries                recent query log (admin.read)

`filters` is a JSON object such as {"region": ["North"]}. Dates are ISO dates in
the reporting time zone; `end` is inclusive in the API and exclusive internally.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import AuthError, Directory, Principal, authenticate
from .backends.base import BackendError
from .chat.assistant import AskContext
from .config import Settings, load_settings
from .plans import PlanError, safe_backend_call, visible
from .query import Period, QueryError, normalise_filters
from .registry import RegistryError
from .service import ServiceIntel

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
HTTP_FOR_BACKEND = {"timeout": 504, "warehouse_unavailable": 503, "connection": 503, "permission_denied": 502,
                    "refused": 400, "not_configured": 503, "query_failed": 502}


class GenerateRequest(BaseModel):
    start: date | None = None
    end: date | None = None
    filters: dict[str, list[str]] | None = None


class SaveRequest(BaseModel):
    snapshot_id: str = Field(min_length=3, max_length=40)
    key: str = Field(min_length=3, max_length=300)
    note: str | None = Field(default=None, max_length=2000)


class StatusRequest(BaseModel):
    to: str = Field(max_length=20)
    version: int
    note: str | None = Field(default=None, max_length=2000)


class AssignRequest(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    version: int
    note: str | None = Field(default=None, max_length=2000)


class CommentRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    start: date | None = None
    end: date | None = None
    filters: dict[str, list[str]] | None = None


def create_app(settings: Settings | None = None, service: ServiceIntel | None = None) -> FastAPI:
    settings = settings or load_settings()
    svc = service or ServiceIntel(settings)
    directory = Directory(settings.users_path)
    app = FastAPI(title="Service Intelligence", docs_url="/api/docs" if settings.env != "prod" else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if settings.env != "prod" else None)
    app.state.svc = svc

    @app.exception_handler(BackendError)
    async def backend_error(_req, exc: BackendError):
        return JSONResponse({"error": exc.kind, "message": str(exc), "query_id": exc.query_id},
                            status_code=HTTP_FOR_BACKEND.get(exc.kind, 502))

    @app.exception_handler(QueryError)
    @app.exception_handler(RegistryError)
    async def query_error(_req, exc: ValueError):
        return JSONResponse({"error": "invalid_request", "message": str(exc)}, status_code=400)

    @app.exception_handler(PlanError)
    async def plan_error(_req, exc: PlanError):
        return JSONResponse({"error": "plan", "message": str(exc)}, status_code=exc.status)

    @app.exception_handler(AuthError)
    async def auth_error(_req, exc: AuthError):
        return JSONResponse({"error": "auth", "message": str(exc)}, status_code=exc.status)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                                   "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def principal(request: Request) -> Principal:
        return authenticate(settings.auth_mode, directory, request.headers, settings.trusted_user_header,
                            settings.trusted_groups_header)

    def need(p: Principal, permission: str) -> None:
        if not p.can(permission):
            raise HTTPException(403, f"Your role does not allow {permission}")

    def parse_filters(raw: str | dict | None) -> dict:
        if raw in (None, "", "{}"):
            return {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raise QueryError("filters must be a JSON object") from None
        if not isinstance(raw, dict):
            raise QueryError("filters must be a JSON object")
        return normalise_filters(raw, svc.available)

    def scope_of(p: Principal) -> dict:
        return normalise_filters(p.scope, svc.available) if p.scope else {}

    def fresh_for(p: Principal) -> dict:
        return svc.analytics.freshness(scope_of(p), user=p.id)

    def period_of(start: date | None, end: date | None, fresh: dict) -> Period:
        if start and end:
            return Period(start, end + timedelta(days=1))
        if start or end:
            raise QueryError("Give both start and end, or neither")
        return svc.analytics.default_period(fresh["as_of"])

    def envelope(p: Principal, fresh: dict, period: Period | None = None, filters: dict | None = None,
                 result: dict | None = None, **body) -> dict:
        out = {**(result or {}), "data_source": svc.source.public(), "as_of": fresh.get("as_of_iso"), "freshness": {
            "as_of": fresh.get("as_of_iso"), "basis": fresh.get("basis"), "stale": fresh.get("stale"),
            "age_hours": round(fresh["age_hours"], 1) if fresh.get("age_hours") is not None else None,
            "max_age_hours": fresh.get("max_age_hours")},
            "reporting_tz": settings.reporting_tz, "scope": {k: list(v) for k, v in scope_of(p).items()}}
        if period:
            out["period"] = {"start": period.start.isoformat(), "end": (period.end - timedelta(days=1)).isoformat(),
                             "label": period.label(), "notes": svc.analytics.period_notes(period, fresh)}
        if filters is not None:
            out["filters"] = {k: list(v) for k, v in filters.items()}
        out.update(body)
        return out

    @app.get("/api/health")
    def health():
        return {"status": "ok", "backend": svc.backend.kind, "synthetic": svc.backend.synthetic, "env": settings.env,
                "registry_version": svc.registry.version}

    @app.get("/api/me")
    def me(p: Principal = Depends(principal)):
        return {**p.public(), "auth_mode": settings.auth_mode}

    @app.get("/api/meta")
    def meta(p: Principal = Depends(principal)):
        need(p, "analysis.read")
        fresh = fresh_for(p)
        scope = scope_of(p)
        dims = []
        for d, dim in svc.registry.dimensions.items():
            values = []
            if d in svc.available:
                res = svc.executor.run(svc.builder.dimension_values(d, scope), user=p.id)
                values = [{"value": r["value"], "n": int(r["n"])} for r in res.records()]
            dims.append({"id": d, "label": dim.label, "available": d in svc.available, "values": values})
        default = svc.analytics.default_period(fresh["as_of"])
        return envelope(p, fresh, None, None,
                        registry_version=svc.registry.version,
                        metrics=[m.public(svc.available) for m in svc.registry.metrics.values()],
                        dimensions=dims, parameters=svc.registry.parameters,
                        default_period={"start": default.start.isoformat(),
                                        "end": (default.end - timedelta(days=1)).isoformat()},
                        first_created=fresh["quality"]["first_created"],
                        mapping={"source_table": svc.mapping.source_table if not svc.backend.synthetic else svc.mapping.source_table + " (synthetic)",
                                 "due_at_source": svc.mapping.due_at_source, "available": sorted(svc.available)})

    @app.get("/api/overview")
    def overview(start: date | None = None, end: date | None = None, filters: str | None = None,
                 p: Principal = Depends(principal)):
        need(p, "analysis.read")
        fresh = fresh_for(p)
        period, f, scope = period_of(start, end, fresh), parse_filters(filters), scope_of(p)
        kpis = [svc.analytics.metric(m.id, period if m.kind == "period" else None, f, scope, fresh["as_of"], user=p.id)
                for m in svc.registry.metrics.values()]
        q = fresh["quality"]
        quality = {k: v for k, v in q.items() if k != "_raw"}
        return envelope(p, fresh, period, f, kpis=kpis, quality=quality)

    @app.get("/api/series")
    def series(metric: str, grain: str = "week", start: date | None = None, end: date | None = None,
               filters: str | None = None, p: Principal = Depends(principal)):
        need(p, "analysis.read")
        fresh = fresh_for(p)
        period, f = period_of(start, end, fresh), parse_filters(filters)
        return envelope(p, fresh, period, f, svc.analytics.series(metric, period, grain, f, scope_of(p),
                                                                 fresh["as_of"], user=p.id))

    @app.get("/api/breakdown")
    def breakdown(metric: str, dimension: str, start: date | None = None, end: date | None = None,
                  filters: str | None = None, limit: int = 25, p: Principal = Depends(principal)):
        need(p, "analysis.read")
        fresh = fresh_for(p)
        period, f = period_of(start, end, fresh), parse_filters(filters)
        return envelope(p, fresh, period, f, svc.analytics.breakdown(metric, dimension, period, f, scope_of(p),
                                                                    fresh["as_of"], limit=limit, user=p.id))

    @app.get("/api/drilldown")
    def drilldown(metric: str, start: date | None = None, end: date | None = None, filters: str | None = None,
                  limit: int = 50, offset: int = 0, p: Principal = Depends(principal)):
        need(p, "analysis.drilldown")
        fresh = fresh_for(p)
        period, f = period_of(start, end, fresh), parse_filters(filters)
        limit = max(1, min(limit, settings.drilldown_page))
        offset = max(0, min(offset, 100_000))
        return envelope(p, fresh, period, f, svc.analytics.drilldown(metric, period, f, scope_of(p), fresh["as_of"],
                                                                    limit, offset, user=p.id))

    @app.post("/api/recommendations/generate")
    def generate(body: GenerateRequest, p: Principal = Depends(principal)):
        need(p, "analysis.read")
        fresh = fresh_for(p)
        period, f = period_of(body.start, body.end, fresh), parse_filters(body.filters)
        res = svc.recommender.generate(period, f, scope_of(p), fresh, user=p.id)
        svc.remember_candidates(res["candidates"])
        if svc.plans is not None:
            for c in res["candidates"]:
                existing = safe_backend_call(svc.plans.find_open_by_key, c["key"])
                c["in_plan"] = existing
        return envelope(p, fresh, period, f, res, plan_enabled=svc.plans is not None)

    def plans_or_503():
        if svc.plans is None:
            raise HTTPException(503, "Plan storage is not configured (SI_APP_SCHEMA and SI_SNOWFLAKE_WRITER_ROLE)")
        return svc.plans

    @app.get("/api/plan")
    def plan_list(status: str | None = None, p: Principal = Depends(principal)):
        need(p, "plan.read")
        items = safe_backend_call(plans_or_503().list, status)
        scope = scope_of(p)
        return {"items": [i for i in items if visible(i["payload"], scope)], "data_source": svc.source.public()}

    def get_visible(rec_id: str, p: Principal) -> dict:
        rec = safe_backend_call(plans_or_503().get, rec_id)
        if not visible(rec["payload"], scope_of(p)):
            raise HTTPException(404, f"{rec_id} is not in the plan")
        return rec

    @app.post("/api/plan")
    def plan_add(body: SaveRequest, p: Principal = Depends(principal)):
        need(p, "plan.add")
        cand = svc.candidate(body.snapshot_id, body.key)
        if cand is None:
            raise HTTPException(410, "That candidate has expired; regenerate recommendations and save again")
        if not visible(cand, scope_of(p)) or cand["snapshot"].get("scope") != {k: list(v) for k, v in scope_of(p).items()}:
            raise HTTPException(403, "That candidate was generated for a different data scope")
        return safe_backend_call(plans_or_503().add, cand, p.id, body.note)

    @app.get("/api/plan/{rec_id}")
    def plan_get(rec_id: str, p: Principal = Depends(principal)):
        need(p, "plan.read")
        return get_visible(rec_id, p)

    @app.post("/api/plan/{rec_id}/status")
    def plan_status(rec_id: str, body: StatusRequest, p: Principal = Depends(principal)):
        need(p, "plan.review")
        get_visible(rec_id, p)
        return safe_backend_call(plans_or_503().transition, rec_id, body.to, p.id, set(p.permissions), body.version, body.note)

    @app.post("/api/plan/{rec_id}/assign")
    def plan_assign(rec_id: str, body: AssignRequest, p: Principal = Depends(principal)):
        need(p, "plan.approve")
        get_visible(rec_id, p)
        return safe_backend_call(plans_or_503().assign, rec_id, body.owner, p.id, set(p.permissions), body.version, body.note)

    @app.post("/api/plan/{rec_id}/comment")
    def plan_comment(rec_id: str, body: CommentRequest, p: Principal = Depends(principal)):
        need(p, "plan.review")
        get_visible(rec_id, p)
        return safe_backend_call(plans_or_503().comment, rec_id, p.id, set(p.permissions), body.note)

    @app.post("/api/chat")
    def chat(body: ChatRequest, p: Principal = Depends(principal)):
        need(p, "chat.ask")
        fresh = fresh_for(p)
        period, f = period_of(body.start, body.end, fresh), parse_filters(body.filters)
        ctx = AskContext(user=p.id, scope=scope_of(p), filters=f, ui_period=period, fresh=fresh,
                         synthetic=svc.backend.synthetic)
        answer = svc.assistant.ask(body.message, ctx, body.conversation_id)
        if answer["intent"] == "evidence" and not p.can("plan.read"):
            answer.update(status="restricted", text=["Your role cannot read the recommendation plan."], table=None, sources=[])
        answer["data_source"] = svc.source.public()
        answer["can_drilldown"] = p.can("analysis.drilldown")
        return answer

    @app.get("/api/admin/queries")
    def admin_queries(p: Principal = Depends(principal)):
        need(p, "admin.read")
        return {"items": list(svc.executor.recent)[-200:][::-1], "warehouse_queries": svc.executor.warehouse_queries}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main() -> None:
    import os
    import uvicorn
    logging.basicConfig(level=os.environ.get("SI_LOG_LEVEL", "INFO"))
    uvicorn.run(create_app(), host=os.environ.get("SI_HOST", "127.0.0.1"), port=int(os.environ.get("SI_PORT", "8780")))


if __name__ == "__main__":
    main()
