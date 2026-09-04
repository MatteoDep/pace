"""Tests for HTTP error classification in the API wrapper."""

from pace.api import ApiError, _status_from_text, classify_http


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
