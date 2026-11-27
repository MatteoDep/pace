"""Tests for HTTP error classification in the API wrapper."""

import asyncio
import http.server
import threading
import time

import pytest
import requests
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from garth.exc import GarthHTTPError
from requests.adapters import HTTPAdapter, Retry

from pace import auth
from pace.api import ApiError, classify_http, safe_call
from pace.httpstatus import http_status, status_from_text
from pace.ratelimit import CONCURRENCY, RateLimiter, budget
from pace.envelope import HINTS, PaceError, exit_code
from tests.conftest import REAL_GET_API


def test_status_from_parenthesised_code() -> None:
    assert status_from_text("API client error (404): not found") == 404


def test_status_from_bare_code() -> None:
    assert status_from_text("Error in request: 503 Service Unavailable") == 503


def test_status_none_when_absent() -> None:
    assert status_from_text("Connection error: name resolution failed") is None
    assert status_from_text("retrieved 200 items") is None
    assert status_from_text(f"Connection error: {_POOL}") is None


# requests names the pool in every transport error: the port is not a status
_POOL = ("HTTPSConnectionPool(host='connectapi.garmin.com', port=443): Max retries exceeded with url: "
         "/activitylist-service/activities/search/activities?start=0&limit=20")


def _connection_error(cause: BaseException) -> GarminConnectConnectionError:
    """What garminconnect's `connectapi` raises when the request got no HTTP error back: its own
    error, chained to requests' (`from e`)."""
    err = GarminConnectConnectionError(f"Connection error: {cause}")
    err.__cause__ = cause
    return err


def _no_answer() -> requests.exceptions.ConnectionError:
    return requests.exceptions.ConnectionError(
        f"{_POOL} (Caused by NameResolutionError(\"Failed to resolve 'connectapi.garmin.com'\"))")


def _retries_ran_out(status: int) -> requests.exceptions.RetryError:
    """garth retries 408, 429 and 5xx; when they run out urllib3 gives up with this text and
    requests raises RetryError without a response."""
    return requests.exceptions.RetryError(f"{_POOL} (Caused by ResponseError('too many {status} error responses'))")


def test_status_from_the_chain_not_the_text() -> None:
    assert http_status(_connection_error(_no_answer())) is None
    assert http_status(_connection_error(_retries_ran_out(429))) == 429
    assert http_status(_garth_http_error(503)) == 503


def test_redirect_status_is_not_an_error_status() -> None:
    resp = requests.Response()
    resp.status_code = 302
    redirects = requests.exceptions.TooManyRedirects("Exceeded 30 redirects.", response=resp)
    assert http_status(_connection_error(redirects)) is None


def test_retry_give_up_text_of_real_urllib3() -> None:
    """Pins urllib3's give-up text, the only place a retried 429 or 5xx keeps its status."""
    class Always429(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(429)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Always429)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        session = requests.Session()
        session.mount("http://", HTTPAdapter(max_retries=Retry(total=1, status_forcelist=(429,), backoff_factor=0)))
        with pytest.raises(requests.exceptions.RetryError) as raised:
            session.get(f"http://127.0.0.1:{server.server_address[1]}/x", timeout=5)
    finally:
        server.shutdown()
        server.server_close()
    assert http_status(raised.value) == 429


def test_classify_codes() -> None:
    assert classify_http(401, "x").code == "auth_required"
    assert classify_http(429, "x").code == "rate_limited"
    assert classify_http(404, "x").code == "not_found"
    assert classify_http(400, "x").code == "endpoint_unavailable"
    assert classify_http(403, "x").code == "endpoint_unavailable"
    assert classify_http(500, "x").code == "network"
    assert classify_http(503, "x").code == "network"
    assert classify_http(418, "teapot") == ApiError("internal", "HTTP error: teapot", 418)
    assert classify_http(None, "boom").code == "internal"


def test_classify_keeps_http_code() -> None:
    err = classify_http(404, "API client error (404): gone")
    assert err.http == 404
    assert "404" in err.message


