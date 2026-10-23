"""Caching, de-duplication, timeouts, failure recovery, and Snowflake driver behaviour (mocked)."""
import threading
import time

import pytest

from conftest import AS_OF, MARCH, make_settings
from si.backends.base import BackendError
from si.backends.snowflake import SnowflakeBackend, classify
from si.config import SnowflakeSettings


def test_repeated_reads_hit_the_cache(known):
    spec = known.builder.value("requests_created", MARCH, {}, {}, AS_OF)
    first = known.executor.run(spec)
    count = known.executor.warehouse_queries
    second = known.executor.run(spec)
    assert second.cached and second.rows == first.rows and known.executor.warehouse_queries == count


def test_concurrent_identical_requests_share_one_query(known):
    class Slow:
        kind, synthetic = "fixture", True
        calls = 0

        def query(self, sql, params, **kw):
            Slow.calls += 1
            time.sleep(0.3)
            return known.backend.query(sql, params, **kw)
    known.executor.backend = Slow()
    known.executor.clear_cache()
    spec = known.builder.value("requests_closed", MARCH, {}, {}, AS_OF)
    results = []
    threads = [threading.Thread(target=lambda: results.append(known.executor.run(spec))) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert Slow.calls == 1 and len(results) == 5 and len({r.rows[0] for r in results}) == 1


def test_warehouse_failure_is_reported_then_recovers(known):
    known.executor.clear_cache()
    known.backend.fail_next = BackendError("warehouse_unavailable", "suspended")
    with pytest.raises(BackendError) as exc:
        known.analytics.metric("requests_created", MARCH, {}, {}, AS_OF, compare=False)
    assert exc.value.kind == "warehouse_unavailable"
    assert known.executor.recent[-1]["status"] == "error"
    assert known.analytics.metric("requests_created", MARCH, {}, {}, AS_OF, compare=False)["value"] == 7


def test_failures_are_not_cached(known):
    known.executor.clear_cache()
    spec = known.builder.value("open_backlog", None, {}, {}, AS_OF)
    known.backend.fail_next = BackendError("timeout", "slow")
    with pytest.raises(BackendError):
        known.executor.run(spec)
    assert known.executor.run(spec).cached is False


def test_fixture_timeout_interrupts_long_queries(known):
    heavy = "SELECT SUM(a.range * b.range) FROM range(200000) a, range(200000) b"     # DuckDB-native, ~4e10 rows
    started = time.monotonic()
    with pytest.raises(BackendError) as exc:
        known.backend._execute(heavy, (), timeout_s=1, max_rows=1, native=True)
    assert exc.value.kind == "timeout" and time.monotonic() - started < 10


def test_row_cap_truncates(known):
    r = known.backend.query("SELECT request_id FROM SI_SYNTHETIC.SERVICE_INTEL.V_SERVICE_REQUESTS LIMIT 10", (),
                            timeout_s=5, max_rows=3, tag="t")
    assert len(r.rows) == 3 and r.truncated


class FakeErr(Exception):
    def __init__(self, errno, msg="", sfqid="01ab"):
        super().__init__(msg)
        self.errno, self.msg, self.sfqid = errno, msg, sfqid


@pytest.mark.parametrize("errno,msg,kind", [
    (604, "SQL execution canceled", "timeout"),
    (630, "Statement reached its statement or warehouse timeout", "timeout"),
    (606, "No active warehouse selected in the current session.", "warehouse_unavailable"),
    (2003, "Object 'X' does not exist or not authorized.", "permission_denied"),
    (3001, "Insufficient privileges to operate on table", "permission_denied"),
    (250001, "Could not connect to Snowflake backend", "connection"),
    (1003, "SQL compilation error: syntax error", "query_failed"),
])
def test_snowflake_errors_are_classified(errno, msg, kind):
    err = classify(FakeErr(errno, msg))
    assert err.kind == kind and err.query_id == "01ab"
    assert "Snowflake" in str(err) or kind in ("timeout", "query_failed")


def _sf_settings(tmp_path, **sf):
    base = dict(account="acct", user="svc", role="SI_READER", warehouse="SI_WH", writer_role="SI_WRITER",
                app_schema="SI_DB.SI_APP")
    base.update(sf)
    return make_settings(tmp_path, backend="snowflake", contract_view="SI_DB.SERVICE_INTEL.V_SERVICE_REQUESTS",
                         snowflake=SnowflakeSettings(**base))


def test_snowflake_connection_uses_safe_session_settings(tmp_path, monkeypatch):
    import snowflake.connector
    seen = {}

    class Cur:
        description = [("METRIC_VALUE",), ("N",)]
        sfqid = "01b2-query"

        def execute(self, sql, params, timeout=None):
            seen.update(sql=sql, params=params, timeout=timeout)

        def fetchmany(self, n):
            return [(7, 7)]

        def close(self):
            pass

    class Con:
        def cursor(self):
            return Cur()

        def close(self):
            pass

    def fake_connect(**kw):
        seen["connect"] = kw
        return Con()
    monkeypatch.setattr(snowflake.connector, "connect", fake_connect)
    b = SnowflakeBackend(_sf_settings(tmp_path, authenticator="externalbrowser"))
    r = b.query("SELECT 1", ("x",), timeout_s=12, max_rows=10, tag="metric_value")
    kw = seen["connect"]
    assert kw["paramstyle"] == "qmark" and kw["role"] == "SI_READER"
    assert kw["session_parameters"]["TIMEZONE"] == "UTC"
    assert kw["session_parameters"]["QUERY_TAG"] == "service-intelligence"
    assert kw["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 60
    assert seen["timeout"] == 12 and seen["params"] == ("x",)
    assert r.query_id == "01b2-query" and r.columns == ["metric_value", "n"]
    b.write("SELECT 1", (), tag="plan_read")
    assert seen["connect"]["role"] == "SI_WRITER"


def test_snowflake_without_credentials_is_not_configured(tmp_path):
    b = SnowflakeBackend(_sf_settings(tmp_path))
    with pytest.raises(BackendError) as exc:
        b.query("SELECT 1", (), timeout_s=5, max_rows=1, tag="t")
    assert exc.value.kind == "not_configured"


def test_secrets_do_not_appear_in_settings_repr():
    s = SnowflakeSettings(account="a", user="u", password="hunter2", private_key_passphrase="pp")
    assert "hunter2" not in repr(s) and "pp'" not in repr(s)


def test_private_key_from_environment_text(tmp_path, monkeypatch):
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    seen = {}

    def fake_connect(**kw):
        seen.update(kw)
        raise RuntimeError("stop")
    monkeypatch.setattr(snowflake.connector, "connect", fake_connect)
    one_line = pem.replace("\n", "\\n")              # how a key often looks after pasting into a secret field
    b = SnowflakeBackend(_sf_settings(tmp_path, private_key_pem=one_line))
    with pytest.raises(BackendError):
        b.query("SELECT 1", (), timeout_s=5, max_rows=1, tag="t")
    assert seen["authenticator"] == "SNOWFLAKE_JWT" and isinstance(seen["private_key"], bytes)
    assert "BEGIN" not in repr(b.sf)
