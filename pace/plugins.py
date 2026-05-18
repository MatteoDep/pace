"""Discover third-party command groups registered under the `pace.commands` entry-point.

A plugin's pyproject.toml declares:

    [project.entry-points."pace.commands"]
    myplugin = "myplugin.cli:app"

The referenced object must be a `typer.Typer`. Import errors are reported on
stderr but never abort startup.
"""

import sys
from importlib.metadata import entry_points

import typer

_GROUP = "pace.commands"


def load_plugins(app: typer.Typer) -> None:
    """Attach every discovered plugin Typer sub-app onto `app`."""
    for ep in entry_points(group=_GROUP):
        try:
            obj = ep.load()
        except Exception as err:
            print(f"pace: plugin {ep.name!r} failed to load: {err}", file=sys.stderr)
            continue

        if not isinstance(obj, typer.Typer):
            print(
                f"pace: plugin {ep.name!r} did not expose a typer.Typer instance.",
                file=sys.stderr,
            )
            continue

        app.add_typer(obj, name=ep.name, help=f"[plugin] {ep.value}")
