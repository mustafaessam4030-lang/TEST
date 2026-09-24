import json

import httpx
import pytest

from app.auth.session import AuthContext, Authenticator
from app.collectors.api import ApiInspectionCollector
from app.errors import (
    AuthorizationError,
    ConfigurationError,
    HttpStatusError,
    MalformedResponseError,
    PaginationError,
    SessionExpiredError,
    SourceUnavailableError,
    UnexpectedResponseError,
)
from app.source_config import SourceConfig
from tests.conftest import SOURCE_DICT, synthetic_record


class CountingAuth(Authenticator):
    def __init__(self):
        self.calls = 0

    async def authenticate(self):
        self.calls += 1
        return AuthContext(cookies=[{"name": "sid", "value": f"session-{self.calls}", "domain": "source.test"}])


async def _no_sleep(_):
    return None


def _collector(settings, handler, source=None, auth=None):
    source = source or SourceConfig.model_validate(SOURCE_DICT)
    return ApiInspectionCollector(settings, source.api, auth or CountingAuth(), source.auth,
                                  transport=httpx.MockTransport(handler), sleep=_no_sleep)


def _page(items, total):
    return httpx.Response(200, json={"data": {"items": items, "total": total}})


def paged_handler(records, calls=None):
    def handler(request: httpx.Request):
        if calls is not None:
            calls.append(request)
        page = int(request.url.params["page"])
        size = int(request.url.params["size"])
        return _page(records[(page - 1) * size: page * size], len(records))
    return handler


async def test_parses_and_paginates(settings):
    records = [synthetic_record(i) for i in range(5)]
    calls = []
    result = await _collector(settings, paged_handler(records, calls)).collect()
    assert [r.raw for r in result.records] == records
    assert result.pages_fetched == 3 and result.expected_total == 5
    assert [c.url.params["page"] for c in calls] == ["1", "2", "3"]
    assert result.records[2].locator == "page=2,index=0"


async def test_stops_at_total_when_last_page_is_full(settings):
    records = [synthetic_record(i) for i in range(4)]
    calls = []
    result = await _collector(settings, paged_handler(records, calls)).collect()
    assert len(result.records) == 4 and len(calls) == 2


async def test_filters_are_sent_with_configured_param_names(settings):
    calls = []
    await _collector(settings, paged_handler([synthetic_record(1)], calls)).collect(serial_number="SYW00001")
    assert calls[0].url.params["sn"] == "SYW00001"


async def test_filter_without_configured_param_is_config_error(settings):
    data = json.loads(json.dumps(SOURCE_DICT))
    data["api"]["request"]["filter_params"] = {}
    with pytest.raises(ConfigurationError):
        await _collector(settings, paged_handler([]), SourceConfig.model_validate(data)).collect(serial_number="X")


async def test_session_cookie_is_sent(settings):
    seen = []

    def handler(request):
        seen.append(request.headers.get("cookie"))
        return _page([], 0)

    await _collector(settings, handler).collect()
    assert seen == ["sid=session-1"]


async def test_cursor_pagination_and_loop_guard(settings):
    data = json.loads(json.dumps(SOURCE_DICT))
    data["api"]["pagination"] = {"type": "cursor", "cursor_param": "after", "next_cursor_path": "data.next"}
    source = SourceConfig.model_validate(data)

    def handler(request):
        after = request.url.params.get("after")
        nxt = {None: "c1", "c1": "c2", "c2": None}[after]
        return httpx.Response(200, json={"data": {"items": [synthetic_record(len(after or ""))], "next": nxt}})

    result = await _collector(settings, handler, source).collect()
    assert result.pages_fetched == 3

    loop = lambda request: httpx.Response(200, json={"data": {"items": [], "next": "same"}})  # noqa: E731
    with pytest.raises(PaginationError, match="repeated"):
        await _collector(settings, loop, source).collect()


