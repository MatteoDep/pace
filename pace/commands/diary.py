"""Diary commands: the coach's durable memory (local file, no Garmin access)."""

import typer

from pace import diary
from pace.envelope import PaceError, Result, run

app = typer.Typer(no_args_is_help=True, rich_markup_mode=None, pretty_exceptions_enable=False,
                  help="Athlete diary: goals, constraints, recommendations, feedback (local file).")

_REF_HELP = "session:<id>, activity:<id> or entry:<id>."


@app.command("show")
def show(since: str | None = typer.Option(None, "--since", help="Only entries on/after this date or ISO datetime."),
         kind: str | None = typer.Option(None, "--kind", help=f"One of {', '.join(diary.KINDS)}."),
         ref: str | None = typer.Option(None, "--ref", help=f"Only entries with this ref: {_REF_HELP}"),
         limit: int = typer.Option(20, "--limit", help="Max entries returned (newest first).")) -> None:
    """Profile, active constraints/injuries, due entries and recent entries (newest first)."""
    async def work() -> Result:
        out, truncated = diary.show(diary.load(), since=since, kind=kind, ref=ref, limit=limit)
        return Result(out, truncated=truncated)

    run("diary show", {"since": since, "kind": kind, "ref": ref, "limit": limit}, work)


@app.command("set")
def set_(key: str = typer.Argument(..., metavar="KEY", help=f"One of {', '.join(diary.PROFILE_KEYS)}."),
         value: str = typer.Argument(..., metavar="VALUE")) -> None:
    """Set a profile field."""
    async def work() -> Result:
        with diary.transaction() as data:
            return Result({"profile": diary.set_profile(data, key, value)})

    run("diary set", {"key": key, "value": value}, work)


@app.command("unset")
def unset(key: str = typer.Argument(..., metavar="KEY", help=f"One of {', '.join(diary.PROFILE_KEYS)}.")) -> None:
    """Remove a profile field."""
    async def work() -> Result:
        with diary.transaction() as data:
            return Result({"profile": diary.unset_profile(data, key)})

    run("diary unset", {"key": key}, work)


@app.command("add")
def add(text: list[str] = typer.Argument(..., metavar="TEXT...",
                                         help="Entry text (words are joined). Put it after `--` if it may start with `-`."),
        kind: str = typer.Option(..., "--kind", help=f"One of {', '.join(diary.KINDS)}."),
        on: str | None = typer.Option(None, "--date", help="Entry date; default today."),
        tag: list[str] = typer.Option([], "--tag", help="Repeatable."),
        ref: list[str] = typer.Option([], "--ref", help=f"Repeatable: {_REF_HELP}"),
        due: str | None = typer.Option(None, "--due", help="Date to check this entry again.")) -> None:
    """Append an entry. TEXT goes after `--` when it may start with `-`."""
    async def work() -> Result:
        with diary.transaction() as data:
            entry = diary.add_entry(data, kind=kind, text=" ".join(text), on=on, tags=tag, refs=ref, due=due)
        return Result({"entry": entry})

    run("diary add", {"kind": kind, "date": on, "tags": tag, "refs": ref, "due": due, "text": " ".join(text)}, work)


@app.command("remove")
def remove(entry_id: int = typer.Argument(..., metavar="ID")) -> None:
    """Delete an entry; ids are never reused."""
    async def work() -> Result:
        with diary.transaction() as data:
            return Result({"removed": diary.remove_entry(data, entry_id)})

    run("diary remove", {"id": entry_id}, work)


@app.command("set-due")
def set_due(entry_id: int = typer.Argument(..., metavar="ID"),
            on: str | None = typer.Option(None, "--date", help="New due date."),
            clear: bool = typer.Option(False, "--clear", help="Remove the due date.")) -> None:
    """Set or clear an entry's due date. Give exactly one of --date or --clear."""
    async def work() -> Result:
        if (on is None) == (not clear):
            raise PaceError("bad_args", "give exactly one of --date or --clear")
        with diary.transaction() as data:
            return Result({"entry": diary.set_due(data, entry_id, None if clear else on)})

    run("diary set-due", {"id": entry_id, "date": on, "clear": clear}, work)


@app.command("resolve")
def resolve(entry_id: int = typer.Argument(..., metavar="ID"),
            on: str | None = typer.Option(None, "--date", help="Resolution date; default today."),
            reopen: bool = typer.Option(False, "--reopen", help="Clear the resolved date.")) -> None:
    """Mark a constraint or injury resolved (--date, default today), or --reopen it. Not both."""
    async def work() -> Result:
        if reopen and on is not None:
            raise PaceError("bad_args", "give --date or --reopen, not both")
        with diary.transaction() as data:
            entry = diary.reopen(data, entry_id) if reopen else diary.resolve(data, entry_id, on)
        return Result({"entry": entry})

    run("diary resolve", {"id": entry_id, "date": on, "reopen": reopen}, work)
