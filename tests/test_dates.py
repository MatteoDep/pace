from datetime import date, timedelta

import pytest

from pace.dates import parse_date, resolve_range, sample_days
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


def test_sample_days_grid_and_last_day() -> None:
    s, e = date(2026, 11, 1), date(2026, 11, 26)
    got = sample_days(s, e, 7)
    assert got[-1] == e and all(d.toordinal() % 7 == 0 for d in got[:-1])
    assert got == sorted(set(got)) and all(s <= d <= e for d in got)
    assert sample_days(s, e, 1) == [s + timedelta(days=i) for i in range(26)]
    assert sample_days(e, e, 7) == [e]


def test_sample_days_grid_is_stable_day_to_day() -> None:
    s, e = date(2026, 8, 1), date(2026, 11, 26)
    today = set(sample_days(s, e, 7))
    tomorrow = set(sample_days(s + timedelta(1), e + timedelta(1), 7))
    assert len(tomorrow - today) <= 2   # the new last day, plus a grid day it may cross


def test_sample_days_rejects_zero() -> None:
    with pytest.raises(PaceError):
        sample_days(date(2026, 1, 1), date(2026, 1, 2), 0)
