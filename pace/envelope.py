"""JSON envelope: the only thing pace ever writes to stdout."""

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, NoReturn

import typer

from pace.api import ApiError

EXIT_CODES: dict[str, int] = {"bad_args": 2, "auth_required": 3, "rate_limited": 4}

HINTS: dict[str, str] = {
    "bad_args": "Check `pace schema` for accepted arguments.",
    "auth_required": "Ask the user to run `pace auth login` (interactive MFA). Do not run it yourself.",
    "rate_limited": "Wait 60 s and retry once; then stop and tell the user."
                    " For writes, verify with the matching read before retrying.",
    "not_found": "The id or date does not exist on Garmin Connect.",
    "endpoint_unavailable": "Feature not enabled for this account or device; do not retry.",
    "rejected": "Garmin refused the write; the message gives its reason. Fix the input or the data on"
                " Garmin Connect, then retry. Do not retry unchanged.",
    "network": "Transient. Retry once after a few seconds."
               " For writes, verify with the matching read before retrying.",
    "internal": "Unexpected. Re-run with --verbose for cache/fetch logs and report the message.",
}

VERBOSE = False


def set_verbose(flag: bool) -> None:
    global VERBOSE
    VERBOSE = flag


def log(msg: str) -> None:
    if VERBOSE:
        print(msg, file=sys.stderr)


class PaceError(Exception):
    def __init__(self, code: str, message: str, http: int | None = None, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http = http
        self.hint = hint or HINTS.get(code)

    @classmethod
    def from_api(cls, err: ApiError) -> "PaceError":
        return cls(err.code, err.message, err.http)


@dataclass
class Result:
    data: Any
    cache: dict[str, Any] | None = None
    shape: dict[str, Any] | None = None
    truncated: bool = False


def exit_code(code: str) -> int:
    return EXIT_CODES.get(code, 1)


def ok(command: str, args: dict[str, Any], result: Result) -> dict[str, Any]:
    return {
        "ok": True,
        "command": command,
        "args": args,
        "cache": result.cache,
        "shape": result.shape,
        "truncated": result.truncated,
        "data": result.data,
    }


def error(command: str, err: PaceError) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "error": {"code": err.code, "http": err.http, "message": err.message, "hint": err.hint},
    }


def emit(doc: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(doc, separators=(",", ":"), default=str))
    sys.stdout.write("\n")


def fail(command: str, err: PaceError) -> NoReturn:
    emit(error(command, err))
    raise typer.Exit(exit_code(err.code))


def run(command: str, args: dict[str, Any], work: Callable[[], Awaitable[Result]]) -> None:
    try:
        result = asyncio.run(work())
    except PaceError as err:
        fail(command, err)
    except Exception as exc:  # noqa: BLE001 -- every failure must still produce JSON
        fail(command, PaceError("internal", f"{type(exc).__name__}: {exc}"))
    emit(ok(command, args, result))