@pytest.mark.asyncio
async def test_library_validation_error_is_bad_args(monkeypatch) -> None:
    """garminconnect validates its own arguments (BP bounds, `get_workouts(limit<=0)`,
    unit keys) and raises ValueError/TypeError. That is the caller's mistake, so it must
    come back as bad_args -- exit 2 -- and not as an `internal` error."""
    class FakeApi:
        def set_blood_pressure(self, *args, **kwargs):
            raise ValueError("systolic must be an int in [70, 260]")

        def add_weigh_in(self, *args, **kwargs):
            raise TypeError("weight must be a number")

    monkeypatch.setattr("pace.api.get_api", lambda: FakeApi())
    monkeypatch.setattr("pace.api.limiter", RateLimiter(min_interval=0.0))
    ok, data, err = await safe_call("set_blood_pressure", 400, 76, 52)
    assert (ok, data) == (False, None)
    assert err == ApiError("bad_args", "Invalid argument: systolic must be an int in [70, 260]")
    ok, _, err = await safe_call("add_weigh_in", "heavy")
    assert not ok and err is not None and err.code == "bad_args"


def _raising_api(monkeypatch, exc: Exception) -> None:
    class FakeApi:
        def add_gear_to_activity(self, *args, **kwargs):
            raise exc

        connectapi = get_activity = add_gear_to_activity

    monkeypatch.setattr("pace.api.get_api", lambda: FakeApi())
    monkeypatch.setattr("pace.api.limiter", RateLimiter(min_interval=0.0))


def _garth_http_error(status: int, body: str = "") -> GarthHTTPError:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode()
    return GarthHTTPError(msg="Error in request", error=requests.HTTPError(response=resp))


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "code"), [(403, "endpoint_unavailable"), (404, "not_found"), (503, "network")])
async def test_garth_http_error_status_from_wrapped_response(monkeypatch, status, code) -> None:
    """Library methods that call `garth.put` directly (gear link/unlink) re-raise GarthHTTPError,
    whose status sits on `err.error.response`, not on `err.response`."""
    _raising_api(monkeypatch, _garth_http_error(status))
    ok, _, err = await safe_call("add_gear_to_activity", "u", 1)
    assert not ok and err is not None and (err.code, err.http) == (code, status)


@pytest.mark.asyncio
async def test_library_gear_not_found_is_not_found(monkeypatch) -> None:
    _raising_api(monkeypatch, GarminConnectConnectionError(
        "Cannot add gear u to activity 1: gear not found (likely retired/removed)"))
    ok, _, err = await safe_call("add_gear_to_activity", "u", 1)
    assert not ok and err is not None and err.code == "not_found"


@pytest.mark.asyncio
async def test_connection_error_without_status_stays_network(monkeypatch) -> None:
    _raising_api(monkeypatch, GarminConnectConnectionError("boom"))
    ok, _, err = await safe_call("add_gear_to_activity", "u", 1)
    assert not ok and err is not None and err.code == "network"


@pytest.mark.asyncio
@pytest.mark.parametrize(("exc", "code", "status"), [
    (requests.exceptions.ConnectionError(f"{_POOL} (Caused by NewConnectionError('refused'))"), "network", None),
    (requests.exceptions.ReadTimeout(f"{_POOL}: Read timed out. (read timeout=10)"), "network", None),
    (_retries_ran_out(429), "rate_limited", 429),
])
async def test_unwrapped_transport_error_is_not_internal(monkeypatch, exc, code, status) -> None:
    """Library writes that call garth directly let requests' own errors through unwrapped."""
    _raising_api(monkeypatch, exc)
    ok, _, err = await safe_call("add_gear_to_activity", "u", 1)
    assert not ok and err is not None and (err.code, err.http) == (code, status)


@pytest.mark.asyncio
async def test_dns_not_found_stays_network(monkeypatch) -> None:
    _raising_api(monkeypatch, GarminConnectConnectionError("Failed to resolve host: name not found"))
    ok, _, err = await safe_call("add_gear_to_activity", "u", 1)
    assert not ok and err is not None and err.code == "network"


_START_DATE = '{"message":"Gear\'s start date must be before activity\'s start date","error":"BadRequestException"}'


