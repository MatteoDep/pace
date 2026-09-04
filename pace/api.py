"""Async wrapper around the sync Garmin Connect client."""

import asyncio
import logging
import re
from dataclasses import dataclass
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
    429: ("rate_limited", "Rate limit exceeded (429)"),
}


def _status_from_text(msg: str) -> int | None:
    """Pull an HTTP status code out of an error message.

    garminconnect wraps HTTP failures as e.g. "API client error (404): ..."
    without exposing the code as an attribute, so we recover it from the text.
    """
    paren = re.search(r"\((\d{3})\)", msg)
    if paren:
        return int(paren.group(1))
    bare = re.search(r"\b([45]\d{2})\b", msg)
    return int(bare.group(1)) if bare else None


def classify_http(code: int | None, msg: str) -> ApiError:
    if code in _HTTP_CODES:
        kind, label = _HTTP_CODES[code]
        return ApiError(kind, label, code)
    if code is not None and 500 <= code < 600:
        return ApiError("network", f"Server error ({code})", code)
    return ApiError("internal", f"HTTP error: {msg}", code)


async def safe_call(
    method_name: str, *args: Any, **kwargs: Any
) -> tuple[bool, Any, ApiError | None]:
    """Rate-limited async wrapper. Returns (success, result, error)."""
    await limiter.acquire()
    async with concurrency:
        try:
            method = getattr(get_api(), method_name)
            result = await asyncio.to_thread(method, *args, **kwargs)
            return True, result, None
        except GarthHTTPError as err:
            code = getattr(getattr(err, "response", None), "status_code", None)
            return False, None, classify_http(code, str(err))
        except FileNotFoundError:
            return False, None, ApiError("auth_required", "No valid tokens. Run `pace auth login`.")
        except GarminConnectAuthenticationError as err:
            return False, None, ApiError("auth_required", f"Auth issue: {err}")
        except GarminConnectConnectionError as err:
            code = _status_from_text(str(err))
            if code is not None:
                return False, None, classify_http(code, str(err))
            return False, None, ApiError("network", f"Connection issue: {err}")
        except GarminConnectTooManyRequestsError as err:
            return False, None, ApiError("rate_limited", f"Rate limit: {err}")
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