async def test_max_pages_guard(settings):
    data = json.loads(json.dumps(SOURCE_DICT))
    data["api"]["pagination"].update(max_pages=2, total_path=None)
    endless = lambda request: _page([synthetic_record(1), synthetic_record(2)], None)  # noqa: E731
    with pytest.raises(PaginationError, match="max_pages"):
        await _collector(settings, endless, SourceConfig.model_validate(data)).collect()


async def test_failure_on_later_page_is_pagination_error(settings):
    records = [synthetic_record(i) for i in range(5)]
    inner = paged_handler(records)

    def handler(request):
        if request.url.params["page"] == "2":
            return httpx.Response(400, json={})
        return inner(request)

    with pytest.raises(PaginationError, match="page=2"):
        await _collector(settings, handler).collect()


async def test_retries_transient_errors_then_succeeds(settings):
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectTimeout("slow")
        if len(attempts) == 2:
            return httpx.Response(503, headers={"Retry-After": "1"})
        return _page([synthetic_record(1)], 1)

    result = await _collector(settings, handler).collect()
    assert len(result.records) == 1 and len(attempts) == 3


async def test_retries_are_bounded(settings):
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(502)

    with pytest.raises(SourceUnavailableError):
        await _collector(settings, handler).collect()
    assert len(attempts) == settings.http_max_attempts


@pytest.mark.parametrize("response,error", [
    (httpx.Response(404), HttpStatusError),
    (httpx.Response(403), AuthorizationError),
    (httpx.Response(200, text="<html>login</html>", headers={"content-type": "text/html"}), UnexpectedResponseError),
    (httpx.Response(200, content=b"{not json", headers={"content-type": "application/json"}), MalformedResponseError),
    (httpx.Response(200, json={"data": {"items": {"not": "a list"}}}), UnexpectedResponseError),
    (httpx.Response(200, json={"unexpected": True}), UnexpectedResponseError),
])
async def test_non_retryable_errors_fail_fast(settings, response, error):
    attempts = []

    def handler(request):
        attempts.append(1)
        return response

    with pytest.raises(error):
        await _collector(settings, handler).collect()
    assert len(attempts) == 1


async def test_expired_session_reauthenticates_once(settings):
    auth = CountingAuth()

    def handler(request):
        if request.headers.get("cookie") == "sid=session-1":
            return httpx.Response(401)
        return _page([synthetic_record(1)], 1)

    result = await _collector(settings, handler, auth=auth).collect()
    assert len(result.records) == 1 and auth.calls == 2


async def test_redirect_to_login_counts_as_expired_session(settings):
    auth = CountingAuth()

    def handler(request):
        if request.url.path == "/login":
            return httpx.Response(200, text="<form>", headers={"content-type": "text/html"})
        if request.headers.get("cookie") == "sid=session-1":
            return httpx.Response(302, headers={"location": "/login"})
        return _page([], 0)

    await _collector(settings, handler, auth=auth).collect()
    assert auth.calls == 2


async def test_persistent_401_raises_session_expired(settings):
    auth = CountingAuth()
    with pytest.raises(SessionExpiredError):
        await _collector(settings, lambda r: httpx.Response(401), auth=auth).collect()
    assert auth.calls == 2  # original + exactly one re-authentication


async def test_post_body_pagination_and_filters(settings):
    data = json.loads(json.dumps(SOURCE_DICT))
    data["api"]["request"].update(method="POST", json_body={"sort": "date"}, filter_location="body")
    data["api"]["pagination"].update(type="offset", location="body", offset_param="skip", limit_param="take",
                                     page_size=2, total_path=None)
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        items = [synthetic_record(i) for i in range(3)][body["skip"]: body["skip"] + body["take"]]
        return _page(items, None)

    result = await _collector(settings, handler, SourceConfig.model_validate(data)).collect(serial_number="S1")
    assert len(result.records) == 3
    assert bodies[0] == {"sort": "date", "sn": "S1", "skip": 0, "take": 2}
    assert bodies[1]["skip"] == 2
