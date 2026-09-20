"""Structured JSON logging with secret redaction and run/trace correlation."""
from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

_ctx: ContextVar[dict[str, Any]] = ContextVar("log_ctx", default={})

_SECRET_KEYS = re.compile(r"(password|passwd|secret|token|api[_-]?key|cookie|authorization)", re.I)
_SECRET_VALUE = re.compile(r"(?i)(bearer\s+[a-z0-9._\-]+|sk-[a-z0-9\-_]{16,})")


def bind(**kwargs: Any) -> None:
    _ctx.set({**_ctx.get(), **{k: v for k, v in kwargs.items() if v is not None}})


def clear() -> None:
    _ctx.set({})


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("***", value)[:2000]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **_ctx.get(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(redact(extra))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)[-2000:]
        return json.dumps(redact(payload), ensure_ascii=False)


def setup(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def log(logger: logging.Logger, level: int, msg: str, **fields: Any) -> None:
    logger.log(level, msg, extra={"extra_fields": fields})
