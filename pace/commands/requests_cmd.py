"""`pace requests apply`: run the requests the athlete made on the dashboard page (pace.pagerequests)."""

from datetime import date
from typing import Any

import typer

import pace.auth
from pace.commands._helpers import read_json
from pace.envelope import PaceError, Result, fail, run
from pace.pagerequests import apply_requests
from pace.writes import DRY_RUN_OPT

app = typer.Typer(no_args_is_help=True, help="Requests the athlete made on the dashboard page.")


@app.command("apply")
def apply(file: str = typer.Argument(..., metavar="FILE",
                                     help="JSON list of {id, version, at, op, params}; - reads stdin."),
          dry_run: bool = DRY_RUN_OPT) -> None:
    """Check and apply dashboard requests (shoe.set, effort.set, weight.add), each at most once."""
    cmd = "requests apply"
    try:
        reqs: Any = read_json(file)
        if not isinstance(reqs, list):
            raise PaceError("bad_args", f"{file} must hold a JSON list of requests, not {type(reqs).__name__}.")
        if reqs and not pace.auth.auth_status()["authenticated"]:
            raise PaceError("auth_required", "No Garmin tokens; no request was applied.")
    except PaceError as err:
        fail(cmd, err)
    today = date.today()

    async def work() -> Result:
        return Result(await apply_requests(reqs, dry_run=dry_run, today=today) if reqs else [])

    # No credential prompt: rejected tokens become auth_required, which ends the run.
    with pace.auth.noninteractive():
        run(cmd, {"file": file, "dry_run": dry_run}, work)
