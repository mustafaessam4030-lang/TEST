"""Configuration. Secrets are referenced, never inlined."""
from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]  # repo-level `maya/`


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAYA_", env_file=".env", extra="ignore")

    environment: str = "dev"
    log_level: str = "INFO"

    # Storage: "memory" for local/dev and tests, "snowflake" in production.
    repository: str = "memory"
    snowflake_account: str | None = None
    snowflake_user: str | None = None
    snowflake_role: str = "MAYA_APP"
    snowflake_warehouse: str = "MAYA_WH"
    snowflake_database: str = "MAYA_PROD"
    snowflake_schema: str = "CORE"
    snowflake_private_key_ref: str | None = None  # vault:// or file:// reference

    # Automation
    headless: bool = True
    browser_channel: str = "chromium"
    chromium_path: str | None = None        # explicit binary; blank = Playwright's own
    max_contexts: int = 4
    run_deadline_ms: int = 90_000
    step_timeout_ms: int = 25_000
    sync_timeout_ms: int = 30_000
    # When sign-in needs a person (MFA), a headed run can pause and keep the very
    # same browser session open while they complete it.
    allow_human_resume: bool = True
    human_wait_timeout_s: int = 600
    # First search against a source with no contract: learn it automatically
    # rather than making the operator run a capture script by hand.
    auto_capture: bool = True
    auto_capture_timeout_s: int = 600
    artifact_dir: str = "/var/maya/artifacts"

    # Policy files
    freshness_config: str = str(ROOT / "config" / "freshness.yaml")
    sources_dir: str = str(ROOT / "config" / "sources")

    # Guardrails
    per_actor_hourly_quota: int = 20
    allow_live_automation: bool = Field(
        default=False,
        description="Master switch. Off by default so nothing touches a partner site by accident.",
    )

    def load_yaml(self, path: str) -> dict[str, Any]:
        p = Path(path)
        if not p.exists():
            return {}
        return yaml.safe_load(p.read_text()) or {}

    def source_config(self, source_id: str) -> dict[str, Any]:
        return self.load_yaml(os.path.join(self.sources_dir, f"{source_id}.yaml"))

    def freshness_policy(self) -> dict[str, Any]:
        return self.load_yaml(self.freshness_config)


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()
