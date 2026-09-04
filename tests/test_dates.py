from datetime import date, timedelta

import pytest

from pace.dates import parse_date, resolve_range
from pace.envelope import PaceError


def test_days_ending_today() -> None:
    start, end = resolve_range(None, "today", 7)
    assert end == date.today() and start == end - timedelta(days=6)


def test_from_overrides_days_and_swaps() -> None:
    start, end = resolve_range("2026-01-10", "2026-01-01", 99)
    assert (start, end) == (date(2026, 1, 1), date(2026, 1, 10))


def test_single_day_default() -> None:
    start, end = resolve_range(None, "2026-03-03", 1)
    assert start == end == date(2026, 3, 3)


def test_bad_date_is_bad_args() -> None:
    with pytest.raises(PaceError) as exc:
        parse_date("garbage")
    assert exc.value.code == "bad_args"
    with pytest.raises(PaceError):
        resolve_range(None, "today", 0)
