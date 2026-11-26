"""Run a dashboard recipe: every dataset invokes its own click command in-process, inside one call and
time budget, and a failure falls back to the previous bundle instead of emptying its section."""

import contextlib
import functools
import io
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import click
from typer.main import get_command

from pace import envelope, ratelimit
from pace.envelope import PaceError
from pace.recipe import Dataset, placeholder
from pace.shape import _get_path

#: Longest error message the summary keeps for a dataset or a foreach child.
MESSAGE_MAX = 300


@functools.cache
def _root() -> click.Group:
    import pace.cli  # pace.cli registers `pace export`, which imports this module

    return get_command(pace.cli.app)  # type: ignore[return-value]


def click_command(name: str) -> click.Command:
    """The leaf command for `name` ("wellness sleep-range"); never the root group, whose callback
    would reset --verbose."""
    node: Any = _root()
    for token in name.split():
        node = node.commands[token]
    return node


def check_argv(datasets: list[Dataset]) -> None:
    """Parse each dataset's argv with its command's own parser (placeholders as "0"); no network."""
    for ds in datasets:
        argv = ["0" if placeholder(a) is not None else a for a in ds.argv]
        try:
            click_command(ds.command).make_context(f"pace {ds.command}", argv)
        except click.ClickException as err:
            raise PaceError("bad_args", f"recipe: {ds.id}: {err.format_message()}") from None


def invoke(name: str, argv: list[str]) -> dict:
    """Run `pace <name> <argv>` in-process and return the envelope it printed (its last line)."""
    out = io.StringIO()
    stdin = sys.stdin
    sys.stdin = io.StringIO()  # a credentials prompt must fail, never wait
    try:
        with contextlib.redirect_stdout(out):
            rv = click_command(name).main(args=argv, prog_name=f"pace {name}", standalone_mode=False)
    except click.ClickException as err:
        return envelope.error(name, PaceError("bad_args", err.format_message()))
    except Exception as exc:  # noqa: BLE001 -- one broken dataset must not end the export
        return envelope.error(name, PaceError("internal", f"{type(exc).__name__}: {exc}"))
    finally:
        sys.stdin = stdin
    if rv == 130:  # Typer turns Ctrl-C into Exit(130): stop the whole export
        raise KeyboardInterrupt
    lines = out.getvalue().strip().splitlines()
    try:
        doc = json.loads(lines[-1]) if lines else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        return envelope.error(name, PaceError("internal", "The command printed no envelope."))
    return doc


def _is_failed_day(item: Any) -> bool:
    return (isinstance(item, dict) and set(item) == {"date", "error"}
            and isinstance(item["date"], str) and isinstance(item["error"], dict))


def extract(data: Any, ds: Dataset) -> tuple[list[dict], list[str]]:
    """(rows, failed day ISO dates) of a successful envelope's `data`, cut to the recipe's columns."""
    if ds.rows is not None:
        found, data = _get_path(data, ds.rows)
        if not found:  # pace prunes empty lists, so a range without rows has no key at all
            return [], []
    if data is None:
        return [], []
    rows: list[dict] = []
    failed: list[str] = []
    for item in data if isinstance(data, list) else [data]:
        if _is_failed_day(item):
            failed.append(item["date"])
            continue
        if not isinstance(item, dict):
            item = {"value": item}
        rows.append({col: _get_path(item, path)[1] for col, path in ds.columns.items()})
    return rows, failed


@dataclass
class _Section:
    """One dataset's outcome while it runs."""

    generated: str
    rows: list[dict] = field(default_factory=list)
    stale: bool = False
    reason: str | None = None
    error: dict | None = None  # {code, message}; the bundle keeps the code only
    day_errors: int = 0
    errors: list[dict] = field(default_factory=list)  # foreach children: {parent, code, message}
    ages: list[int] = field(default_factory=list)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _previous(section: Any, ds: Dataset) -> dict | None:
    """The previous bundle's section for `ds`, only when it has the same column names."""
    if not isinstance(section, dict):
        return None
    meta, rows = section.get("meta"), section.get("rows")
    if not isinstance(meta, dict) or meta.get("columns") != list(ds.columns) or not isinstance(rows, list):
        return None
    return {"rows": [r for r in rows if isinstance(r, dict)], "generated": meta.get("generated")}


def _failure(env: dict) -> dict | None:
    """None for an ok envelope, else its {code, message}. auth_required ends the whole export."""
    if env.get("ok") is True:
        return None
    err = env.get("error")
    if not isinstance(err, dict):
        err = {}
    code, message = str(err.get("code") or "internal"), str(err.get("message") or "")
    if code == "auth_required":
        raise PaceError(code, message, err.get("http"))
    return {"code": code, "message": message[:MESSAGE_MAX]}


