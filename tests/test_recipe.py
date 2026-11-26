import click
import pytest

from pace.commands.schema import build_schema
from pace.envelope import PaceError
from pace.exporter import click_command
from pace.recipe import DEFAULT_RECIPE, REFUSED_FLAGS, Foreach, parse_recipe, placeholder

COMMANDS = {c["name"]: c for c in build_schema(None)["commands"]}


def _ds(**kw):
    return {"id": "rhr", "command": "wellness sleep-range", "argv": ["--days", "7"],
            "columns": {"date": "date"}, **kw}


def test_default_recipe_is_valid() -> None:
    assert [d.id for d in parse_recipe(DEFAULT_RECIPE, COMMANDS)][:2] == ["load", "rhr"]


@pytest.mark.parametrize("bad, needle", [
    ({"version": 2, "datasets": [_ds()]}, "version"),
    ({"version": 1, "datasets": []}, "datasets"),
    ({"version": 1, "datasets": [_ds(), _ds()]}, "duplicate"),
    ({"version": 1, "datasets": [_ds(id="a b")]}, "id"),
    ({"version": 1, "datasets": [_ds(extra=1)]}, "extra"),
    ({"version": 1, "datasets": [_ds(columns={})]}, "columns"),
    # the runner adds parent.<col> keys to foreach rows, so column names may not collide with them
    ({"version": 1, "datasets": [_ds(columns={"": "date"})]}, "column name ''"),
    ({"version": 1, "datasets": [_ds(columns={"parent.uuid": "date"})]}, "parent."),
    ({"version": 1, "datasets": [_ds(argv=["{date}"])]}, "foreach"),
    ({"version": 1, "datasets": [_ds(id="c", command="activity show", argv=["{id}"],
                                     foreach={"dataset": "later", "limit": 5})]}, "later"),
    ({"version": 1, "datasets": [_ds(), _ds(id="c", command="activity show", argv=["{nope}"],
                                            foreach={"dataset": "rhr", "limit": 5})]}, "nope"),
    ({"version": 1, "datasets": [_ds(), _ds(id="c", command="activity show", argv=["x{date}"],
                                            foreach={"dataset": "rhr", "limit": 5})]}, "{"),
    ({"version": 1, "datasets": [_ds(), _ds(id="c", command="activity show", argv=["{date}"],
                                            foreach={"dataset": "rhr", "limit": 0})]}, "limit"),
    ({"version": 1, "datasets": [_ds(), _ds(id="c", command="activity show", argv=["x"],
                                            foreach={"dataset": "rhr", "limit": 5})]}, "placeholder"),
    # `$` would accept a trailing newline: the whole string must match
    ({"version": 1, "datasets": [_ds(id="rhr\n")]}, "datasets[0]: id"),
    ({"version": 1, "datasets": [_ds(), _ds(id="c", command="activity show", argv=["{date}", "{date}\n"],
                                            foreach={"dataset": "rhr", "limit": 5})]}, "allowed only"),
    # an unhashable foreach.dataset is a recipe mistake, not an internal error
    ({"version": 1, "datasets": [_ds(), _ds(id="c", command="activity show", argv=["{date}"],
                                            foreach={"dataset": ["rhr"], "limit": 5})]}, "c: foreach dataset"),
    ({"version": 1, "datasets": [_ds(), _ds(id="c", command="activity show", argv=["{date}"],
                                            foreach={"dataset": {"id": "rhr"}, "limit": 5})]}, "c: foreach dataset"),
])
def test_recipe_rules(bad, needle) -> None:
    with pytest.raises(PaceError) as e:
        parse_recipe(bad, COMMANDS)
    assert e.value.code == "bad_args" and needle in e.value.message


@pytest.mark.parametrize("command, argv", [
    ("gear link", []), ("zones estimate", []), ("diary show", []), ("chart", []),
    ("load", ["--export", "x.json"]), ("load", ["--export=x.json"]), ("wellness sleep-range", ["--no-cache"]),
    ("activity list", ["--help"]),
])
def test_allowlist_refuses_writes_and_writing_flags(command, argv) -> None:
    with pytest.raises(PaceError) as e:
        parse_recipe({"version": 1, "datasets": [_ds(command=command, argv=argv)]}, COMMANDS)
    assert e.value.code == "bad_args"


def test_allowlist_unmarked_command_is_refused() -> None:
    cmds = {**COMMANDS, "new thing": {"name": "new thing", "exportable": False}}
    with pytest.raises(PaceError):
        parse_recipe({"version": 1, "datasets": [_ds(command="new thing")]}, cmds)


def test_foreach_placeholders() -> None:
    ds = parse_recipe({"version": 1, "datasets": [
        _ds(id="acts", command="activity list", argv=["--limit", "3"], columns={"id": "activityId"}),
        _ds(id="eff", command="activity show", argv=["{id}"], foreach={"dataset": "acts", "limit": 3},
            columns={"rpe": "rpe"})]}, COMMANDS)
    assert ds[1].placeholders == ("id",) and ds[1].foreach == Foreach("acts", 3)


def test_placeholder_is_the_whole_item() -> None:
    assert placeholder("{uuid}") == "uuid"
    assert placeholder("{uuid}\n") is None and placeholder("x{uuid}") is None and placeholder("{}") is None


def _exportable_commands() -> dict[str, click.Command]:
    names = [c["name"] for c in COMMANDS.values() if c.get("exportable")]
    assert len(names) > 30  # the walk below must cover the real list
    return {n: click_command(n) for n in names}


def test_refused_flags_have_no_other_spelling() -> None:
    """A refused flag is refused by its text; a short alias or a secondary name (`--out/-o`,
    `--save/--no-save`) would let the same option through."""
    seen = set()
    for name, cmd in _exportable_commands().items():
        for p in cmd.get_params(click.Context(cmd)):
            spellings = [*p.opts, *p.secondary_opts]
            refused = [o for o in spellings if o in REFUSED_FLAGS]
            if refused:
                assert spellings == refused, f"{name}: {spellings}"
                seen.update(refused)
    assert {"--export", "--no-cache", "--help"} <= seen


def test_exportable_commands_have_no_other_output_option() -> None:
    """Every option of an exportable command that names or opens a file, or is named like an
    output, is a refused flag; else a recipe could write a file or a store."""
    outputs = ("--out", "--export", "--save")
    for name, cmd in _exportable_commands().items():
        for p in cmd.get_params(click.Context(cmd)):
            spellings = [*p.opts, *p.secondary_opts]
            writes = (isinstance(p.type, (click.Path, click.File))
                      or any(o.startswith(outputs) for o in spellings))
            if writes:
                assert isinstance(p, click.Option) and all(o in REFUSED_FLAGS for o in spellings), (
                    f"{name}: {spellings}")
