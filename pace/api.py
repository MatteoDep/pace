"""Async wrapper around the sync Garmin Connect client."""

import asyncio
import logging
from typing import Any

from garth.exc import GarthHTTPError

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from pace import auth
from pace.ratelimit import concurrency, limiter

# The garminconnect library logs full tracebacks on every HTTP error (404, 400,
# etc.) before raising. We catch and classify those errors in `safe_call`, so
# silence the library's own logging to keep the CLI output clean.
logging.getLogger("garminconnect").setLevel(logging.CRITICAL)

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


def _classify_http_error(err: GarthHTTPError) -> str:
    msg = str(err)
    code = getattr(getattr(err, "response", None), "status_code", None)
    if code == 400 or "400" in msg:
        return "Endpoint not available (400) -- not enabled for your account"
    if code == 401 or "401" in msg:
        return "Auth required (401) -- re-authenticate"
    if code == 403 or "403" in msg:
        return "Access denied (403)"
    if code == 404 or "404" in msg:
        return "Endpoint not found (404)"
    if code == 429 or "429" in msg:
        return "Rate limit exceeded (429)"
    if code == 500 or "500" in msg:
        return "Server error (500)"
    if code == 503 or "503" in msg:
        return "Service unavailable (503)"
    return f"HTTP error: {err}"


async def safe_call(
    method_name: str, *args: Any, **kwargs: Any
) -> tuple[bool, Any, str | None]:
    """Rate-limited async wrapper.

    Returns (success, result, error_message).
    """
    await limiter.acquire()
    async with concurrency:
        try:
            method = getattr(get_api(), method_name)
            result = await asyncio.to_thread(method, *args, **kwargs)
            return True, result, None
        except GarthHTTPError as err:
            return False, None, _classify_http_error(err)
        except FileNotFoundError:
            return False, None, "No valid tokens. Run `pace auth login`."
        except GarminConnectAuthenticationError as err:
            return False, None, f"Auth issue: {err}"
        except GarminConnectConnectionError as err:
            return False, None, f"Connection issue: {err}"
        except GarminConnectTooManyRequestsError as err:
            return False, None, f"Rate limit: {err}"
        except Exception as err:
            return False, None, f"Unexpected error: {err}"


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
            raise RuntimeError(err)
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
