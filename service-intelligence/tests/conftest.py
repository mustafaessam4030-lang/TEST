"""Test fixtures. All data here is SYNTHETIC; no test touches Snowflake unless RUN_SNOWFLAKE_TESTS=1."""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from si.api import create_app
from si.backends.fixture import FixtureBackend
from si.config import ROOT, Settings
from si.mapping import load_mapping
from si.query import Period
from si.service import ServiceIntel

VIEW = "SI_SYNTHETIC.SERVICE_INTEL.V_SERVICE_REQUESTS"
AS_OF = datetime(2026, 3, 31, 12, 0)
MARCH = Period(date(2026, 3, 1), date(2026, 4, 1))
TOKENS = {"admin": "admin-token", "viewer": "viewer-token", "analyst": "analyst-token",
          "approver": "approver-token", "north": "north-token"}


def _row(tid, opened, resolved, target, status, prio, cat, region, team="Field Team A", modified=None):
    modified = modified or (resolved or opened)
    return (tid, opened, resolved, target, modified, AS_OF, status, prio, cat, "Repair", team, region,
            "Customer X", "free text")


D = datetime
# Hand-built rows with hand-computed expectations (see test_kpis.py). SYNTHETIC.
KNOWN_ROWS = [
    _row("K1", D(2026, 3, 2, 8), D(2026, 3, 2, 18), D(2026, 3, 3, 8), "Closed", "P1", "Engine", "North"),
    _row("K2", D(2026, 3, 5), D(2026, 3, 7), D(2026, 3, 6), "Resolved", "P2", "Engine", "South", team="Field Team C"),
    _row("K3", D(2026, 3, 10), D(2026, 3, 10, 4), None, "Closed", "P3", "Hydraulics", "North"),
    _row("K4", D(2026, 2, 20), D(2026, 3, 1), D(2026, 2, 25), "Closed", "P4", "Hydraulics", None),
    _row("K5", D(2026, 3, 15), None, D(2026, 3, 16), "New", "P1", "Engine", "North"),
    _row("K6", D(2026, 1, 1), None, None, "In Progress", "P2", "Electrical", "South", team="Field Team C"),
    _row("K7", D(2026, 3, 20), None, None, "Cancelled", "P3", "Engine", "North"),
    _row("K8", D(2026, 3, 25), D(2026, 3, 24), None, "Closed", "P2", "Engine", "North"),
    _row("K9", D(2026, 3, 28), None, None, "Pending Review", None, "Cooling", "Delta"),
    # stale duplicate of K2 from an older extract: removed by the mapping's dedupe rule
    _row("K2", D(2026, 3, 5), None, D(2026, 3, 6), "In Progress", "P2", "Engine", "South", modified=D(2026, 3, 5, 1)),
    # 23:30 UTC on 28 Feb is 01:30 on 1 March in Africa/Cairo (UTC+2)
    _row("K12", D(2026, 2, 28, 23, 30), D(2026, 3, 1, 5, 30), None, "Closed", "P3", "Cooling", "West"),
]


@pytest.fixture(scope="session")
def mapping():
    return load_mapping(ROOT / "config" / "source_mapping.synthetic.yaml")


def make_settings(tmp: Path, **kw) -> Settings:
    base = dict(env="test", backend="fixture", contract_view=VIEW,
                mapping_path=ROOT / "config" / "source_mapping.synthetic.yaml",
                metrics_path=ROOT / "config" / "metrics.yaml", users_path=tmp / "users.yaml",
                data_dir=tmp / "data", auth_mode="token", cache_ttl_s=300)
    base.update(kw)
    return Settings(**base)


def write_users(tmp: Path) -> Path:
    def sha(t):
        return hashlib.sha256(t.encode()).hexdigest()
    text = f"""
roles:
  viewer:   [analysis.read, chat.ask, plan.read]
  analyst:  [analysis.read, analysis.drilldown, chat.ask, plan.read, plan.add, plan.review]
  approver: [analysis.read, analysis.drilldown, chat.ask, plan.read, plan.add, plan.review, plan.approve]
  admin:    [analysis.read, analysis.drilldown, chat.ask, plan.read, plan.add, plan.review, plan.approve, admin.read]
group_roles: {{si-analysts: analyst}}
users:
  - {{id: admin.user, roles: [admin], token_sha256: {sha(TOKENS['admin'])}}}
  - {{id: viewer.user, roles: [viewer], token_sha256: {sha(TOKENS['viewer'])}}}
  - {{id: analyst.user, roles: [analyst], token_sha256: {sha(TOKENS['analyst'])}}}
  - {{id: approver.user, roles: [approver], token_sha256: {sha(TOKENS['approver'])}}}
  - {{id: north.analyst, roles: [analyst], token_sha256: {sha(TOKENS['north'])}, scope: {{region: [North]}}}}
"""
    path = tmp / "users.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def build(tmp: Path, rows=None, tz="UTC", **kw) -> ServiceIntel:
    write_users(tmp)
    settings = make_settings(tmp, reporting_tz=tz, **kw)
    mapping_ = load_mapping(settings.mapping_path)
    backend = FixtureBackend(mapping_, VIEW, rows=rows)
    return ServiceIntel(settings, backend=backend)


@pytest.fixture
def known(tmp_path):
    return build(tmp_path, KNOWN_ROWS)


@pytest.fixture(scope="session")
def synthetic(tmp_path_factory):
    """The full generated SYNTHETIC dataset (about 5,700 requests)."""
    return build(tmp_path_factory.mktemp("synthetic"))


@pytest.fixture
def client_for(tmp_path):
    def make(svc: ServiceIntel, who: str = "admin"):
        c = TestClient(create_app(svc.settings, svc))
        c.headers["Authorization"] = f"Bearer {TOKENS[who]}"
        return c
    return make
