"""Canonical error taxonomy. Every failure in the platform maps to one of these."""
from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    INVALID_SERIAL = "INVALID_SERIAL"
    SERIAL_NOT_FOUND = "SERIAL_NOT_FOUND"
    LOGIN_FAILED = "LOGIN_FAILED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    MFA_REQUIRED = "MFA_REQUIRED"
    CAPTCHA_DETECTED = "CAPTCHA_DETECTED"
    SEARCH_FAILED = "SEARCH_FAILED"
    WEBSITE_CHANGED = "WEBSITE_CHANGED"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    EXTRACTION_ERROR = "EXTRACTION_ERROR"
    INVALID_DATA = "INVALID_DATA"
    DATABASE_ERROR = "DATABASE_ERROR"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# code -> (http status, retryable, max attempts including the first)
_POLICY: dict[ErrorCode, tuple[int, bool, int]] = {
    ErrorCode.INVALID_SERIAL: (400, False, 1),
    ErrorCode.SERIAL_NOT_FOUND: (404, False, 1),
    ErrorCode.LOGIN_FAILED: (502, False, 1),
    ErrorCode.SESSION_EXPIRED: (502, True, 2),
    ErrorCode.MFA_REQUIRED: (423, False, 1),      # Locked: a human must act
    ErrorCode.CAPTCHA_DETECTED: (503, False, 1),
    ErrorCode.SEARCH_FAILED: (502, True, 2),
    ErrorCode.WEBSITE_CHANGED: (502, False, 1),
    ErrorCode.TIMEOUT: (504, True, 3),
    ErrorCode.NETWORK_ERROR: (503, True, 3),
    ErrorCode.RATE_LIMITED: (429, True, 2),
    ErrorCode.EXTRACTION_ERROR: (502, True, 2),
    ErrorCode.INVALID_DATA: (422, False, 1),
    ErrorCode.DATABASE_ERROR: (503, True, 3),
    # The data was read correctly and could not be stored. Not retryable in
    # the browser — replaying the whole lookup will not fix a full disk — and
    # never reported to Maia as a success.
    ErrorCode.PERSISTENCE_FAILED: (500, False, 1),
    ErrorCode.CIRCUIT_OPEN: (503, False, 1),
    ErrorCode.INTERNAL_ERROR: (500, False, 1),
}

# Short, factual hints. Maia rephrases them for the user; she never invents detail.
USER_HINT: dict[ErrorCode, str] = {
    ErrorCode.INVALID_SERIAL: "The serial number format is not valid.",
    ErrorCode.SERIAL_NOT_FOUND: "The source has no record for this serial number.",
    ErrorCode.LOGIN_FAILED: "Automation could not sign in to the source system.",
    ErrorCode.SESSION_EXPIRED: "The source session expired and could not be renewed.",
    ErrorCode.MFA_REQUIRED: "Sign-in needs human verification (MFA) at the source.",
    ErrorCode.CAPTCHA_DETECTED: "The source presented a security challenge that needs a human.",
    ErrorCode.SEARCH_FAILED: "The search could not be completed at the source.",
    ErrorCode.WEBSITE_CHANGED: "The source page structure changed; engineering has been alerted.",
    ErrorCode.TIMEOUT: "The source did not respond in time.",
    ErrorCode.NETWORK_ERROR: "The source could not be reached.",
    ErrorCode.RATE_LIMITED: "Too many lookups in a short period.",
    ErrorCode.EXTRACTION_ERROR: "The record was reached but could not be read completely.",
    ErrorCode.INVALID_DATA: "The retrieved data failed quality validation and was not stored.",
    ErrorCode.DATABASE_ERROR: "The data store is unavailable.",
    ErrorCode.PERSISTENCE_FAILED: (
        "The data was retrieved from the source but could not be saved, "
        "so it is not being reported as a completed lookup."),
    ErrorCode.CIRCUIT_OPEN: "Lookups for this source are paused after repeated failures.",
    ErrorCode.INTERNAL_ERROR: "An internal error occurred.",
}


def http_status(code: ErrorCode) -> int:
    return _POLICY[code][0]


def is_retryable(code: ErrorCode) -> bool:
    return _POLICY[code][1]


def max_attempts(code: ErrorCode) -> int:
    return _POLICY[code][2]


class AutomationError(Exception):
    """The only exception type that crosses a layer boundary."""

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        step: str | None = None,
    ) -> None:
        self.code = code
        self.message = message or USER_HINT[code]
        self.details = details or {}
        self.step = step
        super().__init__(f"{code.value}: {self.message}")

    @property
    def retryable(self) -> bool:
        return is_retryable(self.code)

    def to_payload(self, run_id: str | None = None) -> dict[str, Any]:
        return {
            "status": "error",
            "error_code": self.code.value,
            "retryable": self.retryable,
            "message": self.message,
            "user_message_hint": USER_HINT[self.code],
            "failed_step": self.step,
            "automation_run_id": run_id,
            "details": self.details,
        }
