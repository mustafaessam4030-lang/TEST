"""Exception hierarchy.

Every error carries a ``retryable`` flag so the retry helper and the pipeline
can decide, in one place, whether repeating an operation is safe.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for all pipeline errors."""

    retryable: bool = False


class ConfigurationError(PipelineError):
    """Missing or invalid configuration. Never retried."""


class AuthenticationError(PipelineError):
    """Credentials rejected or login flow failed. Never retried blindly."""


class AuthorizationError(PipelineError):
    """Authenticated user is not allowed to access the resource (HTTP 403)."""


class SessionExpiredError(AuthenticationError):
    """The session expired and could not be re-established."""


class SourceUnavailableError(PipelineError):
    """Timeout, connection failure or 5xx/429 from the source. Safe to retry."""

    retryable = True


class HttpStatusError(PipelineError):
    """Non-retryable HTTP error from the source (4xx other than 401/403/429)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class UnexpectedResponseError(PipelineError):
    """The response is not shaped as configured (wrong content type, missing path, ...)."""


class MalformedResponseError(UnexpectedResponseError):
    """The response body is not valid JSON."""


class PaginationError(PipelineError):
    """Pagination did not terminate correctly or a page could not be fetched."""


class PageStructureError(PipelineError):
    """An expected element was not found in the web UI (page structure changed)."""


class WarehouseError(PipelineError):
    """Snowflake connection or SQL failure."""


class WarehouseConnectionError(WarehouseError):
    """Snowflake could not be reached. Safe to retry."""

    retryable = True
