"""`pace requests apply`: file handling, login check, envelope, schema."""

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from pace import pagerequests as pr
from pace.api import ApiError
from pace.cli import app
from pace.commands.schema import build_schema

runner = CliRunner()
A = 12345678901
EFFORT = {"id": "r1", "version": 2, "at": "2026-11-27T08:00:00Z", "op": "effort.set",
          "params": {"activity_id": A, "rpe": 6}}


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")
    monkeypatch.setattr("pace.auth.auth_status", lambda: {"authenticated": True})


@pytest.fixture
def calls(monkeypatch) -> list[str]:
    seen: list[str] = []
    answers: dict[str, Any] = {"get_activity": {"activityId": A}, "connectapi": None}

    async def safe_call(name, /, *args, **kwargs):
        seen.append(name)
        answer = answers[name]
        return (False, None, answer) if isinstance(answer, ApiError) else (True, answer, None)

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    monkeypatch.setattr("pace.writes.safe_call", safe_call)
    return seen


def _doc(res) -> dict:
    assert res.stdout.count("\n") == 1, res.stdout
    return json.loads(res.stdout)


def _file(tmp_path, content: str):
    path = tmp_path / "requests.json"
    path.write_text(content)
    return str(path)


@pytest.mark.parametrize("content", ["{not json", '{"op": "effort.set"}', '"x"', "[" + "9" * 5000 + "]",
                                     "[" * 100000, b"[\xff\xfe]"],
                         ids=["syntax", "object", "string", "huge-int", "deep", "not-utf8"])
def test_bad_file_is_bad_args(tmp_path, calls, content) -> None:
    path = tmp_path / "requests.json"
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    res = runner.invoke(app, ["requests", "apply", str(path)])
    doc = _doc(res)
    assert res.exit_code == 2 and doc["error"]["code"] == "bad_args" and doc["command"] == "requests apply"
    assert calls == []


def test_empty_list_needs_no_login(tmp_path, calls, monkeypatch) -> None:
    def no_check():
        raise AssertionError("auth_status called for an empty list")

    monkeypatch.setattr("pace.auth.auth_status", no_check)
    doc = _doc(runner.invoke(app, ["requests", "apply", _file(tmp_path, "[]")]))
    assert doc["ok"] is True and doc["data"] == [] and not pr.log_path().exists()


def test_no_tokens_is_auth_required(tmp_path, calls, monkeypatch) -> None:
    monkeypatch.setattr("pace.auth.auth_status", lambda: {"authenticated": False})
    res = runner.invoke(app, ["requests", "apply", _file(tmp_path, json.dumps([EFFORT]))])
    assert res.exit_code == 3 and _doc(res)["error"]["code"] == "auth_required" and calls == []


def test_apply_one_request(tmp_path, calls) -> None:
    path = _file(tmp_path, json.dumps([EFFORT]))
    doc = _doc(runner.invoke(app, ["requests", "apply", path]))
    assert doc["ok"] is True and doc["args"] == {"file": path, "dry_run": False}
    [res] = doc["data"]
    assert (res["id"], res["version"], res["op"], res["status"]) == ("r1", 2, "effort.set", "applied")
    assert res["did"][0]["action"] == "update" and calls == ["get_activity", "connectapi"]


def test_stdin_and_dry_run(calls) -> None:
    doc = _doc(runner.invoke(app, ["requests", "apply", "-", "--dry-run"], input=json.dumps([EFFORT])))
    assert doc["data"][0]["status"] == "planned" and calls == ["get_activity"] and not pr.log_path().exists()


def test_auth_required_mid_run_fails_the_command(tmp_path, calls, monkeypatch) -> None:
    async def safe_call(name, /, *args, **kwargs):
        return False, None, ApiError("auth_required", "tokens rejected", 401)

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    res = runner.invoke(app, ["requests", "apply", _file(tmp_path, json.dumps([EFFORT]))])
    assert res.exit_code == 3 and _doc(res)["error"]["code"] == "auth_required"


def test_schema_entry_and_block() -> None:
    schema = build_schema(None)
    [entry] = [c for c in schema["commands"] if c["name"] == "requests apply"]
    assert entry["write"] is True and entry["exportable"] is False
    assert entry["args"] == ["FILE"] and entry["flags"] == ["--dry-run"]
    block = schema["requests"]
    assert block["collection"] == pr.COLLECTION == "data/requests/pending"
    assert block["log_path"] == "$PACE_HOME/requests-applied.jsonl"
    assert block["ops"] == {op: {"params": list(pr.PARAMS[op]), "required": list(pr.REQUIRED[op])} for op in pr.OPS}
    assert (block["max_attempts"], block["max_calls"], block["max_seconds"]) == (3, pr.MAX_CALLS, pr.MAX_SECONDS)
    assert block["kg_range"] == [25, 250] and block["max_age_days"] == 365 and block["max_failing_days"] == 7
    assert block["refused_codes"] == list(pr.REFUSED_CODES) and block["statuses"] == list(pr.STATUSES)
    assert "requests" in schema["groups"]
    assert "requests apply" in schema["writes"]["confirm"]


def test_schema_group_accepted() -> None:
    doc = _doc(runner.invoke(app, ["schema", "requests"]))
    assert [c["name"] for c in doc["data"]["commands"]] == ["requests apply"]
