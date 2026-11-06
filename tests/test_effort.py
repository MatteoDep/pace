"""RPE/feel scale mapping between the user's scale and Garmin's stored values."""

import pytest

from pace.effort import FEEL_LEVELS, feel_from_stored, parse_feel, parse_rpe, rpe_from_stored, with_effort
from pace.envelope import PaceError


def test_feel_levels_order_and_values() -> None:
    assert list(FEEL_LEVELS.items()) == [("very-weak", 0), ("weak", 25), ("normal", 50), ("strong", 75),
                                         ("very-strong", 100)]


def test_parse_rpe_bounds() -> None:
    assert parse_rpe(1) == 10 and parse_rpe(10) == 100
    for bad in (0, 11, -3):
        with pytest.raises(PaceError) as e:
            parse_rpe(bad)
        assert e.value.code == "bad_args"


def test_parse_feel_labels_and_aliases() -> None:
    assert parse_feel("strong") == ("strong", 75)
    assert parse_feel("Very_Weak") == ("very-weak", 0)
    with pytest.raises(PaceError) as e:
        parse_feel("tired")
    assert e.value.code == "bad_args" and "very-strong" in str(e.value)


def test_from_stored_on_and_off_grid() -> None:
    assert rpe_from_stored(40) == 4 and isinstance(rpe_from_stored(40), int)
    assert rpe_from_stored(45) == 4.5
    assert rpe_from_stored(None) is None and rpe_from_stored("x") is None and rpe_from_stored(True) is None
    assert feel_from_stored(75) == "strong" and feel_from_stored(60) is None and feel_from_stored(None) is None


def test_with_effort() -> None:
    a = {"activityId": 1, "summaryDTO": {"directWorkoutRpe": 40, "directWorkoutFeel": 75}}
    out = with_effort(a)
    assert out["rpe"] == 4 and out["feel"] == "strong" and "rpe" not in a
    assert with_effort({"activityId": 2}) == {"activityId": 2, "rpe": None, "feel": None}
    assert with_effort({"activityId": 3, "summaryDTO": None}) == {"activityId": 3, "summaryDTO": None,
                                                                   "rpe": None, "feel": None}
    assert with_effort([1]) == [1]
