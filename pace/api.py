"""Async wrapper around the sync Garmin Connect client."""

import asyncio
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
