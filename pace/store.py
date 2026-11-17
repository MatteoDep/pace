"""Generic locked JSON file store: atomic writes and an advisory inter-process lock."""

import copy
import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pace.envelope import PaceError


def load_json(path: Path, empty: dict[str, Any], what: str) -> dict[str, Any]:
    """Read a JSON object. Missing file -> a deep copy of `empty`; unreadable or non-dict -> internal."""
    hint = f"Inspect or move {path}; pace never overwrites it."
    if not path.exists():
        return copy.deepcopy(empty)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PaceError("internal", f"{what} unreadable: {exc}", hint=hint) from None
    if not isinstance(data, dict):
        raise PaceError("internal", f"{what} unreadable: expected a JSON object", hint=hint)
    return data


def save_json(path: Path, data: dict[str, Any]) -> None:
    """Write atomically: <path>.tmp then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


@contextmanager
def locked(path: Path, timeout_s: float = 10.0) -> Iterator[None]:
    """Hold an exclusive flock on <path>.lock (never deleted).

    flock is per open file description: a nested locked() on the same path in one process
    blocks until timeout, so callers never nest transactions.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + timeout_s
    with lock_path.open("a") as fh:
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise PaceError(
                        "internal", f"{path.name} is busy",
                        hint="Another pace process is writing it; retry.",
                    ) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
