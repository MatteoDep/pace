"""Tests for HTTP error classification in the API wrapper."""

import pytest
import requests
from garminconnect import GarminConnectConnectionError
from garth.exc import GarthHTTPError

from pace.api import ApiError, _status_from_text, classify_http, safe_call
from pace.ratelimit import RateLimiter


def test_status_from_parenthesised_code() -> None:
    assert _status_from_text("API client error (404): not found") == 404


def test_status_from_bare_code() -> None:
    assert _status_from_text("Error in request: 503 Service Unavailable") == 503


def test_status_none_when_absent() -> None:
    assert _status_from_text("Connection error: name resolution failed") is None
    assert _status_from_text("retrieved 200 items") is None


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

    monkeypatch.setattr("pace.api.get_api", lambda: FakeApi())
    monkeypatch.setattr("pace.api.limiter", RateLimiter(min_interval=0.0))


def _garth_http_error(status: int) -> GarthHTTPError:
    resp = requests.Response()
    resp.status_code = status
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
async def test_dns_not_found_stays_network(monkeypatch) -> None:
    _raising_api(monkeypatch, GarminConnectConnectionError("Failed to resolve host: name not found"))
    ok, _, err = await safe_call("add_gear_to_activity", "u", 1)
    assert not ok and err is not None and err.code == "network"
