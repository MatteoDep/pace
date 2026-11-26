"""`pace export`: run the dashboard recipe and write the bundle file.

The body is synchronous: every dataset runs its own command, and each command its own `asyncio.run`."""

import json
from pathlib import Path
from typing import Any

import typer

import pace.auth
from pace import envelope
from pace.commands.schema import build_schema
from pace.envelope import PaceError, Result
from pace.exporter import check_argv, run_recipe
from pace.recipe import (DEFAULT_RECIPE, EXPORT_MAX_CALLS, EXPORT_MAX_SECONDS, Dataset, bundle_path, parse_recipe,
                         recipe_path)
from pace.store import save_json

CMD = "export"


def _label(path: Path) -> str:
    """The recipe path as the bundle shows it: the home directory written as `~`."""
    path = path.absolute()
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def _recipe(path: Path, given: bool) -> tuple[list[Dataset], str]:
    """The parsed and argv-checked recipe and its label; the built-in default when the default file is missing."""
    if not path.exists() and not given:
        obj, label = DEFAULT_RECIPE, "default"
    else:
        try:
            obj, label = json.loads(path.read_text(encoding="utf-8")), _label(path)
        except (OSError, ValueError) as exc:
            raise PaceError("bad_args", f"recipe {path}: cannot read it as JSON ({exc})") from None
    datasets = parse_recipe(obj, {c["name"]: c for c in build_schema(None)["commands"]})
    check_argv(datasets)
    return datasets, label


def _previous(path: Path) -> dict | None:
    """The previous bundle, or None when it is missing, unreadable or not a JSON object."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _work(rpath: Path, given: bool, out: Path, max_calls: int, max_seconds: int, check: bool,
          init: bool) -> dict[str, Any]:
    if init:
        if rpath.exists():
            raise PaceError("bad_args", f"{rpath} exists; edit it, or move it away to start from the default.")
        save_json(rpath, DEFAULT_RECIPE, sort_keys=False)
        return {"path": str(rpath), "datasets": len(DEFAULT_RECIPE["datasets"])}
    datasets, label = _recipe(rpath, given)
    if check:
        return {"recipe": label, "datasets": [ds.id for ds in datasets], "valid": True}
    if max_calls < 1 or max_seconds < 1:
        raise PaceError("bad_args", "--max-calls and --max-seconds must be at least 1.")
    if not pace.auth.auth_status()["authenticated"]:
        raise PaceError("auth_required", "No Garmin tokens; nothing was exported.")
    bundle, summary = run_recipe(datasets, previous=_previous(out), max_calls=max_calls, max_seconds=max_seconds)
    bundle["meta"].append({"label": "Recipe", "value": label})
    save_json(out, bundle, sort_keys=False)
    return {"path": str(out), "recipe": label, "bytes": out.stat().st_size, **summary}


def export(
    recipe: str | None = typer.Option(None, "--recipe", help="Recipe file (default $PACE_HOME/dashboard/recipe.json; "
                                                              "the built-in default recipe when that file is missing)."),
    out: str | None = typer.Option(None, "--out", help="Bundle file (default $PACE_HOME/dashboard/bundle.json)."),
    max_calls: int = typer.Option(EXPORT_MAX_CALLS, "--max-calls", help="Garmin call budget; cache hits are free."),
    max_seconds: int = typer.Option(EXPORT_MAX_SECONDS, "--max-seconds", help="Time budget in seconds."),
    check: bool = typer.Option(False, "--check", help="Validate the recipe without network calls; write nothing."),
    init: bool = typer.Option(False, "--init", help="Write the default recipe to --recipe or the default path; "
                                                    "refused when the file exists."),
) -> None:
    """Run the dashboard recipe and write the bundle file."""
    rpath = Path(recipe).expanduser() if recipe else recipe_path()
    opath = Path(out).expanduser() if out else bundle_path()
    args = {"recipe": str(rpath), "out": str(opath), "max_calls": max_calls, "max_seconds": max_seconds,
            "check": check, "init": init}
    try:
        data = _work(rpath, recipe is not None, opath, max_calls, max_seconds, check, init)
    except KeyboardInterrupt:
        envelope.emit(envelope.error(CMD, PaceError("internal", "Aborted by user.")))
        raise typer.Exit(130) from None
    except PaceError as err:
        envelope.fail(CMD, err)
    except Exception as exc:  # noqa: BLE001 -- stdout stays one JSON document
        envelope.fail(CMD, PaceError("internal", f"{type(exc).__name__}: {exc}"))
    envelope.emit(envelope.ok(CMD, args, Result(data)))
