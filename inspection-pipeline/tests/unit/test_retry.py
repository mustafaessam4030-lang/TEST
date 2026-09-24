import pytest

from app.errors import AuthenticationError, SourceUnavailableError
from app.retry import backoff_delay, retry_async, retry_sync


async def test_retry_async_retries_only_retryable():
    calls, sleeps = [], []

    async def sleep(d):
        sleeps.append(d)

    async def op():
        calls.append(1)
        if len(calls) < 3:
            raise SourceUnavailableError("down")
        return "ok"

    assert await retry_async(op, attempts=3, base_delay=1, description="x", sleep=sleep) == "ok"
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]  # exponential


async def test_auth_errors_are_not_retried():
    calls = []

    async def op():
        calls.append(1)
        raise AuthenticationError("bad password")

    with pytest.raises(AuthenticationError):
        await retry_async(op, attempts=5, base_delay=0, description="x")
    assert len(calls) == 1


def test_retry_sync_gives_up_after_attempts():
    calls = []

    def op():
        calls.append(1)
        raise SourceUnavailableError("down")

    with pytest.raises(SourceUnavailableError):
        retry_sync(op, attempts=3, base_delay=0, description="x", sleep=lambda _: None)
    assert len(calls) == 3


def test_backoff_is_capped():
    assert backoff_delay(20, base=2, cap=60) <= 72
