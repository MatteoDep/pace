"""Garmin activity type keys -> session sport families."""

import pytest

from pace.sports import PUSHABLE, SESSION_SPORTS, UNTRACKED, family


@pytest.mark.parametrize("key,expected", [
    ("running", "running"), ("treadmill_running", "running"), ("trail_running", "running"),
    ("cycling", "cycling"), ("indoor_cycling", "cycling"), ("e_bike_mountain", "cycling"),
    ("e_bike_fitness", "cycling"), ("lap_swimming", "swimming"), ("walking", "walking"),
    ("hiking", "hiking"), ("strength_training", "strength"), ("yoga", "mobility"),
    ("skate_skiing_ws", "other"), ("cross_country_skiing_ws", "other"), ("indoor_cardio", "other"),
    ("surfing_v2", "other"), (None, "other"), ("unknown_thing", "other"),
])
def test_family(key, expected) -> None:
    assert family(key) == expected


def test_groups_are_consistent() -> None:
    assert set(PUSHABLE) | set(UNTRACKED) == set(SESSION_SPORTS)
    assert not set(PUSHABLE) & set(UNTRACKED)
