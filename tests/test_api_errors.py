"""Tests for HTTP error classification in the API wrapper."""

from pace.api import _classify_http_error, _status_from_text


def test_status_from_parenthesised_code() -> None:
    # garminconnect's client-error wrapper format.
    assert _status_from_text("API client error (404): not found") == 404
    assert _status_from_text("API client error (400): bad request") == 400


def test_status_from_bare_code() -> None:
    assert _status_from_text("Error in request: 503 Service Unavailable") == 503


def test_status_none_when_absent() -> None:
    assert _status_from_text("Connection error: name resolution failed") is None
    # A 3-digit number that is not a 4xx/5xx status must not be mistaken for one.
    assert _status_from_text("retrieved 200 items") is None


def test_classify_known_and_unknown_codes() -> None:
    assert _classify_http_error(404, "x") == "Endpoint not found (404)"
    assert _classify_http_error(429, "x") == "Rate limit exceeded (429)"
    assert _classify_http_error(418, "teapot") == "HTTP error: teapot"
    assert _classify_http_error(None, "boom") == "HTTP error: boom"
