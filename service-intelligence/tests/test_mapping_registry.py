"""Source mapping validation, contract-view generation and metric registry rules."""
import copy
from datetime import datetime

import pytest
import yaml

from conftest import AS_OF, KNOWN_ROWS, MARCH, VIEW, make_settings, write_users
from si.backends.fixture import FixtureBackend
from si.config import ROOT
from si.mapping import MappingError, parse_mapping, view_ddl
from si.registry import RegistryError, load_registry
from si.service import ServiceIntel

BASE = yaml.safe_load((ROOT / "config" / "source_mapping.synthetic.yaml").read_text())


def variant(**changes):
    raw = copy.deepcopy(BASE)
    for path, value in changes.items():
        node = raw
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[k]
        if value is None and keys[-1] in node:
            del node[keys[-1]]
        else:
            node[keys[-1]] = value
    return raw


@pytest.mark.parametrize("changes,message", [
    ({"columns__request_id": None}, "required"),
    ({"columns__created_at": "OPENED_TS; DROP TABLE X"}, "plain identifier"),
    ({"columns__region": "CUSTOMER_NAME"}, "Sensitive"),
    ({"columns__nonsense": "X"}, "Unknown contract columns"),
    ({"columns__status_class": "X"}, "derived"),
    ({"source__table": "RAW.SERVICE_REQUESTS"}, "fully qualified"),
    ({"status_classes__CLOSED": ["New"]}, "mapped to both"),
    ({"status_classes__OPEN": []}, "at least one OPEN"),
    ({"status_classes__DONE": ["X"]}, "status_classes keys"),
    ({"sla_policy": {"basis": "priority", "targets_hours": {"P1": -1}}}, "positive"),
    ({"sla_policy": {"basis": "customer", "targets_hours": {"P1": 4}}}, "basis"),
])
def test_invalid_mappings_are_rejected(changes, message):
    with pytest.raises(MappingError, match=message):
        parse_mapping(variant(**changes))


def test_ddl_is_a_secure_view_with_literal_safe_status_values():
    m = parse_mapping(variant(status_classes__CANCELLED=["Can't fix"]))
    ddl = view_ddl(m, VIEW)
    assert ddl.startswith(f"CREATE OR REPLACE SECURE VIEW {VIEW}")
    assert "'CAN''T FIX'" in ddl
    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY TICKET_NO ORDER BY LAST_MODIFIED_TS DESC) = 1" in ddl
    assert "CUSTOMER_NAME" not in ddl


def test_missing_columns_make_metrics_unavailable_instead_of_invented(tmp_path):
    write_users(tmp_path)
    raw = variant(columns__due_at=None, columns__closed_at=None, columns__region=None)
    m = parse_mapping(raw)
    assert {"due_at", "closed_at", "region"}.isdisjoint(m.available)
    svc = ServiceIntel(make_settings(tmp_path), backend=FixtureBackend(m, VIEW, rows=KNOWN_ROWS), mapping=m)
    r = svc.analytics.metric("sla_compliance_rate", MARCH, {}, {}, AS_OF)
    assert r["available"] is False and "due_at" in r["reason"] and r["value"] is None
    assert svc.analytics.metric("requests_created", MARCH, {}, {}, AS_OF)["value"] == 7
    from si.query import QueryError
    with pytest.raises(QueryError):
        svc.analytics.breakdown("requests_created", "region", MARCH, {}, {}, AS_OF)


def test_sla_policy_derives_due_time_when_source_has_none(tmp_path):
    write_users(tmp_path)
    raw = variant(columns__due_at=None, sla_policy={"basis": "priority", "targets_hours": {"P1": 8, "P2": 24, "P3": 72, "P4": 168}})
    m = parse_mapping(raw)
    assert "due_at" in m.available and m.due_at_source == "SLA policy by priority"
    svc = ServiceIntel(make_settings(tmp_path), backend=FixtureBackend(m, VIEW, rows=KNOWN_ROWS), mapping=m)
    r = svc.analytics.metric("sla_compliance_rate", MARCH, {}, {}, AS_OF, compare=False)
    # K1 P1 10h > 8h breach; K2 P2 48h > 24h breach; K3 P3 4h met; K4 P4 216h > 168h breach; K12 P3 6h met
    assert r["n"] == 5 and r["value"] == pytest.approx(2 / 5)


def test_timezone_conversion_for_naive_local_source(tmp_path):
    write_users(tmp_path)
    m = parse_mapping(variant(source__timezone="Africa/Cairo"))
    svc = ServiceIntel(make_settings(tmp_path), backend=FixtureBackend(m, VIEW, rows=KNOWN_ROWS), mapping=m)
    d = svc.analytics.drilldown("requests_created", MARCH, {}, {}, datetime(2026, 4, 1), limit=50, offset=0)
    k1 = next(r for r in d["rows"] if r["request_id"] == "K1")
    assert k1["created_at"].startswith("2026-03-02T06:00")      # 08:00 Cairo is 06:00 UTC


def test_fixture_backend_refuses_a_non_synthetic_mapping():
    from si.backends.base import BackendError
    with pytest.raises(BackendError):
        FixtureBackend(parse_mapping(variant(source__synthetic=False)), VIEW, rows=[])


def test_registry_loads_and_rejects_bad_definitions(tmp_path):
    reg = load_registry(ROOT / "config" / "metrics.yaml")
    assert len(reg.metrics) == 12 and reg.param("aged_days") == 30
    bad = yaml.safe_load((ROOT / "config" / "metrics.yaml").read_text())
    bad["metrics"][0]["expression"] = "SUM(revenue)"
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(RegistryError, match="non-contract"):
        load_registry(p)
    bad["metrics"][0]["expression"] = "COUNT(DISTINCT request_id)"
    bad["metrics"][0]["filter"] = "due_at IS NOT NULL"
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(RegistryError, match="requires"):
        load_registry(p)


def test_prod_settings_refuse_unsafe_configuration(monkeypatch):
    from si.config import ConfigError, load_settings
    monkeypatch.setenv("SI_ENV", "prod")
    monkeypatch.setenv("SI_BACKEND", "fixture")
    with pytest.raises(ConfigError, match="synthetic"):
        load_settings()
    monkeypatch.setenv("SI_BACKEND", "snowflake")
    monkeypatch.setenv("SI_AUTH_MODE", "dev")
    with pytest.raises(ConfigError, match="dev"):
        load_settings()
    monkeypatch.setenv("SI_AUTH_MODE", "token")
    with pytest.raises(ConfigError, match="SI_CONTRACT_VIEW"):
        load_settings()
    monkeypatch.setenv("SI_CONTRACT_VIEW", "DB.SCHEMA.V_SERVICE_REQUESTS")
    with pytest.raises(ConfigError, match="SNOWFLAKE_ACCOUNT"):
        load_settings()
    for k, v in {"SNOWFLAKE_ACCOUNT": "acct", "SNOWFLAKE_USER": "u", "SNOWFLAKE_ROLE": "r",
                 "SNOWFLAKE_WAREHOUSE": "w", "SNOWFLAKE_PASSWORD": "p"}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ConfigError, match="Password"):
        load_settings()
    monkeypatch.delenv("SNOWFLAKE_PASSWORD")
    s = load_settings()
    assert s.backend == "snowflake" and s.auth_mode == "token"
