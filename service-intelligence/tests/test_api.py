"""HTTP API: authentication, permissions, data scope, validation, and failure states."""
import json

import pytest
from fastapi.testclient import TestClient

from conftest import KNOWN_ROWS, TOKENS, build
from si.api import create_app
from si.backends.base import BackendError

MARCH = {"start": "2026-03-01", "end": "2026-03-31"}


@pytest.fixture
def svc(tmp_path):
    return build(tmp_path, KNOWN_ROWS)


def test_authentication_is_required(svc):
    c = TestClient(create_app(svc.settings, svc))
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/overview", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/api/me", headers={"Authorization": f"Bearer {TOKENS['viewer']}"})
    assert r.status_code == 200 and r.json()["id"] == "viewer.user"


def test_permissions_are_enforced_server_side(svc, client_for):
    viewer = client_for(svc, "viewer")
    assert viewer.get("/api/overview", params=MARCH).status_code == 200
    assert viewer.get("/api/drilldown", params={"metric": "open_backlog"}).status_code == 403
    assert viewer.post("/api/plan", json={"snapshot_id": "S-x", "key": "k:x"}).status_code == 403
    assert viewer.get("/api/admin/queries").status_code == 403
    assert client_for(svc, "admin").get("/api/admin/queries").status_code == 200


def test_overview_matches_registry_figures(svc, client_for):
    j = client_for(svc).get("/api/overview", params=MARCH).json()
    kpi = {k["metric"]["id"]: k for k in j["kpis"]}
    assert kpi["requests_created"]["value"] == 7 and kpi["open_backlog"]["value"] == 2
    assert j["period"]["label"] == "2026-03-01 to 2026-03-31"
    assert j["data_source"]["synthetic"] is True and "SYNTHETIC" in j["data_source"]["label"]
    assert j["freshness"]["as_of"].startswith("2026-03-31T12:00")


def test_dashboard_and_chat_agree_over_http(svc, client_for):
    c = client_for(svc)
    dash = c.get("/api/breakdown", params={**MARCH, "metric": "requests_created", "dimension": "region"}).json()
    chat = c.post("/api/chat", json={"message": "requests created by region", **MARCH}).json()
    assert {r["segment"]: r["value"] for r in chat["table"]["rows"]} == {r["segment"]: r["value"] for r in dash["rows"]}


def test_scoped_user_only_sees_their_region(svc, client_for):
    north = client_for(svc, "north")
    d = north.get("/api/drilldown", params={**MARCH, "metric": "requests_created", "limit": 50}).json()
    assert d["rows"] and {r["region"] for r in d["rows"]} == {"North"}
    b = north.get("/api/breakdown", params={**MARCH, "metric": "requests_created", "dimension": "region"}).json()
    assert [r["segment"] for r in b["rows"]] == ["North"]
    # asking for another region through a filter returns nothing rather than widening the scope
    south = north.get("/api/overview", params={**MARCH, "filters": json.dumps({"region": ["South"]})}).json()
    assert next(k for k in south["kpis"] if k["metric"]["id"] == "requests_created")["value"] == 0
    meta = north.get("/api/meta").json()
    region_values = next(d for d in meta["dimensions"] if d["id"] == "region")["values"]
    assert [v["value"] for v in region_values] == ["North"]
    chat = north.post("/api/chat", json={"message": "requests created by region", **MARCH}).json()
    assert [r["segment"] for r in chat["table"]["rows"]] == ["North"]


def test_scoped_user_cannot_save_unscoped_candidates(svc, client_for):
    admin, north = client_for(svc, "admin"), client_for(svc, "north")
    cands = admin.post("/api/recommendations/generate", json=MARCH).json()["candidates"]
    if cands:
        c = cands[0]
        r = north.post("/api/plan", json={"snapshot_id": c["snapshot"]["snapshot_id"], "key": c["key"]})
        assert r.status_code == 403


def test_plan_save_requires_a_server_generated_candidate(svc, client_for):
    c = client_for(svc, "analyst")
    r = c.post("/api/plan", json={"snapshot_id": "S-forged", "key": "volume_surge:category=Fake"})
    assert r.status_code == 410


