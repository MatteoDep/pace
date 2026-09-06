"""Discover third-party command groups registered under the `pace.commands` entry-point.

A plugin's pyproject.toml declares:

    [project.entry-points."pace.commands"]
    myplugin = "myplugin.cli:app"

The referenced object must be a `typer.Typer`. Import errors are logged (visible with
`--verbose`) but never abort startup, and never break the one-JSON-document contract.
"""

from importlib.metadata import entry_points

import typer

from pace.envelope import log

_GROUP = "pace.commands"


def load_plugins(app: typer.Typer) -> None:
    """Attach every discovered plugin Typer sub-app onto `app`."""
    for ep in entry_points(group=_GROUP):
        try:
            obj = ep.load()
        except Exception as err:
            log(f"pace: plugin {ep.name!r} failed to load: {err}")
            continue

        if not isinstance(obj, typer.Typer):
            log(f"pace: plugin {ep.name!r} did not expose a typer.Typer instance.")
            continue

        app.add_typer(obj, name=ep.name, help=f"[plugin] {ep.value}")
