"""Tests for threshold/color helpers."""

from pace.render.colors import Threshold, threshold_color


def test_higher_better() -> None:
    t = Threshold(warn=25, good=75, higher_better=True)
    assert threshold_color(80, t) == "green"
    assert threshold_color(50, t) == "yellow"
    assert threshold_color(10, t) == "red"
    assert threshold_color(None, t) == "dim"


def test_lower_better() -> None:
    t = Threshold(warn=70, good=55, higher_better=False)
    assert threshold_color(50, t) == "green"
    assert threshold_color(60, t) == "yellow"
    assert threshold_color(80, t) == "red"