def test_plan_end_to_end_over_http(tmp_path, client_for):
    from conftest import build
    svc = build(tmp_path)        # full synthetic data so detectors fire
    analyst, approver, viewer = client_for(svc, "analyst"), client_for(svc, "approver"), client_for(svc, "viewer")
    gen = analyst.post("/api/recommendations/generate", json={}).json()
    c = gen["candidates"][0]
    saved = analyst.post("/api/plan", json={"snapshot_id": c["snapshot"]["snapshot_id"], "key": c["key"], "note": "n"})
    assert saved.status_code == 200
    rid = saved.json()["rec_id"]
    assert analyst.post(f"/api/plan/{rid}/status", json={"to": "approved", "version": 1}).status_code == 403
    assert analyst.post(f"/api/plan/{rid}/status", json={"to": "reviewed", "version": 1}).status_code == 200
    assert approver.post(f"/api/plan/{rid}/status", json={"to": "approved", "version": 2}).status_code == 200
    assert viewer.post(f"/api/plan/{rid}/comment", json={"note": "hi"}).status_code == 403
    item = viewer.get(f"/api/plan/{rid}").json()
    assert item["status"] == "approved" and len(item["history"]) == 3
    again = analyst.post("/api/recommendations/generate", json={}).json()
    assert next(x for x in again["candidates"] if x["key"] == c["key"])["in_plan"]["rec_id"] == rid


@pytest.mark.parametrize("params", [
    {"filters": "{not json"},
    {"filters": json.dumps({"customer_name": ["x"]})},
    {"filters": json.dumps({"region": ["x" * 500]})},
    {"start": "2026-03-01"},
    {"start": "2026-03-10", "end": "2026-03-01"},
])
def test_invalid_requests_are_rejected(svc, client_for, params):
    assert client_for(svc).get("/api/overview", params=params).status_code == 400


def test_unknown_metric_and_dimension(svc, client_for):
    c = client_for(svc)
    assert c.get("/api/breakdown", params={"metric": "revenue", "dimension": "region"}).status_code == 400
    assert c.get("/api/breakdown", params={"metric": "open_backlog", "dimension": "customer"}).status_code == 400


def test_warehouse_outage_returns_503_and_no_figures(svc, client_for):
    c = client_for(svc)
    svc.executor.clear_cache()
    svc.backend.fail_next = BackendError("warehouse_unavailable", "The Snowflake warehouse is unavailable or suspended.")
    r = c.get("/api/overview", params=MARCH)
    assert r.status_code == 503 and r.json()["error"] == "warehouse_unavailable" and "kpis" not in r.json()
    assert c.get("/api/overview", params=MARCH).status_code == 200


def test_security_headers_and_no_store(svc, client_for):
    r = client_for(svc).get("/api/me")
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    page = TestClient(create_app(svc.settings, svc)).get("/")
    assert page.status_code == 200 and "Service Intelligence" in page.text
    assert "SNOWFLAKE" not in page.text.upper().replace("SNOWFLAKE CORTEX", "")


def test_trusted_header_mode(tmp_path):
    svc = build(tmp_path, KNOWN_ROWS, auth_mode="trusted_header")
    c = TestClient(create_app(svc.settings, svc))
    assert c.get("/api/me").status_code == 401
    me = c.get("/api/me", headers={"X-Forwarded-User": "jane.doe", "X-Forwarded-Groups": "si-analysts,other"}).json()
    assert me["roles"] == ["analyst"] and "plan.approve" not in me["permissions"]
    nobody = c.get("/api/overview", headers={"X-Forwarded-User": "no.groups"})
    assert nobody.status_code == 403


def test_query_log_records_ids_without_bind_values(svc, client_for):
    c = client_for(svc)
    c.get("/api/overview", params={**MARCH, "filters": json.dumps({"region": ["North"]})})
    items = c.get("/api/admin/queries").json()["items"]
    assert items and all("query_id" in i or i["status"] in ("refused",) for i in items)
    assert "North" not in json.dumps(items)
    log = (svc.settings.data_dir / "query_log.jsonl").read_text()
    assert "North" not in log and "token" not in log.lower()
