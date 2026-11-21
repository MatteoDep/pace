"""JSON store: load/save, corruption, inter-process lock."""

import fcntl
import json
import multiprocessing

import pytest

from pace.envelope import PaceError
from pace.store import load_json, locked, save_json


def _bump(path_str: str) -> None:
    from pathlib import Path

    p = Path(path_str)
    for _ in range(20):
        with locked(p):
            d = load_json(p, {"n": 0}, "x")
            d["n"] += 1
            save_json(p, d)


def test_missing_returns_copy_of_empty(tmp_path) -> None:
    empty = {"a": [1]}
    p = tmp_path / "x.json"
    got = load_json(p, empty, "x")
    assert got == empty
    got["a"].append(2)
    assert empty == {"a": [1]}
    assert not p.exists()


def test_save_round_trip_unicode(tmp_path) -> None:
    p = tmp_path / "x.json"
    save_json(p, {"t": "perché città"})
    assert "perché città" in p.read_text(encoding="utf-8")
    assert load_json(p, {}, "x") == {"t": "perché città"}
    assert not (tmp_path / "x.json.tmp").exists()


@pytest.mark.parametrize("content", ["{oops", "[]"])
def test_corrupt_is_internal_and_untouched(tmp_path, content) -> None:
    p = tmp_path / "x.json"
    p.write_text(content)
    with pytest.raises(PaceError) as exc:
        load_json(p, {}, "thing")
    assert exc.value.code == "internal" and str(p) in (exc.value.hint or "")
    assert "thing unreadable" in exc.value.message
    assert p.read_text() == content


def test_lock_serializes_writers(tmp_path) -> None:
    p = tmp_path / "x.json"
    save_json(p, {"n": 0})
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_bump, args=(str(p),)) for _ in range(2)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(60)
        assert proc.exitcode == 0
    assert json.loads(p.read_text())["n"] == 40


def test_lock_timeout_is_internal(tmp_path) -> None:
    p = tmp_path / "x.json"
    with (tmp_path / "x.json.lock").open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        with pytest.raises(PaceError) as exc, locked(p, timeout_s=0.2):
            pass
    assert exc.value.code == "internal" and "busy" in exc.value.message


def test_fresh_dir_is_created(tmp_path) -> None:
    p = tmp_path / "new" / "x.json"
    with locked(p):
        save_json(p, {"a": 1})
    assert load_json(p, {}, "x") == {"a": 1}


def test_saved_file_is_private_and_dir_is_0700(tmp_path) -> None:
    p = tmp_path / "new" / "x.json"
    save_json(p, {"a": 1})
    assert p.stat().st_mode & 0o777 == 0o600
    assert p.parent.stat().st_mode & 0o777 == 0o700


def test_failed_write_leaves_old_file_and_no_tmp(tmp_path, monkeypatch) -> None:
    p = tmp_path / "x.json"
    save_json(p, {"a": 1})

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("pace.store.json.dumps", boom)
    with pytest.raises(RuntimeError):
        save_json(p, {"a": 2})
    assert load_json(p, {}, "x") == {"a": 1}
    assert not (tmp_path / "x.json.tmp").exists()


def test_failed_replace_removes_tmp(tmp_path, monkeypatch) -> None:
    p = tmp_path / "x.json"

    def boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr("pace.store.os.replace", boom)
    with pytest.raises(OSError):
        save_json(p, {"a": 2})
    assert not (tmp_path / "x.json.tmp").exists()
