"""Collector that calls the backend endpoint the web application itself uses.

The endpoint, parameters, pagination scheme and JSON paths all come from the
``api`` section of the source configuration (filled from the discovery report).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.auth.session import AuthContext, Authenticator
from app.collectors.base import CollectionResult, InspectionCollector
from app.config import Settings
from app.errors import (
    AuthorizationError,
    ConfigurationError,
    HttpStatusError,
    MalformedResponseError,
    PaginationError,
    PipelineError,
    SessionExpiredError,
    SourceUnavailableError,
    UnexpectedResponseError,
)
from app.models import SourceRecord
from app.retry import retry_async
from app.services.normalization import MISSING, get_path
from app.source_config import ApiSource, LoginConfig

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 120.0


class _Unauthorized(PipelineError):
    """Internal: HTTP 401 or redirect to the login page."""


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value and value.strip().isdigit():
        return min(float(value), MAX_RETRY_AFTER_SECONDS)
    return None


class ApiInspectionCollector(InspectionCollector):
    collector_type = "api"
    lookup_mode = "path"

    def __init__(
        self,
        settings: Settings,
        source: ApiSource,
        authenticator: Authenticator,
        login: LoginConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.source = source
        self.field_mapping = source.fields
        self.authenticator = authenticator
        self.login = login
        self._transport = transport
        self._sleep = sleep

    # ------------------------------------------------------------------ public
    async def collect(
        self, serial_number: str | None = None, inspection_number: str | None = None
    ) -> CollectionResult:
        filters = self._filters(serial_number, inspection_number)
        auth = await self.authenticator.authenticate()
        async with self._client(auth) as client:
            return await self._paginate(client, filters)

    # ----------------------------------------------------------------- helpers
    def _filters(self, serial_number: str | None, inspection_number: str | None) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, value in (("serial_number", serial_number), ("inspection_number", inspection_number)):
            if value is None:
                continue
            param = self.source.request.filter_params.get(name)
            if not param:
                raise ConfigurationError(
                    f"Filtering by {name} requested but api.request.filter_params.{name} is not configured"
                )
            out[param] = value
        return out

    def _client(self, auth: AuthContext) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            base_url=self.settings.require_base_url(),
            timeout=self.settings.http_timeout_seconds,
            follow_redirects=True,
            transport=self._transport,
            headers={"Accept": "application/json", **self.source.request.headers},
        )
        self._apply_auth(client, auth)
        return client

    @staticmethod
    def _apply_auth(client: httpx.AsyncClient, auth: AuthContext) -> None:
        client.cookies.clear()
        for cookie in auth.cookies:
            client.cookies.set(
                cookie["name"], cookie["value"], domain=cookie.get("domain", ""), path=cookie.get("path", "/")
            )
        client.headers.update(auth.headers)

    def _build_request(self, filters: dict[str, str], page_state: dict[str, Any]) -> dict[str, Any]:
        req = self.source.request
        params: dict[str, Any] = dict(req.params)
        body: dict[str, Any] | None = copy.deepcopy(req.json_body) if (req.json_body or req.method == "POST") else None
        if body is None and (req.filter_location == "body" or self.source.pagination.location == "body"):
            body = {}
        (body if req.filter_location == "body" else params).update(filters)
        (body if self.source.pagination.location == "body" else params).update(page_state)
        return {"method": req.method, "url": req.path, "params": params or None, "json": body}

    async def _send(self, client: httpx.AsyncClient, request: dict[str, Any]) -> Any:
        try:
            response = await client.request(**request)
        except httpx.TimeoutException as exc:
            raise SourceUnavailableError(f"Timeout calling {request['url']}") from exc
        except httpx.TransportError as exc:
            raise SourceUnavailableError(f"Network error calling {request['url']}: {type(exc).__name__}") from exc

        if response.status_code == 401 or self._redirected_to_login(response):
            raise _Unauthorized(f"Session rejected by {request['url']} (HTTP {response.status_code})")
        if response.status_code == 403:
            raise AuthorizationError(f"Access to {request['url']} is forbidden for this account (HTTP 403)")
        if response.status_code in RETRYABLE_STATUS:
            err = SourceUnavailableError(f"HTTP {response.status_code} from {request['url']}")
            err.retry_after = _retry_after(response)  # type: ignore[attr-defined]
            raise err
        if response.status_code >= 400:
            raise HttpStatusError(f"HTTP {response.status_code} from {request['url']}", response.status_code)

        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            raise UnexpectedResponseError(
                f"Expected JSON from {request['url']} but got content-type {content_type or 'none'!r}"
            )
        try:
            return response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MalformedResponseError(f"Malformed JSON from {request['url']}: {exc}") from exc

    def _redirected_to_login(self, response: httpx.Response) -> bool:
        return bool(
            self.login and response.history and response.url.path.rstrip("/") == self.login.login_path.rstrip("/")
        )

    async def _fetch(self, client: httpx.AsyncClient, request: dict[str, Any]) -> Any:
        """Fetch with bounded retries; on an expired session re-authenticate exactly once."""

        async def attempt() -> Any:
            return await retry_async(
                lambda: self._send(client, request),
                attempts=self.settings.http_max_attempts,
                base_delay=self.settings.retry_base_delay_seconds,
                description=f"{request['method']} {request['url']}",
                sleep=self._sleep,
            )

        try:
            return await attempt()
        except _Unauthorized:
            logger.warning("Session rejected; re-authenticating once")
        self._apply_auth(client, await self.authenticator.authenticate())
        try:
            return await attempt()
        except _Unauthorized as exc:
            raise SessionExpiredError("Session rejected again after re-authentication") from exc

    def _records(self, body: Any, page_label: str) -> list[Any]:
        records = get_path(body, self.source.records_path)
        if records is MISSING:
            raise UnexpectedResponseError(f"records_path {self.source.records_path!r} not found in response ({page_label})")
        if not isinstance(records, list):
            raise UnexpectedResponseError(
                f"records_path {self.source.records_path!r} is {type(records).__name__}, expected a list ({page_label})"
            )
        return records

    async def _paginate(self, client: httpx.AsyncClient, filters: dict[str, str]) -> CollectionResult:
        pg = self.source.pagination
        records: list[SourceRecord] = []
        expected_total: int | None = None
        cursor: Any = None
        seen_cursors: set[str] = set()
        page_index = 0

        while True:
            if page_index >= pg.max_pages:
                raise PaginationError(f"Stopped after max_pages={pg.max_pages}; pagination did not terminate")
            state = self._page_state(page_index, cursor)
            label = f"page={page_index + 1}"
            try:
                body = await self._fetch(client, self._build_request(filters, state))
                page_records = self._records(body, label)
            except (SourceUnavailableError, UnexpectedResponseError, HttpStatusError) as exc:
                if page_index == 0:
                    raise
                raise PaginationError(f"Failed to fetch {label}: {exc}") from exc

            if page_index == 0 and pg.total_path:
                total = get_path(body, pg.total_path)
                if isinstance(total, int) or (isinstance(total, str) and total.isdigit()):
                    expected_total = int(total)
            for i, item in enumerate(page_records):
                raw = item if isinstance(item, dict) else {"value": item}
                records.append(SourceRecord(raw=raw, locator=f"{label},index={i}"))
            page_index += 1
            logger.info("Fetched %s with %d records", label, len(page_records))

            if pg.type == "none":
                break
            if pg.type == "cursor":
                cursor = get_path(body, pg.next_cursor_path or "")
                if cursor is MISSING or cursor in (None, ""):
                    break
                key = json.dumps(cursor, sort_keys=True, default=str)
                if key in seen_cursors:
                    raise PaginationError(f"Cursor repeated at {label}; aborting to avoid an infinite loop")
                seen_cursors.add(key)
                continue
            # page / offset
            if not page_records or len(page_records) < pg.page_size:
                break
            if expected_total is not None and len(records) >= expected_total:
                break

        return CollectionResult(records=records, pages_fetched=page_index, expected_total=expected_total)

    def _page_state(self, page_index: int, cursor: Any) -> dict[str, Any]:
        pg = self.source.pagination
        if pg.type == "page":
            if not pg.page_param:
                raise ConfigurationError("pagination.type=page requires pagination.page_param")
            state: dict[str, Any] = {pg.page_param: pg.start_page + page_index}
            if pg.size_param:
                state[pg.size_param] = pg.page_size
            return state
        if pg.type == "offset":
            if not pg.offset_param:
                raise ConfigurationError("pagination.type=offset requires pagination.offset_param")
            state = {pg.offset_param: page_index * pg.page_size}
            if pg.limit_param:
                state[pg.limit_param] = pg.page_size
            return state
        if pg.type == "cursor":
            if not (pg.cursor_param and pg.next_cursor_path):
                raise ConfigurationError("pagination.type=cursor requires cursor_param and next_cursor_path")
            return {pg.cursor_param: cursor} if cursor is not None else {}
        return {}
