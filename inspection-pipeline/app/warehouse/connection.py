"""Snowflake connection factory (password, key-pair or external authenticator)."""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.errors import WarehouseConnectionError, WarehouseError
from app.logger import redact
from app.retry import retry_sync

logger = logging.getLogger(__name__)


def _load_private_key(settings: Settings) -> bytes:
    from cryptography.hazmat.primitives import serialization

    assert settings.snowflake_private_key_path is not None
    passphrase = settings.snowflake_private_key_passphrase
    key = serialization.load_pem_private_key(
        settings.snowflake_private_key_path.read_bytes(),
        password=passphrase.get_secret_value().encode() if passphrase else None,
    )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def connect(settings: Settings, run_id: str) -> Any:
    """Open one connection for the whole run (reused for every statement)."""
    settings.require_snowflake()
    import snowflake.connector
    from snowflake.connector.errors import DatabaseError, OperationalError

    kwargs: dict[str, Any] = {
        "account": settings.snowflake_account,
        "user": settings.snowflake_user,
        "warehouse": settings.snowflake_warehouse,
        "database": settings.snowflake_database,
        "schema": settings.snowflake_schema,
        "login_timeout": settings.snowflake_login_timeout_seconds,
        "autocommit": True,
        "session_parameters": {"QUERY_TAG": f"inspection-pipeline:{run_id}", "TIMEZONE": "UTC"},
    }
    if settings.snowflake_role:
        kwargs["role"] = settings.snowflake_role
    if settings.snowflake_authenticator:
        kwargs["authenticator"] = settings.snowflake_authenticator
    if settings.snowflake_private_key_path:
        kwargs["private_key"] = _load_private_key(settings)
    elif settings.snowflake_password:
        kwargs["password"] = settings.snowflake_password.get_secret_value()

    def attempt() -> Any:
        try:
            return snowflake.connector.connect(**kwargs)
        except OperationalError as exc:  # network / availability
            raise WarehouseConnectionError(f"Snowflake unreachable: {redact(str(exc))}") from exc
        except DatabaseError as exc:  # authentication / configuration: do not retry
            raise WarehouseError(f"Snowflake connection refused: {redact(str(exc))}") from exc

    conn = retry_sync(
        attempt,
        attempts=settings.snowflake_connect_attempts,
        base_delay=settings.retry_base_delay_seconds,
        description="Snowflake connect",
    )
    logger.info("Connected to Snowflake %s.%s", settings.snowflake_database, settings.snowflake_schema)
    return conn
