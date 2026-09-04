"""Date parsing and range resolution."""

import re
from collections.abc import Iterator
from datetime import date, timedelta

from pace.envelope import PaceError

__all__ = ["parse_date", "date_range", "resolve_range"]

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
    except ValueError:
        raise PaceError("bad_args", f"Invalid date {value!r}. Use YYYY-MM-DD, today, yesterday, or -Nd.") from None


def date_range(start: date, end: date) -> Iterator[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def resolve_range(from_: str | None, to: str, days: int) -> tuple[date, date]:
    """Resolve --from/--to/--days into an inclusive (start, end). --from wins over --days."""
    if days < 1:
        raise PaceError("bad_args", "--days must be >= 1.")
    end = parse_date(to)
    start = parse_date(from_) if from_ else end - timedelta(days=days - 1)
    if start > end:
        start, end = end, start
    return start, end
