"""Identifier checks shared by hand-written commands and registry specs."""

import re

from pace.envelope import PaceError

_UUID = re.compile(r"[0-9a-f]{32}")


def validate_uuid(value: str) -> str:
    """Garmin gear uuids are 32 lowercase hex chars; the value is interpolated into a URL path."""
    norm = value.strip().lower()
    if not _UUID.fullmatch(norm):
        raise PaceError("bad_args", f"GEAR_UUID must be 32 hex chars (see `pace gear list`): {value!r}")
    return norm
