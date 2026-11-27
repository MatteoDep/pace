"""The HTTP status behind a failed Garmin call, read from the chain of errors the libraries raise."""

import re

import requests

# urllib3's reason when garth's retries on 408, 429 and 5xx run out. requests then raises RetryError
# without a response, so this text is the only place the status survives.
_RETRIES_RAN_OUT = re.compile(r"too many (\d{3}) error responses")


def status_from_text(msg: str) -> int | None:
    """Pull an HTTP status code out of an error message.

    garminconnect wraps HTTP failures as e.g. "API client error (404): ..." without exposing the
    code as an attribute. A number right after `port=` is the pool's port, not a status.
    """
    paren = re.search(r"\((\d{3})\)", msg)
    if paren:
        return int(paren.group(1))
    bare = re.search(r"(?<!port=)\b([45]\d{2})\b", msg)
    return int(bare.group(1)) if bare else None


def _response_status(err: BaseException) -> int | None:
    """An error status on the response of `err`, or of its `.error` (garth's GarthHTTPError)."""
    for holder in (getattr(err, "error", None), err):
        code = getattr(getattr(holder, "response", None), "status_code", None)
        if isinstance(code, int) and code >= 400:
            return code
    return None


def http_status(exc: BaseException) -> int | None:
    """The HTTP error status behind `exc`, or None when Garmin sent none (no answer at all).

    garminconnect chains the error it wraps (`from e`) and garth keeps requests' error on `.error`,
    so the status is read from there. A requests error without one is a transport failure: its text
    names the host, port and url, and holds a status only when garth's retries ran out. The message
    text is read only when the chain holds no requests error.
    """
    err: BaseException | None = exc
    seen: set[int] = set()
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        code = _response_status(err)
        if code is not None:
            return code
        if isinstance(err, requests.exceptions.RequestException):
            ran_out = _RETRIES_RAN_OUT.search(str(err))
            return int(ran_out.group(1)) if ran_out else None
        err = err.__cause__
    return status_from_text(str(exc))
