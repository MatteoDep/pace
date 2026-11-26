import garminconnect

from pace.spec import GROUPS, REGISTRY, specs_for


def test_every_method_exists_on_garmin_client() -> None:
    missing = [s.command for s in REGISTRY if not hasattr(garminconnect.Garmin, s.method)]
    assert missing == []


def test_commands_unique_and_groups_known() -> None:
    names = [s.command for s in REGISTRY]
    assert len(names) == len(set(names))
    assert {s.group for s in REGISTRY} <= set(GROUPS)


def test_specs_for_group() -> None:
    assert [s.name for s in specs_for("wellness")][:2] == ["summary", "sleep"]
    assert specs_for("nope") == []


def test_series_paths_are_dotted_strings() -> None:
    for s in REGISTRY:
        for ser in s.series:
            assert ser.path and " " not in ser.path


def test_sentinel_series_declare_min_value() -> None:
    """Stress and body battery carry -1 / -2 sentinels; they must never reach a bucket."""
    sentinel_paths = {"stressValuesArray", "bodyBatteryValuesArray"}
    checked = 0
    for spec in REGISTRY:
        for ser in spec.series:
            if ser.path in sentinel_paths:
                assert ser.min_value == 0, f"{spec.command} {ser.path} has no min_value"
                checked += 1
    assert checked == 3


def test_registry_methods_are_reads() -> None:
    """Every registry command is exportable by being in the registry, so each one must be a read."""
    assert [s.command for s in REGISTRY if not s.method.startswith("get_")] == []


def test_race_predictions_help_names_the_range() -> None:
    # `pace schema` is the coach's authority on what a command returns
    spec = next(s for s in REGISTRY if s.command == "training race-predictions")
    assert "the latest" in spec.help and "one row per day over --from/--to/--days" in spec.help
