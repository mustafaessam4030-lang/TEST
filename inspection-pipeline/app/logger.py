"""Structured logging with run_id correlation and secret redaction.

* ``run_id`` is held in a context variable and stamped on every log record.
* Every record passes through a redaction filter that masks registered secret
  values (passwords, tokens) and common credential patterns, so a secret
  cannot reach the logs even if it ends up inside an exception message.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone

_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")
_secrets: set[str] = set()

REDACTED = "***REDACTED***"

_PATTERNS = [
    # Authorization: Bearer xxx / Basic xxx
    # (a credential contains a digit or symbol, which keeps prose like "basic information" intact)
    (re.compile(r"(?i)\b(bearer|basic)\s+(?=[A-Za-z0-9\-._~+/=]*[0-9\-._~+/=])[A-Za-z0-9\-._~+/=]{8,}"),
     r"\1 " + REDACTED),
    # key=value or "key": "value" for sensitive keys
    (
        re.compile(
            r"(?i)(\"?(?:password|passwd|pwd|secret|token|access_token|refresh_token|id_token|"
            r"api[_-]?key|authorization|cookie|set-cookie|session(?:id)?|sid|jsessionid)\"?"
            r"\s*[:=]\s*)(\"[^\"]*\"|[^\s&,;]+)"
        ),
        r"\1" + REDACTED,
    ),
]


def register_secret(value: str | None) -> None:
    """Mask this exact value anywhere it appears in logs or error messages."""
    if value and len(value) >= 4:
        _secrets.add(value)


def redact(text: str) -> str:
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def set_run_id(run_id: str) -> contextvars.Token[str]:
    return _run_id.set(run_id)


def get_run_id() -> str:
    return _run_id.get()


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id.get()
        message = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        record.msg = redact(message)
        record.args = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "run_id": getattr(record, "run_id", "-"),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_text:
            payload["exception"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ContextFilter())
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-5s run_id=%(run_id)s %(name)s %(message)s")
        )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Third-party loggers can print full URLs/headers at INFO/DEBUG.
    for noisy in ("httpx", "httpcore", "snowflake", "snowflake.connector", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