def _note_cache(env: dict, st: _Section) -> None:
    cache = env.get("cache")
    if isinstance(cache, dict) and isinstance(cache.get("oldest_age_s"), int):
        st.ages.append(cache["oldest_age_s"])


def _merge_days(rows: list[dict], failed: list[str], previous: list[dict], ds: Dataset) -> list[dict]:
    """Put back the previous row of each failed day, matched on the column mapped from `date`."""
    col = next((c for c, path in ds.columns.items() if path == "date"), None)
    if not failed or col is None:
        return rows
    by_date = {r.get(col): r for r in previous}
    kept = [by_date[d] for d in failed if d in by_date]
    return sorted(rows + kept, key=lambda r: str(r.get(col) or "")) if kept else rows


def _run_single(ds: Dataset, prev: dict | None, st: _Section) -> None:
    env = invoke(ds.command, list(ds.argv))
    _note_cache(env, st)
    failure = _failure(env)
    if failure is not None:
        st.stale, st.error = True, failure
        st.reason = "deferred" if failure["code"] == "deferred" else "error"
        if prev is not None:
            st.rows, st.generated = prev["rows"], prev["generated"]
        return
    rows, failed = extract(env.get("data"), ds)
    st.rows = _merge_days(rows, failed, prev["rows"] if prev else [], ds)
    st.day_errors = len(failed)


def _child_argv(ds: Dataset, values: dict[str, Any]) -> list[str] | None:
    """The argv of one foreach child, or None when a placeholder value is null or its string (the
    exact text click would get) starts with "-", so it could be read as a flag (`--export`)."""
    argv = []
    for a in ds.argv:
        name = placeholder(a)
        if name is not None:
            if values[name] is None or str(values[name]).startswith("-"):
                return None
            a = str(values[name])
        argv.append(a)
    return argv


def _run_children(ds: Dataset, parent_rows: list[dict], prev: dict | None, st: _Section) -> None:
    assert ds.foreach is not None
    for parent in parent_rows[: ds.foreach.limit]:
        values = {name: parent.get(name) for name in ds.placeholders}
        tag = {f"parent.{k}": v for k, v in values.items()}
        mine = [r for r in (prev["rows"] if prev else []) if all(r.get(k) == v for k, v in tag.items())]
        argv = _child_argv(ds, values)
        env: dict = {}
        if argv is None:
            failure: dict | None = {"code": "bad_args",
                                    "message": "A placeholder value is null or starts with '-'; not run."}
        else:
            env = invoke(ds.command, argv)
            _note_cache(env, st)
            failure = _failure(env)
        if failure is not None:
            st.errors.append({"parent": values, **failure})
            st.rows += mine
            continue
        rows, failed = extract(env.get("data"), ds)
        st.rows += _merge_days([{**r, **tag} for r in rows], failed, mine, ds)
        st.day_errors += len(failed)


def run_recipe(datasets: list[Dataset], *, previous: dict | None, max_calls: int,
               max_seconds: float) -> tuple[dict, dict]:
    """Run every dataset in order inside one budget; return (bundle, summary).

    The bundle carries error codes only (everyone who opens the dashboard can read it); the
    summary, for the coach, carries the messages too."""
    prev_sections = previous.get("datasets") if isinstance(previous, dict) else None
    if not isinstance(prev_sections, dict):
        prev_sections = {}
    sections: dict[str, dict] = {}
    report: dict[str, dict] = {}
    started = time.monotonic()
    with ratelimit.budget(max_calls, max_seconds) as budget:
        for ds in datasets:
            calls_before = budget.calls
            st = _Section(generated=_now())
            prev = _previous(prev_sections.get(ds.id), ds)
            if ds.foreach is None:
                _run_single(ds, prev, st)
            else:
                _run_children(ds, sections[ds.foreach.dataset]["rows"], prev, st)
            calls = budget.calls - calls_before
            sections[ds.id] = {"rows": st.rows, "meta": {
                "command": " ".join([ds.command, *ds.argv]), "generated": st.generated,
                "columns": list(ds.columns), "rows": len(st.rows), "calls": calls,
                "cache_oldest_age_s": max(st.ages, default=None), "stale": st.stale, "reason": st.reason,
                "error": {"code": st.error["code"]} if st.error else None, "day_errors": st.day_errors,
                "errors": [{"parent": e["parent"], "code": e["code"]} for e in st.errors]}}
            report[ds.id] = {"rows": len(st.rows), "calls": calls, "stale": st.stale, "reason": st.reason,
                             "error": st.error, "day_errors": st.day_errors, "errors": st.errors}
        total = budget.calls
    bundle = {"meta": [{"label": "Generated", "value": _now()}], "datasets": sections}
    return bundle, {"calls": total, "seconds": round(time.monotonic() - started, 1), "datasets": report}
