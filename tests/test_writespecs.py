"""Write specs shared by the CLI write commands and `requests apply`."""

from pace.writespecs import effort_spec, manual_weigh_in_ids


def test_manual_weigh_in_ids() -> None:
    day = {"dateWeightList": [
        {"samplePk": 1, "weight": 66300.0, "sourceType": "MANUAL"},
        {"samplePk": 2, "weight": 66450.0, "sourceType": "MANUAL"},  # 150 g off
        {"samplePk": 3, "weight": 66300.0, "sourceType": "SCALE"},
        {"samplePk": 4, "weight": 66250.0},  # no source: kept
        "not a dict",
        {"weight": 66300.0, "sourceType": "MANUAL"},  # no samplePk
    ]}
    assert manual_weigh_in_ids(day, 66.3) == [1, 4]
    assert manual_weigh_in_ids(None, 66.3) == []
    assert manual_weigh_in_ids({}, 66.3) == []
    assert manual_weigh_in_ids({"dateWeightList": "x"}, 66.3) == []


def test_effort_spec_only_given_fields() -> None:
    assert effort_spec(9, 6, None).kwargs["json"] == {"activityId": 9, "summaryDTO": {"directWorkoutRpe": 60}}
    assert effort_spec(9, None, "strong").kwargs["json"]["summaryDTO"] == {"directWorkoutFeel": 75}
    spec = effort_spec(9, 6, "strong")
    assert spec.echo == {"activity_id": 9, "rpe": 6, "feel": "strong"}
    assert spec.kwargs["json"]["summaryDTO"] == {"directWorkoutRpe": 60, "directWorkoutFeel": 75}
