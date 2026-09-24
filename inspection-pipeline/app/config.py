"""Runtime settings, read from environment variables (and an optional .env file).

Secrets are ``SecretStr`` so they never appear in ``repr()``/logs, and each
secret is registered with the log redactor as soon as settings are loaded.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.errors import ConfigurationError
from app.logger import register_secret


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Source -------------------------------------------------------------
    source_name: str = Field("website", description="Value written to the SOURCE column.")
    collector_type: Literal["api", "playwright"] = "api"
    source_config_path: Path = Path("config/source.yaml")
    source_timezone: str = Field("UTC", description="Timezone for source timestamps that carry no offset.")

    website_base_url: str | None = None
    website_username: str | None = None
    website_password: SecretStr | None = None
    website_api_token: SecretStr | None = None
    auth_mode: Literal["browser_session", "bearer_token", "none"] = "browser_session"

    http_timeout_seconds: float = 30.0
    http_max_attempts: int = Field(3, ge=1, le=10)
    retry_base_delay_seconds: float = 2.0

    browser_headless: bool = True
    browser_timeout_ms: int = 30_000
    diagnostics_dir: Path = Path("diagnostics")

    min_expected_records: int = Field(0, ge=0, description="Fewer records than this fails the run.")

    # --- Snowflake ------------------------------------------------------------
    snowflake_account: str | None = None
    snowflake_user: str | None = None
    snowflake_password: SecretStr | None = None
    snowflake_private_key_path: Path | None = None
    snowflake_private_key_passphrase: SecretStr | None = None
    snowflake_authenticator: str | None = None
    snowflake_database: str | None = None
    snowflake_schema: str | None = None
    snowflake_warehouse: str | None = None
    snowflake_role: str | None = None
    snowflake_login_timeout_seconds: int = 60
    snowflake_connect_attempts: int = Field(3, ge=1, le=10)
    snowflake_insert_batch_size: int = Field(500, ge=1, le=10_000)

    # --- Logging --------------------------------------------------------------
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"

    def model_post_init(self, __context: object) -> None:
        for secret in (
            self.website_password,
            self.website_api_token,
            self.snowflake_password,
            self.snowflake_private_key_passphrase,
        ):
            if secret is not None:
                register_secret(secret.get_secret_value())

    def require_base_url(self) -> str:
        if not self.website_base_url:
            raise ConfigurationError("WEBSITE_BASE_URL is not set")
        return self.website_base_url.rstrip("/")

    def require_snowflake(self) -> None:
        missing = [
            name.upper()
            for name in ("snowflake_account", "snowflake_user", "snowflake_database",
                         "snowflake_schema", "snowflake_warehouse")
            if not getattr(self, name)
        ]
        if not (self.snowflake_password or self.snowflake_private_key_path or self.snowflake_authenticator):
            missing.append("SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_PATH or SNOWFLAKE_AUTHENTICATOR")
        if missing:
            raise ConfigurationError("Missing Snowflake settings: " + ", ".join(missing))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
