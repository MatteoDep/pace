"""Compact workout DSL -> Garmin workout JSON."""

import pytest

from pace.envelope import PaceError
from pace.workout import (
    DSL_REFERENCE,
    build_workout,
    estimate_duration_s,
    parse_distance,
    parse_duration,
    parse_pace,
    parse_range,
)


def test_parse_duration() -> None:
    assert parse_duration("45s", "p") == 45
    assert parse_duration("10m", "p") == 600
    assert parse_duration("1h", "p") == 3600
    assert parse_duration("1h30m", "p") == 5400
    assert parse_duration("1h05m30s", "p") == 3930
    for bad in ("", "10", "10x", "-5m", "1m1h"):
        with pytest.raises(PaceError) as exc:
            parse_duration(bad, "steps[0].duration")
        assert exc.value.code == "bad_args" and exc.value.message.startswith("steps[0].duration")


def test_parse_distance() -> None:
    assert parse_distance("400m", "p") == 400.0
    assert parse_distance("1km", "p") == 1000.0
    assert parse_distance("5.5km", "p") == 5500.0
    assert parse_distance("1.2 km", "p") == 1200.0
    with pytest.raises(PaceError):
        parse_distance("5", "p")


def test_parse_pace_and_range() -> None:
    assert parse_pace("4:00", "p") == pytest.approx(1000 / 240)
    assert parse_pace("5:30", "p") == pytest.approx(1000 / 330)
    assert parse_range("140-150", "p", "hr") == (140.0, 150.0)
    assert parse_range("150", "p", "hr") == (150.0, 150.0)
    with pytest.raises(PaceError):
        parse_range("150-140", "p", "hr")
    with pytest.raises(PaceError):
        parse_pace("4-15", "p")


def test_parse_pace_rejects_out_of_range_seconds() -> None:
    for bad in ("4:75", "0:60"):
        with pytest.raises(PaceError) as exc:
            parse_pace(bad, "p")
        assert exc.value.code == "bad_args"


def test_parse_duration_rejects_zero() -> None:
    for bad in ("0s", "0m"):
        with pytest.raises(PaceError) as exc:
            parse_duration(bad, "p")
        assert exc.value.code == "bad_args"
        assert exc.value.message == "p: duration must be positive"


def _dsl():
    return {
        "name": "Tempo 3x10", "sport": "running",
        "steps": [
            {"type": "warmup", "duration": "15m"},
            {"repeat": 3, "steps": [
                {"type": "interval", "duration": "10m", "target": {"pace": "4:15-4:25"}},
                {"type": "recovery", "duration": "3m"}]},
            {"type": "cooldown", "distance": "1km"},
        ],
    }


