"""Async wrapper around the sync Garmin Connect client."""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import requests
from garth.exc import GarthHTTPError

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from pace import auth
from pace.httpstatus import http_status
from pace.ratelimit import BudgetExhausted, active_budget, limiter, slot

# The garminconnect library logs full tracebacks on every HTTP error (404, 400,
# etc.) before raising. We catch and classify those errors in `safe_call`, so
# silence the library's own logging to keep the CLI output clean.
logging.getLogger("garminconnect").setLevel(logging.CRITICAL)

@dataclass(frozen=True)
class ApiError:
    code: str
    message: str
    http: int | None = None


_api: Garmin | None = None


def get_api() -> Garmin:
    """Lazy-init the authenticated Garmin client."""
    global _api
    if _api is None:
        client = auth.init_api()
        if client is None:
            raise RuntimeError("Failed to authenticate with Garmin Connect.")
        _api = client
    return _api


_HTTP_CODES: dict[int, tuple[str, str]] = {
    400: ("endpoint_unavailable", "Endpoint not available (400) -- not enabled for your account"),
    401: ("auth_required", "Auth required (401) -- re-authenticate"),
    403: ("endpoint_unavailable", "Access denied (403)"),
    404: ("not_found", "Endpoint not found (404)"),
    408: ("network", "Request timeout (408)"),
    429: ("rate_limited", "Rate limit exceeded (429)"),
}


def classify_http(code: int | None, msg: str) -> ApiError:
    if code in _HTTP_CODES:
        kind, label = _HTTP_CODES[code]
        return ApiError(kind, label, code)
    if code is not None and 500 <= code < 600:
        return ApiError("network", f"Server error ({code})", code)
    return ApiError("internal", f"HTTP error: {msg}", code)


_BODY_MAX = 300
# Statuses with their own meaning; any other 4xx from a write is Garmin refusing the payload.
_NOT_REJECTIONS = {401, 403, 404, 408, 429}


def _rejection(status: int | None, err: GarthHTTPError) -> ApiError | None:
    """A refused write as `rejected`, with Garmin's reason: the JSON body's `message`, else the raw
    body with whitespace collapsed, else garth's text. garth's own message drops the body."""
    if status is None or not 400 <= status < 500 or status in _NOT_REJECTIONS:
        return None
    resp = getattr(getattr(err, "error", None), "response", None)
    text = getattr(resp, "text", "") or ""
    try:
        message = json.loads(text).get("message")
    except (ValueError, AttributeError):
        message = None
    reason = message.strip() if isinstance(message, str) else ""
    reason = reason or " ".join(text.split()) or str(err)
    return ApiError("rejected", f"Garmin rejected the request ({status}): {reason[:_BODY_MAX]}", status)


def _ends_budget(err: ApiError) -> bool:
    """Garmin throttles (rate_limited) or does not answer at all (network without an HTTP status):
    every later call would fail the same way. A 5xx is one endpoint failing, so only its call fails."""
    return err.code == "rate_limited" or (err.code == "network" and err.http is None)


async def safe_call(
    method_name: str, *args: Any, **kwargs: Any
) -> tuple[bool, Any, ApiError | None]:
    """Rate-limited async wrapper. Returns (success, result, error)."""
    ok, result, err = await _call(method_name, *args, **kwargs)
    budget = active_budget()
    if err is not None and budget is not None and _ends_budget(err):
        budget.stop(err.code)
    return ok, result, err


async def _call(
    method_name: str, *args: Any, **kwargs: Any
) -> tuple[bool, Any, ApiError | None]:
    budget = active_budget()
    try:
        await limiter.acquire(budget=budget)
    except BudgetExhausted as exc:
        return False, None, ApiError("deferred", f"Not sent: {exc.reason}")
    async with slot():
        if budget is not None:
            # The call may have waited for a slot while others stopped the budget or ran out the clock.
            try:
                budget.check_started()
            except BudgetExhausted as exc:
                return False, None, ApiError("deferred", f"Not sent: {exc.reason}")
        try:
            method = getattr(get_api(), method_name)
            result = await asyncio.to_thread(method, *args, **kwargs)
            return True, result, None
        except auth.TokenLoginError as err:
            # get_api() inside pace export: stored tokens missing or rejected, or the login failed.
            # The login is not tried again in the run, so a failed login ends the budget whatever
            # its status. Not auth_required: it ends the export, and a stopped budget would turn
            # the rest of a fan-out into `deferred`, which could be reported in its place.
            if budget is not None and err.code != "auth_required":
                budget.stop(err.code)
            return False, None, ApiError(err.code, err.message, err.http)
        except GarthHTTPError as err:
            # Raised by library writes that call garth directly (gear link, upload_workout,
            # add_weigh_in, ...): a 4xx there is a refused payload, not "endpoint not enabled".
            code = http_status(err)
            return False, None, _rejection(code, err) or classify_http(code, str(err))
        except FileNotFoundError:
            return False, None, ApiError("auth_required", "No valid tokens. Run `pace auth login`.")
        except GarminConnectAuthenticationError as err:
            return False, None, ApiError("auth_required", f"Auth issue: {err}")
        except GarminConnectConnectionError as err:
            cause = err.__cause__
            if method_name == "connectapi" and isinstance(cause, GarthHTTPError):
                # pace calls `connectapi` itself for writes (workout schedule/delete, activity
                # effort) and for one read (`workout calendar`); a 400 there surfaces as `rejected`.
                # The library wraps garth's error, which still carries Garmin's reason.
                rejected = _rejection(http_status(cause), cause)
                if rejected:
                    return False, None, rejected
            code = http_status(err)
            if code is None and "gear not found" in str(err).lower():
                # The library's own 404 branches (e.g. gear link/unlink) drop the status code.
                code = 404
            if code is not None:
                return False, None, classify_http(code, str(err))
            return False, None, ApiError("network", f"Connection issue: {err}")
        except GarminConnectTooManyRequestsError as err:
            return False, None, ApiError("rate_limited", f"Rate limit: {err}")
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.RetryError) as err:
            # Library writes that call garth directly let requests' transport errors through unwrapped.
            code = http_status(err)
            if code is not None:
                return False, None, classify_http(code, str(err))
            return False, None, ApiError("network", f"Connection issue: {err}")
        except (ValueError, TypeError) as err:
            # garminconnect validates its own arguments (blood-pressure bounds,
            # get_workouts(limit<=0), unit keys, timestamp format) and raises
            # ValueError/TypeError. That is the caller's mistake, not an internal
            # failure, so it must surface as bad_args / exit 2.
            return False, None, ApiError("bad_args", f"Invalid argument: {err}")
        except Exception as err:
            return False, None, ApiError("internal", f"Unexpected error: {err}")


async def paginate(
    method_name: str,
    *,
    page_size: int = 1000,
    max_items: int | None = None,
    **kwargs: Any,
) -> list[Any]:
    """Repeatedly call a `(start, limit, ...)`-style method until exhausted.

    Garmin caps `limit` at 1000 per request for `get_activities`. We accumulate
    pages until an empty result or the optional `max_items` cap is hit.
    """
    out: list[Any] = []
    start = 0
    while True:
        ok, page, err = await safe_call(method_name, start, page_size, **kwargs)
        if not ok:
            assert err is not None
            # Local import: pace.envelope imports pace.api (for ApiError), so importing
            # PaceError at module scope here would be a circular import.
            from pace.envelope import PaceError
            raise PaceError.from_api(err)
        if not page:
            break
        out.extend(page)
        if len(page) < page_size:
            break
        if max_items is not None and len(out) >= max_items:
            out = out[:max_items]
            break
        start += page_size
    return out
