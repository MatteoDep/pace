"""Garmin Connect authentication and token management."""

import os
import shutil
import sys
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


def get_credentials() -> tuple[str, str]:
    """Read EMAIL/PASSWORD env vars or prompt interactively."""
    email = os.getenv("EMAIL") or input("Login email: ")
    password = os.getenv("PASSWORD") or getpass("Password: ")
    return email, password


def init_api() -> Garmin | None:
    """Return authenticated Garmin client, or None on failure.

    Tries cached tokens first, falls back to credential entry with MFA support.
    """
    try:
        garmin = Garmin()
        garmin.login(str(TOKEN_STORE))
        return garmin
    except (
        FileNotFoundError,
        GarthHTTPError,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
    ):
        pass

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


def auth_status() -> dict[str, object]:
    """Return current token state."""
    return {
        "authenticated": config.TOKEN_STORE.exists(),
        "token_path": str(config.TOKEN_STORE),
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