def test_build_workout_structure() -> None:
    w = build_workout(_dsl())
    assert w["workoutName"] == "Tempo 3x10"
    assert w["sportType"] == {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
    seg = w["workoutSegments"][0]
    assert seg["segmentOrder"] == 1 and seg["sportType"] == w["sportType"]
    steps = seg["workoutSteps"]
    assert [s["stepOrder"] for s in steps] == [1, 2, 5]
    warm = steps[0]
    assert warm["type"] == "ExecutableStepDTO"
    assert warm["stepType"] == {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1}
    assert warm["endCondition"]["conditionTypeKey"] == "time" and warm["endConditionValue"] == 900.0
    assert warm["targetType"]["workoutTargetTypeKey"] == "no.target"
    rep = steps[1]
    assert rep["type"] == "RepeatGroupDTO" and rep["numberOfIterations"] == 3
    assert rep["endCondition"] == {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}
    assert rep["endConditionValue"] == 3.0
    assert [s["stepOrder"] for s in rep["workoutSteps"]] == [3, 4]
    cool = steps[2]
    assert cool["endCondition"]["conditionTypeKey"] == "distance" and cool["endCondition"]["conditionTypeId"] == 3
    assert cool["endConditionValue"] == 1000.0
    assert w["estimatedDurationInSecs"] == estimate_duration_s(_dsl()["steps"])


def test_pace_target_faster_bound_first() -> None:
    w = build_workout(_dsl())
    iv = w["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][0]
    assert iv["targetType"] == {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6}
    assert iv["targetValueOne"] == pytest.approx(1000 / 255)   # 4:15 -> faster -> higher m/s
    assert iv["targetValueTwo"] == pytest.approx(1000 / 265)   # 4:25
    assert iv["zoneNumber"] is None


def test_pace_target_faster_bound_first_regardless_of_input_order() -> None:
    dsl = {"name": "x", "sport": "running", "steps": [
        {"type": "interval", "duration": "1m", "target": {"pace": "4:25-4:15"}}]}
    iv = build_workout(dsl)["workoutSegments"][0]["workoutSteps"][0]
    assert iv["targetValueOne"] > iv["targetValueTwo"]
    assert iv["targetValueOne"] == pytest.approx(1000 / 255)
    assert iv["targetValueTwo"] == pytest.approx(1000 / 265)


def test_pace_target_single_value_gives_equal_bounds() -> None:
    dsl = {"name": "x", "sport": "running", "steps": [
        {"type": "interval", "duration": "1m", "target": {"pace": "4:30"}}]}
    iv = build_workout(dsl)["workoutSegments"][0]["workoutSteps"][0]
    assert iv["targetValueOne"] == iv["targetValueTwo"] == pytest.approx(1000 / 270)


def test_note_maps_to_description() -> None:
    dsl = {"name": "x", "sport": "running", "steps": [
        {"type": "warmup", "duration": "1m", "note": "easy does it"}]}
    step = build_workout(dsl)["workoutSegments"][0]["workoutSteps"][0]
    assert step["description"] == "easy does it"


def test_nested_repeat_stepOrder_is_depth_first_contiguous() -> None:
    dsl = {"name": "x", "sport": "running", "steps": [
        {"type": "warmup", "duration": "1m"},
        {"repeat": 2, "steps": [
            {"repeat": 2, "steps": [
                {"type": "interval", "duration": "1m"},
                {"type": "recovery", "duration": "1m"},
            ]},
            {"type": "interval", "duration": "1m"},
        ]},
        {"type": "cooldown", "duration": "1m"},
    ]}
    steps = build_workout(dsl)["workoutSegments"][0]["workoutSteps"]

    seen: list[tuple[str, int]] = []

    def walk(items: list[dict]) -> None:
        for s in items:
            kind = "repeat" if s["type"] == "RepeatGroupDTO" else "exec"
            seen.append((kind, s["stepOrder"]))
            if kind == "repeat":
                walk(s["workoutSteps"])

    walk(steps)
    assert [order for _, order in seen] == [1, 2, 3, 4, 5, 6, 7]
    assert [kind for kind, _ in seen] == ["exec", "repeat", "repeat", "exec", "exec", "exec", "exec"]


def test_other_targets_and_open_end() -> None:
    dsl = {"name": "mix", "sport": "cycling", "steps": [
        {"type": "interval", "open": True, "target": {"hr": "z2"}},
        {"type": "interval", "duration": "5m", "target": {"hr": "140-150"}},
        {"type": "interval", "duration": "5m", "target": {"power": "200-220"}},
        {"type": "rest", "duration": "1m", "target": {"cadence": "85-95"}},
    ]}
    steps = build_workout(dsl)["workoutSegments"][0]["workoutSteps"]
    z2 = steps[0]
    assert z2["endCondition"] == {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
    assert z2["endConditionValue"] == 0.0
    assert z2["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone" and z2["zoneNumber"] == 2
    assert z2["targetValueOne"] is None and z2["targetValueTwo"] is None
    bpm = steps[1]
    assert bpm["targetType"]["workoutTargetTypeId"] == 4 and (bpm["targetValueOne"], bpm["targetValueTwo"]) == (140.0, 150.0)
    assert bpm["zoneNumber"] is None
    assert steps[2]["targetType"]["workoutTargetTypeKey"] == "power.zone" and steps[2]["targetValueTwo"] == 220.0
    assert steps[3]["stepType"]["stepTypeKey"] == "rest"
    assert steps[3]["targetType"]["workoutTargetTypeKey"] == "cadence.zone" and steps[3]["targetValueOne"] == 85.0


def test_estimate_duration() -> None:
    assert estimate_duration_s(_dsl()["steps"]) == 900 + 3 * (600 + 180) + 0   # distance cooldown has no pace -> 0
    with_pace = [{"type": "interval", "distance": "1km", "target": {"pace": "4:00-5:00"}}]
    assert estimate_duration_s(with_pace) == 270          # 1000 m at midpoint 4:30/km


@pytest.mark.parametrize("dsl, path", [
    ({"sport": "running", "steps": []}, "name"),
    ({"name": "x", "sport": "rowing", "steps": [{"type": "warmup", "duration": "1m"}]}, "sport"),
    ({"name": "x", "sport": "running", "steps": []}, "steps"),
    ({"name": "x", "sport": "running", "steps": [{"type": "sprint", "duration": "1m"}]}, "steps[0].type"),
    ({"name": "x", "sport": "running", "steps": [{"type": "warmup"}]}, "steps[0]"),
    ({"name": "x", "sport": "running", "steps": [{"type": "warmup", "duration": "1m", "distance": "1km"}]}, "steps[0]"),
    ({"name": "x", "sport": "running", "steps": [{"type": "warmup", "duration": "1m", "target": {"pace": "4:00", "hr": "z2"}}]}, "steps[0].target"),
    ({"name": "x", "sport": "running", "steps": [{"type": "warmup", "duration": "1m", "target": {"hr": "z9"}}]}, "steps[0].target.hr"),
    ({"name": "x", "sport": "running", "steps": [{"repeat": 0, "steps": [{"type": "interval", "duration": "1m"}]}]}, "steps[0].repeat"),
    ({"name": "x", "sport": "running", "steps": [{"repeat": 2, "steps": [{"type": "interval", "duration": "1x"}]}]}, "steps[0].steps[0].duration"),
    ({"nme": "x", "sport": "running", "steps": [{"type": "warmup", "duration": "1m"}]}, "<root>"),
    ({"name": "x", "sport": "running", "steps": [{"type": "interval", "duration": "5m", "targets": {"pace": "4:00"}}]}, "steps[0]"),
    ({"name": "x", "sport": "running", "steps": [{"type": "warmup", "duration": "1m", "target": {"paace": "4:00"}}]}, "steps[0].target"),
    ({"name": "x", "sport": "running", "steps": [{"repeat": 2, "steps": [{"type": "interval", "duration": "1m"}], "bogus": 1}]}, "steps[0]"),
])
def test_invalid_dsl_names_path(dsl, path) -> None:
    with pytest.raises(PaceError) as exc:
        build_workout(dsl)
    assert exc.value.code == "bad_args"
    assert exc.value.message.startswith(path), exc.value.message


def test_unknown_key_message_names_the_offending_key_step() -> None:
    dsl = {"name": "x", "sport": "running", "steps": [
        {"type": "interval", "duration": "5m", "targets": {"pace": "4:00"}}]}
    with pytest.raises(PaceError) as exc:
        build_workout(dsl)
    assert exc.value.message.startswith("steps[0]")
    assert "'targets'" in exc.value.message


def test_unknown_key_message_names_the_offending_key_top_level() -> None:
    dsl = {"nme": "x", "sport": "running", "steps": [{"type": "warmup", "duration": "1m"}]}
    with pytest.raises(PaceError) as exc:
        build_workout(dsl)
    assert exc.value.message.startswith("<root>")
    assert "'nme'" in exc.value.message


def test_dsl_reference_is_json_friendly() -> None:
    import json
    doc = json.loads(json.dumps(DSL_REFERENCE))
    assert set(doc["sports"]) == {"running", "cycling", "swimming", "walking", "hiking"}
    assert "example" in doc and doc["example"]["steps"]
