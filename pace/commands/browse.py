"""`pace browse` -- interactive TUI."""

from enum import Enum

import typer


class BrowseTarget(str, Enum):
    ACTIVITY = "activity"


browse_app = typer.Typer(no_args_is_help=True, help="Interactive terminal UI.")


@browse_app.command("activity")
def activity(
    limit: int = typer.Option(50, "--limit", "-n", help="Activities to load (page 1)."),
) -> None:
    """Browse recent activities; press Enter on a row for the full detail tree."""
    from pace.tui.app import run_tui  # lazy import keeps `--help` fast

    run_tui(target=BrowseTarget.ACTIVITY.value, limit=limit)