def _wrapped_by_connectapi(status: int, body: str) -> GarminConnectConnectionError:
    """What garminconnect's `connectapi` raises: its own error, chained to garth's."""
    try:
        try:
            raise _garth_http_error(status, body)
        except GarthHTTPError as e:
            raise GarminConnectConnectionError(f"API client error ({status}): {e}") from e
    except GarminConnectConnectionError as wrapped:
        return wrapped


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 409, 422])
async def test_direct_garth_4xx_is_rejected_with_garmin_reason(monkeypatch, status) -> None:
    """Writes that call garth directly (gear link, upload_workout, add_weigh_in, ...): Garmin explains
    a refused payload in the JSON body, and that reason must reach the caller."""
    _raising_api(monkeypatch, _garth_http_error(status, _START_DATE))
    ok, _, err = await safe_call("add_gear_to_activity", "u", 1)
    assert not ok and err is not None and (err.code, err.http) == ("rejected", status)
    assert err.message == f"Garmin rejected the request ({status}): Gear's start date must be before activity's start date"
    pe = PaceError.from_api(err)
    assert pe.hint == HINTS["rejected"] and exit_code(pe.code) == 1


@pytest.mark.asyncio
async def test_connectapi_write_400_is_rejected_with_garmin_reason(monkeypatch) -> None:
    """workout schedule/delete and activity effort go through the library's `connectapi`, which wraps
    garth's error; the reason is still on the chained cause."""
    _raising_api(monkeypatch, _wrapped_by_connectapi(400, _START_DATE))
    ok, _, err = await safe_call("connectapi", "/x", method="PUT")
    assert not ok and err is not None and (err.code, err.http) == ("rejected", 400)
    assert "Gear's start date must be before activity's start date" in err.message


@pytest.mark.asyncio
async def test_library_read_400_stays_endpoint_unavailable(monkeypatch) -> None:
    """Reads reach 400 through the library's wrappers; that still means "not enabled for this account"."""
    _raising_api(monkeypatch, _wrapped_by_connectapi(400, _START_DATE))
    ok, _, err = await safe_call("get_activity", 1)
    assert not ok and err is not None and err.code == "endpoint_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(("body", "expected"), ids=["trimmed", "capped", "blank-message", "json-list", "html"], argvalues=[
    ('{"message": "  too far  "}', "too far"),
    ('{"message": "' + "m" * 1000 + '"}', "m" * 300),
    ('{"message": "   "}', '{"message": " "}'),
    ('["not", "a dict"]', '["not", "a dict"]'),
    ("<html>\n  <body>Bad   request</body>\n</html>" + "x" * 1000, ("<html> <body>Bad request</body> </html>" + "x" * 1000)[:300]),
])
async def test_rejection_reason_fallbacks(monkeypatch, body, expected) -> None:
    _raising_api(monkeypatch, _garth_http_error(400, body))
    ok, _, err = await safe_call("add_gear_to_activity", "u", 1)
    assert not ok and err is not None and err.message == f"Garmin rejected the request (400): {expected}"


@pytest.mark.asyncio
async def test_rejection_with_empty_body_keeps_garth_text(monkeypatch) -> None:
    _raising_api(monkeypatch, _garth_http_error(400))
    ok, _, err = await safe_call("add_gear_to_activity", "u", 1)
    assert not ok and err is not None and err.code == "rejected" and "Error in request" in err.message


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "code"), [(403, "endpoint_unavailable"), (404, "not_found")])
async def test_connectapi_write_403_404_keep_their_codes(monkeypatch, status, code) -> None:
    _raising_api(monkeypatch, _wrapped_by_connectapi(status, _START_DATE))
    ok, _, err = await safe_call("connectapi", "/x", method="PUT")
    assert not ok and err is not None and err.code == code


