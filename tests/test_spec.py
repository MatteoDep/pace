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
