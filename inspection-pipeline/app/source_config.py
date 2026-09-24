"""Source definition: *how* the website exposes inspection data.

Nothing in here is hard-coded: endpoints, JSON paths, labels and column
headers must be taken from the discovery report (``python -m app.discovery``)
and written to ``config/source.yaml``. Any value left as ``<FILL_ME>`` makes
the pipeline stop with a list of exactly what is still unknown.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import ConfigurationError

PLACEHOLDER = "<FILL_ME>"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldMapping(_Strict):
    """Canonical field -> location in a source record.

    For the API collector a location is a dotted JSON path (``data.sn``,
    ``items.0.id``). For the Playwright collector it is the exact column
    header text shown in the results table.
    """

    inspection_number: str
    serial_number: str
    status: str
    inspection_date: str | None = None
    summary: dict[str, str] = Field(default_factory=dict, description="counter name -> location")
    attachments: str | None = None
    attachment_fields: dict[str, str] = Field(
        default_factory=dict, description="API only: attachment attribute -> path inside each element"
    )


class ApiRequest(_Strict):
    method: Literal["GET", "POST"] = "GET"
    path: str
    params: dict[str, Any] = Field(default_factory=dict)
    json_body: dict[str, Any] | None = None
    headers: dict[str, str] = Field(default_factory=dict, description="Static, non-secret headers only.")
    filter_location: Literal["query", "body"] = "query"
    filter_params: dict[str, str | None] = Field(
        default_factory=dict, description="serial_number/inspection_number -> parameter name"
    )


class ApiPagination(_Strict):
    type: Literal["none", "page", "offset", "cursor"] = "none"
    location: Literal["query", "body"] = "query"
    page_param: str | None = None
    start_page: int = 1
    size_param: str | None = None
    page_size: int = 100
    offset_param: str | None = None
    limit_param: str | None = None
    cursor_param: str | None = None
    next_cursor_path: str | None = None
    total_path: str | None = None
    max_pages: int = 1000


class ApiSource(_Strict):
    request: ApiRequest
    records_path: str = Field(description='Dotted path to the list of records; "" if the body is the list.')
    pagination: ApiPagination = Field(default_factory=ApiPagination)
    fields: FieldMapping


class LoginConfig(_Strict):
    """Normal login form, located by accessible labels/roles."""

    login_path: str
    username_label: str
    password_label: str
    submit_button_name: str
    success_url_pattern: str | None = Field(None, description="Regex the URL matches after login.")
    success_text: str | None = Field(None, description="Text visible only after a successful login.")


class RoleLocator(_Strict):
    role: str
    name: str | None = None


class SearchConfig(_Strict):
    serial_number_label: str | None = None
    inspection_number_label: str | None = None
    submit_button_name: str | None = None


class BrowserSource(_Strict):
    inspections_path: str
    search: SearchConfig = Field(default_factory=SearchConfig)
    table: RoleLocator = Field(default_factory=lambda: RoleLocator(role="table"))
    next_page: RoleLocator | None = None
    max_pages: int = 500
    fields: FieldMapping


class SourceConfig(_Strict):
    auth: LoginConfig | None = None
    api: ApiSource | None = None
    browser: BrowserSource | None = None
    date_formats: list[str] = Field(default_factory=list, description="strptime formats for non-ISO dates")
    allowed_statuses: list[str] = Field(default_factory=list, description="Optional allow-list of status values")

    def require(self, collector_type: str, auth_mode: str) -> None:
        """Fail with a precise message if the sections needed for this run are incomplete."""
        problems: list[str] = []
        sections: dict[str, BaseModel | None] = {}
        if collector_type == "api":
            sections["api"] = self.api
        else:
            sections["browser"] = self.browser
        if auth_mode == "browser_session":
            sections["auth"] = self.auth
        for name, section in sections.items():
            if section is None:
                problems.append(f"section '{name}' is missing")
            else:
                problems.extend(f"{name}.{p}" for p in _find_placeholders(section.model_dump()))
        if problems:
            raise ConfigurationError(
                "Source configuration is incomplete (run `python -m app.discovery` to determine "
                "these values): " + "; ".join(problems)
            )


def _find_placeholders(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_find_placeholders(item, f"{prefix}{key}."))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_placeholders(item, f"{prefix}{index}."))
    elif isinstance(value, str) and PLACEHOLDER in value:
        found.append(prefix.rstrip(".") + " is <FILL_ME>")
    return found


def load_source_config(path: Path) -> SourceConfig:
    if not path.exists():
        raise ConfigurationError(
            f"Source configuration not found at {path}. Copy config/source.example.yaml and fill it in."
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return SourceConfig.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(f"Invalid source configuration {path}: {exc}") from exc
