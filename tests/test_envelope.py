"""Envelope shape, exit codes, runner behaviour."""

import json

import pytest
import typer

from pace.api import ApiError
from pace.envelope import PaceError, Result, emit, error, exit_code, ok, run


def test_ok_shape() -> None:
    doc = ok("wellness sleep", {"from": "2026-01-01"}, Result(data=[1], cache={"hit": True, "oldest_age_s": 5}))
    assert doc == {
        "ok": True,
        "command": "wellness sleep",
        "args": {"from": "2026-01-01"},
        "cache": {"hit": True, "oldest_age_s": 5},
        "shape": None,
        "truncated": False,
        "data": [1],
    }


def test_error_shape_and_hint() -> None:
    doc = error("x", PaceError.from_api(ApiError("auth_required", "nope", 401)))
    assert doc["ok"] is False
    assert doc["command"] == "x"
    assert doc["error"]["code"] == "auth_required"
    assert doc["error"]["http"] == 401
    assert "pace auth login" in doc["error"]["hint"]


def test_exit_codes() -> None:
    assert exit_code("bad_args") == 2
    assert exit_code("auth_required") == 3
    assert exit_code("rate_limited") == 4
    assert exit_code("not_found") == 1
    assert exit_code("internal") == 1


def test_emit_is_compact_single_line(capsys) -> None:
    emit({"a": [1, 2], "b": {"c": None}})
    out = capsys.readouterr().out
    assert out == '{"a":[1,2],"b":{"c":null}}\n'


def test_run_success(capsys) -> None:
    async def work() -> Result:
        return Result(data={"v": 1})

    run("cmd", {}, work)
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is True and doc["data"] == {"v": 1}


def test_run_error_exits_with_code(capsys) -> None:
    async def work() -> Result:
        raise PaceError("rate_limited", "slow down", 429)

    with pytest.raises(typer.Exit) as exc:
        run("cmd", {}, work)
    assert exc.value.exit_code == 4
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["error"]["code"] == "rate_limited"
    assert capsys.readouterr().err == ""


def test_run_unexpected_exception_becomes_internal(capsys) -> None:
    async def work() -> Result:
        raise ValueError("boom")

    with pytest.raises(typer.Exit) as exc:
        run("cmd", {}, work)
    assert exc.value.exit_code == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "internal" and "boom" in doc["error"]["message"]