@pytest.mark.asyncio
async def test_real_library_connectapi_wrapper_keeps_reason(monkeypatch) -> None:
    """Pins garminconnect's contract: `Garmin.connectapi` must chain garth's error (`from e`), or
    workout schedule/delete and activity effort lose Garmin's reason again."""
    api = Garmin()

    def refuse(*args, **kwargs):
        raise _garth_http_error(400, _START_DATE)

    monkeypatch.setattr(api.garth, "connectapi", refuse)
    monkeypatch.setattr("pace.api.get_api", lambda: api)
    monkeypatch.setattr("pace.api.limiter", RateLimiter(min_interval=0.0))
    ok, _, err = await safe_call("connectapi", "/x", method="PUT")
    assert not ok and err is not None and err.code == "rejected"
    assert "Gear's start date must be before activity's start date" in err.message


class _FakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self.delay = 0.0
        self._raise: list[Exception] = []

    def raise_next(self, exc: Exception) -> None:
        self._raise.append(exc)

    def get_x(self):
        self.calls += 1
        time.sleep(self.delay)
        if self._raise:
            raise self._raise.pop(0)
        return {"x": 1}


@pytest.fixture
def fake_client(monkeypatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr("pace.api.get_api", lambda: client)
    monkeypatch.setattr(
        "pace.api.limiter", RateLimiter(min_interval=0.01, per_minute=1000, per_hour=1000, per_day=1000)
    )
    return client


def test_budget_caps_a_gathered_fanout(fake_client) -> None:
    async def fan():
        return await asyncio.gather(*(safe_call("get_x") for _ in range(5)))

    with budget(2, 60) as b:
        res = asyncio.run(fan())
    assert sum(ok for ok, _, _ in res) == 2 and fake_client.calls == 2 and b.calls == 2
    assert all(err.code == "deferred" for ok, _, err in res if not ok)


def test_budget_past_deadline_defers_without_calling(fake_client) -> None:
    with budget(10, 0):
        ok, _, err = asyncio.run(safe_call("get_x"))
    assert not ok and err.code == "deferred" and fake_client.calls == 0


def test_rate_limited_ends_the_budget(fake_client) -> None:
    fake_client.raise_next(GarminConnectTooManyRequestsError("429"))

    async def two():
        return [await safe_call("get_x"), await safe_call("get_x")]

    with budget(10, 60) as b:
        first, second = asyncio.run(two())
    assert first[2].code == "rate_limited" and second[2].code == "deferred"
    assert b.stopped == "rate_limited" and fake_client.calls == 1


def test_no_budget_no_change(fake_client) -> None:
    assert all(asyncio.run(safe_call("get_x"))[0] for _ in range(3)) and fake_client.calls == 3


def test_stopped_budget_defers_calls_already_waiting_for_a_slot(fake_client) -> None:
    fake_client.delay = 0.2
    fake_client.raise_next(GarminConnectTooManyRequestsError("429"))

    async def fan():
        return await asyncio.gather(*(safe_call("get_x") for _ in range(8)))

    with budget(100, 60) as b:
        res = asyncio.run(fan())
    assert b.stopped == "rate_limited"
    assert fake_client.calls <= CONCURRENCY
    assert sum(1 for ok, _, err in res if not ok and err.code == "deferred") == 8 - fake_client.calls


def _two_calls() -> list:
    async def two():
        return [await safe_call("get_x"), await safe_call("get_x")]

    return asyncio.run(two())


def test_server_error_fails_only_its_call(fake_client) -> None:
    # Garmin answered with a 5xx: one endpoint failing says nothing about the others
    fake_client.raise_next(GarminConnectConnectionError("HTTP error: Error in request: 503 Server Error"))
    with budget(10, 60) as b:
        first, second = _two_calls()
    assert (first[0], first[2].code, first[2].http) == (False, "network", 503) and second[0] is True
    assert b.stopped is None and fake_client.calls == 2


def test_connection_error_ends_the_budget(fake_client) -> None:
    # no answer from Garmin at all: every later call would hit the same wall
    fake_client.raise_next(GarminConnectConnectionError("Connection error: Failed to establish a new connection"))
    with budget(10, 60) as b:
        first, second = _two_calls()
    assert (first[2].code, first[2].http) == ("network", None) and second[2].code == "deferred"
    assert b.stopped == "network" and fake_client.calls == 1


def test_connection_error_naming_the_port_ends_the_budget(fake_client) -> None:
    fake_client.raise_next(_connection_error(_no_answer()))
    with budget(10, 60) as b:
        first, second = _two_calls()
    assert (first[2].code, first[2].http) == ("network", None) and second[2].code == "deferred"
    assert b.stopped == "network" and fake_client.calls == 1


@pytest.mark.parametrize(("status", "code", "stops"), [
    (429, "rate_limited", True),
    (503, "network", False),
    (408, "network", False),
])
def test_retries_ran_out_keeps_the_status(fake_client, status, code, stops) -> None:
    fake_client.raise_next(_connection_error(_retries_ran_out(status)))
    with budget(10, 60) as b:
        first, second = _two_calls()
    assert (first[0], first[2].code, first[2].http) == (False, code, status)
    assert second[0] is not stops and b.stopped == (code if stops else None)


def _login_failed(status: int) -> GarminConnectConnectionError:
    """What Garmin.login raises when the token login's profile or settings read fails with an HTTP
    error, 429 included (garth's error has no `response`, so the library misses the status): the
    garth error is the cause."""
    err = GarminConnectConnectionError("Login failed: Error in request")
    err.__cause__ = _garth_http_error(status)
    return err


def _login_retries_ran_out(status: int) -> GarminConnectConnectionError:
    """Garmin.login when garth's retries on the settings read ran out: `connectapi` wraps the
    RetryError, and the login wraps that again."""
    err = GarminConnectConnectionError("Login failed: Connection error")
    err.__cause__ = _connection_error(_retries_ran_out(status))
    return err


@pytest.fixture
def failing_login(monkeypatch):
    """The real get_api, whose token login raises the exception given to the returned setter."""
    logins: list = []
    raised: list[BaseException] = []

    class FailingGarmin:
        def login(self, tokenstore=None):
            logins.append(tokenstore)
            raise raised[0]

    def prompt(*args, **kwargs):
        raise AssertionError("prompted for credentials")

    monkeypatch.setattr("pace.api.get_api", REAL_GET_API)
    monkeypatch.setattr("pace.api._api", None)
    monkeypatch.setattr("pace.auth.Garmin", FailingGarmin)
    monkeypatch.setattr("pace.auth.get_credentials", prompt)
    monkeypatch.setattr("pace.api.limiter", RateLimiter(min_interval=0.0))
    return raised.append, logins


@pytest.mark.parametrize(("exc", "code"), [
    (FileNotFoundError("oauth1_token.json"), "auth_required"),
    (GarminConnectAuthenticationError("Authentication failed: 401"), "auth_required"),
    (_login_failed(403), "auth_required"),
    (_login_failed(429), "rate_limited"),
    (_login_failed(500), "network"),
    (_login_retries_ran_out(429), "rate_limited"),
    (requests.exceptions.ConnectionError("connection refused"), "network"),
])
def test_noninteractive_token_login_failure_is_an_error_code(failing_login, exc, code) -> None:
    """Inside auth.noninteractive() a failed token login never prompts: it becomes an error code,
    and later calls get the same error without a second login attempt."""
    set_exc, logins = failing_login
    set_exc(exc)
    with auth.noninteractive():
        results = _two_calls()
    assert [(ok, err.code) for ok, _, err in results] == [(False, code), (False, code)]
    assert len(logins) == 1


@pytest.mark.parametrize(("exc", "code"), [
    (_login_failed(500), "network"),
    (_login_failed(429), "rate_limited"),
    (_login_retries_ran_out(429), "rate_limited"),
    (requests.exceptions.ConnectionError("connection refused"), "network"),
])
def test_failed_token_login_ends_the_budget(failing_login, exc, code) -> None:
    # the login is not tried again in the run, so no later call can succeed, whatever its status
    set_exc, logins = failing_login
    set_exc(exc)
    with auth.noninteractive(), budget(10, 60) as b:
        first, second = _two_calls()
    assert first[2].code == code and second[2].code == "deferred"
    assert b.stopped == code and len(logins) == 1
