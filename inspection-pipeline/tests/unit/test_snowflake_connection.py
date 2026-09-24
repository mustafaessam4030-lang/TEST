import pytest
from snowflake.connector.errors import DatabaseError, OperationalError

from app.errors import ConfigurationError, WarehouseConnectionError, WarehouseError
from app.warehouse import connection
from tests.conftest import make_settings

SF = dict(snowflake_account="acct", snowflake_user="svc", snowflake_password="sf-secret-pw",
          snowflake_database="DB", snowflake_schema="RAW", snowflake_warehouse="WH", snowflake_connect_attempts=3)


def test_missing_settings_are_reported():
    with pytest.raises(ConfigurationError, match="SNOWFLAKE_ACCOUNT"):
        connection.connect(make_settings(), "run")


def test_network_errors_are_retried_then_raised(monkeypatch):
    calls = []

    def fake_connect(**kwargs):
        calls.append(kwargs)
        raise OperationalError(msg="Could not connect")

    monkeypatch.setattr("snowflake.connector.connect", fake_connect)
    with pytest.raises(WarehouseConnectionError):
        connection.connect(make_settings(**SF), "run-1")
    assert len(calls) == 3
    assert calls[0]["session_parameters"]["QUERY_TAG"] == "inspection-pipeline:run-1"


def test_auth_errors_are_not_retried_and_hide_password(monkeypatch):
    calls = []

    def fake_connect(**kwargs):
        calls.append(1)
        raise DatabaseError(msg=f"Incorrect username or password was specified: {kwargs['password']}")

    monkeypatch.setattr("snowflake.connector.connect", fake_connect)
    with pytest.raises(WarehouseError) as info:
        connection.connect(make_settings(**SF), "run")
    assert len(calls) == 1 and not isinstance(info.value, WarehouseConnectionError)
    assert "sf-secret-pw" not in str(info.value)
