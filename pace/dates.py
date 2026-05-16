"""Date parsing and Typer option helpers."""

import re
from collections.abc import Iterator
from datetime import date, timedelta

import typer

__all__ = ["parse_date", "date_range"]

_RELATIVE = re.compile(r"^([+-]?\d+)d$")


def parse_date(value: str | None) -> date:
    """Parse YYYY-MM-DD, 'today', 'yesterday', or '-Nd' / '+Nd' (relative to today)."""
    if value is None or value == "today":
        return date.today()
    if value == "yesterday":
        return date.today() - timedelta(days=1)
    m = _RELATIVE.match(value)
    if m:
        return date.today() + timedelta(days=int(m.group(1)))
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise typer.BadParameter(
            f"Invalid date {value!r}. Use YYYY-MM-DD, today, yesterday, or -Nd."
        ) from err


def date_range(start: date, end: date) -> Iterator[date]:
    if start > end:
        return
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


# Option factories deferred: commands use typer.Option(...) inline so Typer's
# signature inspection sees the OptionInfo at the right spot.
