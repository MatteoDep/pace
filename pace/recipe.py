"""Dashboard recipe: the data model and the rules that keep `pace export` read-only and non-leaking."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pace import config
from pace.envelope import PaceError

RECIPE_VERSION = 1
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")  # use fullmatch: `$` would accept a trailing newline
MAX_DATASETS = 50
MAX_FOREACH_LIMIT = 100
EXPORT_MAX_CALLS = 60
EXPORT_MAX_SECONDS = 480

#: Flags that write a file, bypass the cache or print help: never allowed in a recipe argv.
#: Kept here for validation; `pace schema` publishes the same tuple.
REFUSED_FLAGS = ("--export", "--out", "--out-dir", "--save", "--no-cache", "--help")

_DATASET_KEYS = {"id", "command", "argv", "rows", "columns", "foreach"}
_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")  # use fullmatch, as for ID_RE


@dataclass(frozen=True)
class Foreach:
    dataset: str
    limit: int


@dataclass(frozen=True)
class Dataset:
    id: str
    command: str
    argv: tuple[str, ...]
    rows: str | None
    columns: dict[str, str]
    foreach: Foreach | None

    @property
    def placeholders(self) -> tuple[str, ...]:
        return tuple(name for a in self.argv if (name := placeholder(a)) is not None)


def placeholder(arg: str) -> str | None:
    """The column a whole `{column}` argv item names, else None."""
    m = _PLACEHOLDER_RE.fullmatch(arg)
    return m.group(1) if m else None


def _bad(where: str, msg: str) -> PaceError:
    return PaceError("bad_args", f"recipe: {where}: {msg}")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_dataset(i: int, raw: Any, commands: dict[str, dict], done: dict[str, Dataset]) -> Dataset:
    where = f"datasets[{i}]"
    if not isinstance(raw, dict):
        raise _bad(where, "must be an object")
    id_ = raw.get("id")
    if not isinstance(id_, str) or not ID_RE.fullmatch(id_):
        raise _bad(where, "id must be 1-64 letters, digits, '_' or '-'")
    where = id_
    if id_ in done:
        raise _bad(where, "duplicate id")
    extra = sorted(set(raw) - _DATASET_KEYS)
    if extra:
        raise _bad(where, f"unknown key(s): {', '.join(extra)}")
    command = raw.get("command")
    entry = commands.get(command) if isinstance(command, str) else None
    if entry is None:
        raise _bad(where, f"unknown command {command!r}")
    if not entry.get("exportable"):
        raise _bad(where, f"command {command!r} is not exportable")
    argv = raw.get("argv", [])
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise _bad(where, "argv must be a list of strings")
    for a in argv:
        if any(a == f or a.startswith(f + "=") for f in REFUSED_FLAGS):
            raise _bad(where, f"argv flag {a!r} is refused in a recipe")
    rows = raw.get("rows")
    if "rows" in raw and (not isinstance(rows, str) or not rows):
        raise _bad(where, "rows must be a non-empty string")
    columns = raw.get("columns")
    if (not isinstance(columns, dict) or not columns
            or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in columns.items())):
        raise _bad(where, "columns must be a non-empty object of column name to path")
    for name in columns:
        if not name or name.startswith("parent."):
            # foreach rows carry parent.<col> keys next to the columns
            raise _bad(where, f"column name {name!r} must be non-empty and not start with 'parent.'")
    foreach = None
    if "foreach" in raw:
        f = raw["foreach"]
        if not isinstance(f, dict) or set(f) != {"dataset", "limit"}:
            raise _bad(where, "foreach must be an object with exactly dataset and limit")
        if not isinstance(f["dataset"], str) or f["dataset"] not in done:
            raise _bad(where, f"foreach dataset {f['dataset']!r} is not defined earlier in the list")
        if not _is_int(f["limit"]) or not 1 <= f["limit"] <= MAX_FOREACH_LIMIT:
            raise _bad(where, f"foreach limit must be an integer 1..{MAX_FOREACH_LIMIT}")
        foreach = Foreach(f["dataset"], f["limit"])
    ds = Dataset(id_, command, tuple(argv), rows, dict(columns), foreach)
    if ds.placeholders and foreach is None:
        raise _bad(where, "placeholders in argv need a foreach")
    if foreach is not None:
        if not ds.placeholders:
            raise _bad(where, "a foreach dataset needs at least one {placeholder} in argv")
        parent = done[foreach.dataset].columns
        for name in ds.placeholders:
            if name not in parent:
                raise _bad(where, f"placeholder {{{name}}} is not a column of {foreach.dataset!r}")
    for a in argv:
        if placeholder(a) is None and ("{" in a or "}" in a):
            raise _bad(where, f"argv item {a!r}: '{{' and '}}' are allowed only as a whole {{column}} item")
    return ds


def parse_recipe(obj: Any, commands: dict[str, dict]) -> list[Dataset]:
    """Validate a recipe object; `commands` maps a command name to its `pace schema` entry."""
    if not isinstance(obj, dict) or set(obj) != {"version", "datasets"}:
        raise _bad("top level", "must be an object with exactly version and datasets")
    if obj["version"] != RECIPE_VERSION or not _is_int(obj["version"]):
        raise _bad("top level", f"version must be {RECIPE_VERSION}")
    raw = obj["datasets"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_DATASETS:
        raise _bad("top level", f"datasets must be a list of 1..{MAX_DATASETS} objects")
    done: dict[str, Dataset] = {}
    for i, r in enumerate(raw):
        ds = _parse_dataset(i, r, commands, done)
        done[ds.id] = ds
    return list(done.values())


_LOAD_COLUMNS = (
    ["table", "week", "start", "sport"]
    + [f"{p}_{m}" for p in ("actual", "planned", "projected", "remaining", "missing") for m in ("km", "elev_m", "hours")]
    + ["date", "projected"]
    + [f"rolling7_{m}" for m in ("km", "elev_m", "hours")]
    + ["title", "intensity", "duration_s", "distance_m", "status"]
)

DEFAULT_RECIPE: dict = {"version": RECIPE_VERSION, "datasets": [
    {"id": "load", "command": "load", "argv": ["--rows"], "columns": {c: c for c in _LOAD_COLUMNS}},
    {"id": "rhr", "command": "wellness sleep-range", "argv": ["--days", "112"],
     "columns": {"date": "date", "rhr": "rhr"}},
    {"id": "weight", "command": "log weight list", "argv": ["--days", "112"], "rows": "dailyWeightSummaries",
     "columns": {"date": "summaryDate", "weight_g": "latestWeight.weight"}},
    {"id": "race_predictions", "command": "training race-predictions", "argv": ["--days", "112"],
     "columns": {"date": "calendarDate", "t5k_s": "time5K", "t10k_s": "time10K",
                 "half_s": "timeHalfMarathon", "marathon_s": "timeMarathon"}},
    {"id": "fitness_age", "command": "training fitness-age", "argv": ["--days", "112", "--every", "7"],
     "columns": {"date": "date", "fitness_age": "fitnessAge", "achievable": "achievableFitnessAge",
                 "age": "chronologicalAge"}},
    {"id": "shoes", "command": "gear list",
     "columns": {"uuid": "uuid", "name": "customMakeModel", "type": "gearTypeName", "status": "gearStatusName"}},
    {"id": "shoe_activities", "command": "gear activities", "argv": ["{uuid}"],
     "foreach": {"dataset": "shoes", "limit": 20},
     "columns": {"activity_id": "activityId", "start": "startTimeLocal"}},
    {"id": "activities", "command": "activity list", "argv": ["--limit", "15"],
     "columns": {"id": "activityId", "name": "activityName", "type": "activityType",
                 "start": "startTimeLocal", "duration_s": "duration", "distance_m": "distance",
                 "avg_hr": "averageHR", "elev_gain_m": "elevationGain", "avg_speed_mps": "averageSpeed"}},
    {"id": "activity_effort", "command": "activity show", "argv": ["{id}"],
     "foreach": {"dataset": "activities", "limit": 15}, "columns": {"rpe": "rpe", "feel": "feel"}},
]}


def recipe_path() -> Path:
    return config.PACE_HOME / "dashboard" / "recipe.json"


def bundle_path() -> Path:
    return config.PACE_HOME / "dashboard" / "bundle.json"
