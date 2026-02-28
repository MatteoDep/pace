"""Garmin Connect API client: authentication, rate limiting, and safe call wrapper."""

import os
import sys
import time
from collections import deque
from getpass import getpass
from pathlib import Path

import requests
from garth.exc import GarthException, GarthHTTPError

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

_MIN_CALL_INTERVAL = 2.0   # seconds between any two calls
_MAX_PER_MINUTE = 10
_MAX_PER_HOUR = 100
_MAX_PER_DAY = 500

_WINDOW_MINUTE = 60.0
_WINDOW_HOUR = 3600.0
_WINDOW_DAY = 86400.0

_call_history: deque[float] = deque()
_last_call_time: float = 0.0


def _rate_limit():
    """Enforce per-call interval and per-minute/hour/day caps."""
    global _last_call_time

    while True:
        now = time.monotonic()

        # Drop timestamps outside the day window (oldest entries first)
        while _call_history and now - _call_history[0] > _WINDOW_DAY:
            _call_history.popleft()

        calls_last_minute = sum(1 for t in _call_history if now - t <= _WINDOW_MINUTE)
        calls_last_hour = sum(1 for t in _call_history if now - t <= _WINDOW_HOUR)
        calls_last_day = len(_call_history)

        wait = 0.0

        elapsed = now - _last_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            wait = max(wait, _MIN_CALL_INTERVAL - elapsed)

        if calls_last_minute >= _MAX_PER_MINUTE:
            minute_calls = [t for t in _call_history if now - t <= _WINDOW_MINUTE]
            wait = max(wait, _WINDOW_MINUTE - (now - minute_calls[0]) + 0.1)

        if calls_last_hour >= _MAX_PER_HOUR:
            hour_calls = [t for t in _call_history if now - t <= _WINDOW_HOUR]
            wait = max(wait, _WINDOW_HOUR - (now - hour_calls[0]) + 0.1)

        if calls_last_day >= _MAX_PER_DAY:
            wait = max(wait, _WINDOW_DAY - (now - _call_history[0]) + 0.1)

        if wait <= 0:
            break
        time.sleep(wait)

    _last_call_time = time.monotonic()
    _call_history.append(_last_call_time)


# ---------------------------------------------------------------------------
# Safe API call wrapper
# ---------------------------------------------------------------------------

def safe_api_call(api_method, *args, **kwargs):
    """Rate-limited API call with comprehensive error handling.

    Returns (success: bool, result: Any, error_message: str | None).
    """
    _rate_limit()

    try:
        result = api_method(*args, **kwargs)
        return True, result, None

    except GarthHTTPError as e:
        error_str = str(e)
        status_code = getattr(getattr(e, "response", None), "status_code", None)

        if status_code == 400 or "400" in error_str:
            return False, None, "Endpoint not available (400 Bad Request) - Feature may not be enabled for your account"
        if status_code == 401 or "401" in error_str:
            return False, None, "Authentication required (401 Unauthorized) - Please re-authenticate"
        if status_code == 403 or "403" in error_str:
            return False, None, "Access denied (403 Forbidden) - Account may not have permission"
        if status_code == 404 or "404" in error_str:
            return False, None, "Endpoint not found (404) - Feature may have been moved or removed"
        if status_code == 429 or "429" in error_str:
            return False, None, "Rate limit exceeded (429) - Please wait before making more requests"
        if status_code == 500 or "500" in error_str:
            return False, None, "Server error (500) - Garmin's servers are experiencing issues"
        if status_code == 503 or "503" in error_str:
            return False, None, "Service unavailable (503) - Garmin's servers are temporarily unavailable"
        return False, None, f"HTTP error: {e}"

    except FileNotFoundError:
        return False, None, "No valid tokens found. Please login with your email/password to create new tokens."

    except GarminConnectAuthenticationError as e:
        return False, None, f"Authentication issue: {e}"

    except GarminConnectConnectionError as e:
        return False, None, f"Connection issue: {e}"

    except GarminConnectTooManyRequestsError as e:
        return False, None, f"Rate limit exceeded: {e}"

    except Exception as e:
        return False, None, f"Unexpected error: {e}"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def get_credentials():
    """Get email and password from environment or user input."""
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")

    if not email:
        email = input("Login email: ")
    if not password:
        password = getpass("Enter password: ")

    return email, password


def init_api() -> Garmin | None:
    """Initialize Garmin API with authentication and token management."""
    tokenstore = os.getenv("GARMINTOKENS", "~/.garminconnect")
    tokenstore_path = Path(tokenstore).expanduser()

    # Try stored tokens first
    try:
        garmin = Garmin()
        garmin.login(str(tokenstore_path))
        return garmin

    except (
        FileNotFoundError,
        GarthHTTPError,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
    ):
        pass

    # Fall back to credential entry with retry on auth failure
    while True:
        try:
            email, password = get_credentials()

            garmin = Garmin(
                email=email, password=password, is_cn=False, return_on_mfa=True
            )
            result1, result2 = garmin.login()

            if result1 == "needs_mfa":
                mfa_code = input("Please enter your MFA code: ")

                try:
                    garmin.resume_login(result2, mfa_code)  # ty:ignore[invalid-argument-type]

                except GarthHTTPError as garth_error:
                    error_str = str(garth_error)
                    if "429" in error_str and "Too Many Requests" in error_str:
                        sys.exit(1)
                    elif "401" in error_str or "403" in error_str:
                        continue
                    else:
                        sys.exit(1)

                except GarthException:
                    continue

            garmin.garth.dump(str(tokenstore_path))
            return garmin

        except GarminConnectAuthenticationError:
            continue

        except (
            FileNotFoundError,
            GarthHTTPError,
            GarminConnectConnectionError,
            requests.exceptions.HTTPError,
        ):
            return None

        except KeyboardInterrupt:
            return None
