"""Gear commands: reads generated from pace.spec.REGISTRY, plus link/unlink writes."""

import typer

from pace.envelope import PaceError, fail, run
from pace.factory import build_group
from pace.ids import validate_uuid
from pace.writes import DRY_RUN_OPT, run_write
from pace.writespecs import gear_link_spec

app = build_group("gear")

def _link_command(verb: str, help_: str) -> None:
    def command(activity_id: int = typer.Argument(..., metavar="ACTIVITY_ID"),
                gear_uuid: str = typer.Argument(..., metavar="GEAR_UUID"),
                dry_run: bool = DRY_RUN_OPT) -> None:
        cmd = f"gear {verb}"
        try:
            if activity_id <= 0:
                raise PaceError("bad_args", f"ACTIVITY_ID must be positive: {activity_id}")
            uuid = validate_uuid(gear_uuid)
        except PaceError as err:
            fail(cmd, err)
        spec = gear_link_spec(verb, activity_id, uuid)
        run(cmd, {"activity_id": activity_id, "gear_uuid": uuid, "dry_run": dry_run},
            lambda: run_write(spec, dry_run=dry_run))

    command.__name__ = verb
    command.__doc__ = help_
    app.command(verb, help=help_)(command)


_link_command("link", "Link one gear item to one activity (other linked gear is kept).")
_link_command("unlink", "Unlink one gear item from one activity.")
