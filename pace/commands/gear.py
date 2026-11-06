"""Gear commands: reads generated from pace.spec.REGISTRY, plus link/unlink writes."""

import re

import typer

from pace.envelope import PaceError, fail, run
from pace.factory import build_group
from pace.writes import DRY_RUN_OPT, WriteSpec, run_write

app = build_group("gear")

_UUID = re.compile(r"[0-9a-f]{32}")


def validate_uuid(value: str) -> str:
    """Garmin gear uuids are 32 lowercase hex chars; the value is interpolated into a URL path."""
    norm = value.strip().lower()
    if not _UUID.fullmatch(norm):
        raise PaceError("bad_args", f"GEAR_UUID must be 32 hex chars (see `pace gear list`): {value!r}")
    return norm


def _link_command(verb: str, method: str, help_: str) -> None:
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
        # Gear-side reads are keyed by the uuid as typed (any case), and Garmin may touch another
        # shoe on link: drop the whole gear-side prefixes, they are a handful of rows.
        spec = WriteSpec(resource="activity_gear", action=verb, method=method, args=(uuid, activity_id),
                         payload={"method": method, "gearUUID": uuid, "activity_id": activity_id},
                         echo={"activity_id": activity_id, "gear_uuid": uuid},
                         invalidate=(f"get_activity_gear:{activity_id}", "get_gear_activities:", "get_gear_stats:"))
        run(cmd, {"activity_id": activity_id, "gear_uuid": uuid, "dry_run": dry_run},
            lambda: run_write(spec, dry_run=dry_run))

    command.__name__ = verb
    command.__doc__ = help_
    app.command(verb, help=help_)(command)


_link_command("link", "add_gear_to_activity", "Link one gear item to one activity (other linked gear is kept).")
_link_command("unlink", "remove_gear_from_activity", "Unlink one gear item from one activity.")
