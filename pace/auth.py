"""Garmin Connect authentication and token management."""

import json
import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from getpass import getpass

import requests
from garth.exc import GarthException, GarthHTTPError

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
)
from pace import config
from pace.config import TOKEN_STORE
from pace.httpstatus import http_status


class TokenLoginError(Exception):
    """A token login that failed inside `noninteractive()`; `code` is a pace error code."""

    def __init__(self, code: str, message: str, http: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http = http


_prompts = True  # False inside noninteractive(): init_api raises TokenLoginError instead of prompting
_failed: TokenLoginError | None = None  # the failed token login of the current noninteractive() block


@contextmanager
def noninteractive() -> Iterator[None]:
    """Inside this block `init_api` never prompts: a failed token login raises TokenLoginError, and
    later calls raise the same error without trying again (one login attempt per block)."""
    global _prompts, _failed
    saved = _prompts, _failed
    _prompts, _failed = False, None
    try:
        yield
    finally:
        _prompts, _failed = saved


def _login_error(exc: BaseException) -> TokenLoginError:
    """auth_required when there are no tokens or Garmin refused them (auth error, 401, 403),
    rate_limited on a 429, else network."""
    status = http_status(exc)
    if isinstance(exc, (FileNotFoundError, GarminConnectAuthenticationError)) or status in (401, 403):
        return TokenLoginError("auth_required", f"Stored Garmin tokens are missing or were rejected: {exc}", status)
    if status == 429:
        # the library maps a 429 only on the token refresh; on the profile or settings read it
        # arrives as a connection error with garth's error as the cause
        return TokenLoginError("rate_limited", f"Token login rate limited: {exc}", status)
    return TokenLoginError("network", f"Token login failed: {exc}", status)


def get_credentials() -> tuple[str, str]:
    """Read EMAIL/PASSWORD env vars or prompt interactively."""
    email = os.getenv("EMAIL") or input("Login email: ")
    password = os.getenv("PASSWORD") or getpass("Password: ")
    return email, password


def init_api() -> Garmin | None:
    """Return authenticated Garmin client, or None on failure.

    Tries cached tokens first, falls back to credential entry with MFA support.
    Inside `noninteractive()` it never prompts: a failed token login raises TokenLoginError.
    """
    global _failed
    if not _prompts and _failed is not None:
        raise TokenLoginError(_failed.code, _failed.message, _failed.http)
    try:
        garmin = Garmin()
        garmin.login(str(TOKEN_STORE))
        return garmin
    except (
        FileNotFoundError,
        GarthHTTPError,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        requests.exceptions.RequestException,
    ) as exc:
        if not _prompts:
            _failed = _login_error(exc)
            raise _failed from exc
        if isinstance(exc, requests.exceptions.RequestException):
            raise  # interactive behaviour unchanged: only the token errors fall through to the prompt

    while True:
        try:
            email, password = get_credentials()
            garmin = Garmin(email=email, password=password, is_cn=False, return_on_mfa=True)
            result1, result2 = garmin.login()

            if result1 == "needs_mfa":
                mfa_code = input("MFA code: ")
                try:
                    garmin.resume_login(result2, mfa_code)  # ty:ignore[invalid-argument-type]
                except GarthHTTPError as err:
                    msg = str(err)
                    if "429" in msg:
                        sys.exit(1)
                    if "401" in msg or "403" in msg:
                        continue
                    sys.exit(1)
                except GarthException:
                    continue

            garmin.garth.dump(str(TOKEN_STORE))
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


def oauth2_expires_at() -> str | None:
    """Expiry of the cached oauth2 token as an ISO local string, or None if unreadable.

    Not the same thing as being logged out: garth mints a new oauth2 token from the
    long-lived oauth1 token, so an expired one still means `logged_in`.
    """
    try:
        data = json.loads((config.TOKEN_STORE / "oauth2_token.json").read_text())
    except (OSError, ValueError):
        return None
    expires_at = data.get("expires_at") if isinstance(data, dict) else None
    if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
        return None
    return datetime.fromtimestamp(expires_at).astimezone().isoformat()


def auth_status() -> dict[str, object]:
    """Return current token state."""
    return {
        "authenticated": config.TOKEN_STORE.exists(),
        "token_path": str(config.TOKEN_STORE),
        "oauth2_expires_at": oauth2_expires_at(),
    }


def logout() -> bool:
    """Delete cached tokens. Returns True if anything removed."""
    if not config.TOKEN_STORE.exists():
        return False
    if config.TOKEN_STORE.is_dir():
        shutil.rmtree(config.TOKEN_STORE)
    else:
        config.TOKEN_STORE.unlink()
    return True
