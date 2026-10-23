"""Settings, read once from the environment.

Every Snowflake value comes from the environment (or a secrets mount the
environment points at); nothing is hard-coded and nothing reaches the browser.
Variable names follow the conventions already used in this repository's other
Snowflake projects (SNOWFLAKE_ACCOUNT, SNOWFLAKE_PRIVATE_KEY_PATH, ...).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENTS = ("dev", "test", "prod")
BACKENDS = ("fixture", "snowflake")
AUTH_MODES = ("dev", "token", "trusted_header")


class ConfigError(ValueError):
    pass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _int(name: str, default: int, lo: int, hi: int) -> int:
    raw = _env(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return value


@dataclass(frozen=True)
class SnowflakeSettings:
    account: str | None = None
    user: str | None = None
    role: str | None = None              # read-only analytics role
    writer_role: str | None = None       # role that may write SI_APP plan tables only
    warehouse: str | None = None
    authenticator: str | None = None     # SNOWFLAKE_JWT (key pair), externalbrowser, oauth, ...
    private_key_path: str | None = None
    private_key_passphrase: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    oauth_token_path: str | None = None
    app_schema: str | None = None        # fully qualified, e.g. SI_DB.SI_APP

    @property
    def configured(self) -> bool:
        return bool(self.account and self.user and self.role and self.warehouse)


@dataclass(frozen=True)
class Settings:
    env: str = "dev"
    backend: str = "fixture"
    contract_view: str = "SI_SYNTHETIC.SERVICE_INTEL.V_SERVICE_REQUESTS"
    mapping_path: Path = ROOT / "config" / "source_mapping.synthetic.yaml"
    metrics_path: Path = ROOT / "config" / "metrics.yaml"
    users_path: Path = ROOT / "config" / "users.example.yaml"
    data_dir: Path = ROOT / "data"
    reporting_tz: str = "UTC"
    auth_mode: str = "dev"
    trusted_user_header: str = "X-Forwarded-User"
    trusted_groups_header: str = "X-Forwarded-Groups"
    query_timeout_s: int = 60
    max_rows: int = 5000
    drilldown_page: int = 100
    cache_ttl_s: int = 300
    cortex_enabled: bool = False
    cortex_model: str = "mistral-large2"
    snowflake: SnowflakeSettings = field(default_factory=SnowflakeSettings)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.reporting_tz)

    @property
    def synthetic(self) -> bool:
        return self.backend == "fixture"


def load_settings() -> Settings:
    env = _env("SI_ENV", "dev")
    if env not in ENVIRONMENTS:
        raise ConfigError(f"SI_ENV must be one of {ENVIRONMENTS}")
    backend = _env("SI_BACKEND", "fixture")
    if backend not in BACKENDS:
        raise ConfigError(f"SI_BACKEND must be one of {BACKENDS}")
    if env == "prod" and backend == "fixture":
        raise ConfigError("SI_ENV=prod cannot run on synthetic fixtures; set SI_BACKEND=snowflake")
    auth_mode = _env("SI_AUTH_MODE", "dev" if env == "dev" else "token")
    if auth_mode not in AUTH_MODES:
        raise ConfigError(f"SI_AUTH_MODE must be one of {AUTH_MODES}")
    if auth_mode == "dev" and env != "dev":
        raise ConfigError("SI_AUTH_MODE=dev is only allowed with SI_ENV=dev")
    tz = _env("SI_REPORTING_TZ", "UTC")
    if tz not in available_timezones():
        raise ConfigError("SI_REPORTING_TZ must be an IANA time zone name")

    default_view = "SI_SYNTHETIC.SERVICE_INTEL.V_SERVICE_REQUESTS"
    contract_view = _env("SI_CONTRACT_VIEW", default_view if backend == "fixture" else None)
    if not contract_view:
        raise ConfigError("SI_CONTRACT_VIEW (fully qualified DB.SCHEMA.VIEW) is required with SI_BACKEND=snowflake")
    if contract_view.count(".") != 2:
        raise ConfigError("SI_CONTRACT_VIEW must be fully qualified as DATABASE.SCHEMA.VIEW")

    sf = SnowflakeSettings(
        account=_env("SNOWFLAKE_ACCOUNT"),
        user=_env("SNOWFLAKE_USER"),
        role=_env("SNOWFLAKE_ROLE"),
        writer_role=_env("SI_SNOWFLAKE_WRITER_ROLE"),
        warehouse=_env("SNOWFLAKE_WAREHOUSE"),
        authenticator=_env("SNOWFLAKE_AUTHENTICATOR"),
        private_key_path=_env("SNOWFLAKE_PRIVATE_KEY_PATH"),
        private_key_passphrase=_env("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
        password=_env("SNOWFLAKE_PASSWORD"),
        oauth_token_path=_env("SNOWFLAKE_OAUTH_TOKEN_PATH"),
        app_schema=_env("SI_APP_SCHEMA"),
    )
    if backend == "snowflake" and not sf.configured:
        raise ConfigError("SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_ROLE and SNOWFLAKE_WAREHOUSE are required")
    if backend == "snowflake" and env == "prod" and sf.password:
        raise ConfigError("Password authentication is not allowed in prod; use key pair or OAuth")

    mapping_default = ROOT / "config" / ("source_mapping.synthetic.yaml" if backend == "fixture" else "source_mapping.yaml")
    return Settings(
        env=env,
        backend=backend,
        contract_view=contract_view.upper(),
        mapping_path=Path(_env("SI_MAPPING_PATH", str(mapping_default))),
        metrics_path=Path(_env("SI_METRICS_PATH", str(ROOT / "config" / "metrics.yaml"))),
        users_path=Path(_env("SI_USERS_PATH", str(ROOT / "config" / "users.example.yaml"))),
        data_dir=Path(_env("SI_DATA_DIR", str(ROOT / "data"))),
        reporting_tz=tz,
        auth_mode=auth_mode,
        trusted_user_header=_env("SI_TRUSTED_USER_HEADER", "X-Forwarded-User"),
        trusted_groups_header=_env("SI_TRUSTED_GROUPS_HEADER", "X-Forwarded-Groups"),
        query_timeout_s=_int("SI_QUERY_TIMEOUT_S", 60, 5, 600),
        max_rows=_int("SI_MAX_ROWS", 5000, 10, 50000),
        drilldown_page=_int("SI_DRILLDOWN_PAGE", 100, 10, 1000),
        cache_ttl_s=_int("SI_CACHE_TTL_S", 300, 0, 86400),
        cortex_enabled=_env("SI_CORTEX_ENABLED", "0") == "1",
        cortex_model=_env("SI_CORTEX_MODEL", "mistral-large2"),
        snowflake=sf,
    )
