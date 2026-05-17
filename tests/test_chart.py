"""Tests for sparkline + bar helpers."""

from pace.render.chart import bar_row, sparkline


def test_sparkline_basic() -> None:
    s = sparkline([1, 2, 3, 4, 5, 6, 7, 8])
    assert len(s) == 8
    assert " " not in s


def test_sparkline_none_renders_space() -> None:
    s = sparkline([1, None, 3])
    assert s[1] == " "


def test_sparkline_constant_series() -> None:
    s = sparkline([5, 5, 5])
    assert len(s) == 3
    assert len(set(s)) == 1


def test_sparkline_bucket_to_width() -> None:
    s = sparkline(list(range(100)), width=10)
    assert len(s) == 10


def test_bar_row_proportional() -> None:
    assert bar_row(50, 100, width=10) == "█████·····"
    assert bar_row(0, 100, width=10) == "··········"
    assert bar_row(100, 100, width=10) == "██████████"
