"""Server-side retry. The LLM never retries; it is told the outcome once."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Awaitable, Callable, TypeVar

from app.core.errors import AutomationError, ErrorCode, max_attempts
from app.core.logging import log

T = TypeVar("T")
logger = logging.getLogger(__name__)

BASE_DELAY_S = 2.0
CAP_DELAY_S = 30.0


def backoff_delay(attempt: int) -> float:
    """Exponential backoff with full-ish jitter (attempt is 1-based)."""
    raw = min(BASE_DELAY_S * (2 ** (attempt - 1)), CAP_DELAY_S)
    return raw * random.uniform(0.5, 1.5)


async def with_retry(
    fn: Callable[[int], Awaitable[T]],
    *,
    deadline_s: float,
    on_attempt_failed: Callable[[int, AutomationError], None] | None = None,
) -> T:
    """Run `fn(attempt)` under the taxonomy's retry policy and a hard deadline.

    `fn` receives the 1-based attempt number so it can escalate isolation
    (attempt 2+ gets a fresh browser context and a forced relogin).
    """
    started = time.monotonic()
    attempt = 0
    last: AutomationError | None = None

    while True:
        attempt += 1
        try:
            return await fn(attempt)
        except AutomationError as err:
            last = err
            allowed = max_attempts(err.code)
            elapsed = time.monotonic() - started
            if on_attempt_failed:
                on_attempt_failed(attempt, err)
            if not err.retryable or attempt >= allowed:
                raise
            delay = backoff_delay(attempt)
            if elapsed + delay >= deadline_s:
                log(logger, logging.WARNING, "retry.deadline_exceeded",
                    code=err.code.value, attempt=attempt, elapsed_s=round(elapsed, 2))
                raise AutomationError(
                    ErrorCode.TIMEOUT,
                    "Run deadline reached while retrying.",
                    details={"last_error": err.code.value, "attempts": attempt},
                ) from err
            log(logger, logging.WARNING, "retry.scheduled",
                code=err.code.value, attempt=attempt, delay_s=round(delay, 2))
            await asyncio.sleep(delay)
        except asyncio.TimeoutError as err:  # pragma: no cover - defensive
            raise AutomationError(ErrorCode.TIMEOUT, "Operation timed out.") from err

    raise last  # pragma: no cover - unreachable


class CircuitBreaker:
    """Per-source breaker. Cache reads keep working while it is open."""

    CLOSED, OPEN, HALF_OPEN = "CLOSED", "OPEN", "HALF_OPEN"
    IMMEDIATE_OPEN = {ErrorCode.CAPTCHA_DETECTED, ErrorCode.LOGIN_FAILED}

    def __init__(self, failure_threshold: int = 5, cooldown_s: float = 300.0,
                 max_cooldown_s: float = 3600.0) -> None:
        self.state = self.CLOSED
        self.failures = 0
        self.failure_threshold = failure_threshold
        self.base_cooldown_s = cooldown_s
        self.cooldown_s = cooldown_s
        self.max_cooldown_s = max_cooldown_s
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN and self.opened_at is not None:
            if time.monotonic() - self.opened_at >= self.cooldown_s:
                self.state = self.HALF_OPEN  # let one canary through
                return True
            return False
        return self.state == self.HALF_OPEN

    def record_success(self) -> None:
        self.state = self.CLOSED
        self.failures = 0
        self.cooldown_s = self.base_cooldown_s
        self.opened_at = None

    def record_failure(self, code: ErrorCode) -> None:
        self.failures += 1
        if code in self.IMMEDIATE_OPEN or self.failures >= self.failure_threshold:
            if self.state == self.HALF_OPEN:
                self.cooldown_s = min(self.cooldown_s * 2, self.max_cooldown_s)
            self.state = self.OPEN
            self.opened_at = time.monotonic()

    def snapshot(self) -> dict[str, object]:
        return {"state": self.state, "failures": self.failures,
                "cooldown_s": self.cooldown_s}
