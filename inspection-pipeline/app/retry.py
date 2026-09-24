"""Bounded retry with exponential backoff and jitter.

Only exceptions that are explicitly classified as retryable are retried;
everything else propagates immediately (for example authentication failures).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.errors import PipelineError

T = TypeVar("T")
logger = logging.getLogger(__name__)


def is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, PipelineError) and exc.retryable


def backoff_delay(attempt: int, base: float, cap: float = 60.0) -> float:
    """Delay before retry number ``attempt`` (1-based): base * 2^(attempt-1) with +/-20% jitter."""
    delay = min(cap, base * (2 ** (attempt - 1)))
    return delay * random.uniform(0.8, 1.2)


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_delay: float,
    description: str,
    should_retry: Callable[[BaseException], bool] = is_retryable,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run ``operation`` up to ``attempts`` times."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            if attempt >= attempts or not should_retry(exc):
                raise
            delay = getattr(exc, "retry_after", None) or backoff_delay(attempt, base_delay)
            logger.warning(
                "%s failed (attempt %d/%d): %s; retrying in %.1fs",
                description, attempt, attempts, exc, delay,
            )
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def retry_sync(
    operation: Callable[[], T],
    *,
    attempts: int,
    base_delay: float,
    description: str,
    should_retry: Callable[[BaseException], bool] = is_retryable,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Synchronous twin of :func:`retry_async` (used for the Snowflake connector)."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except BaseException as exc:  # noqa: BLE001
            if attempt >= attempts or not should_retry(exc):
                raise
            delay = backoff_delay(attempt, base_delay)
            logger.warning(
                "%s failed (attempt %d/%d): %s; retrying in %.1fs",
                description, attempt, attempts, exc, delay,
            )
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
