"""Tests for nested-dict flattening."""

from pace.render._flatten import flatten_dict


def test_flat_passthrough() -> None:
    assert flatten_dict({"a": 1, "b": "x"}) == {"a": 1, "b": "x"}


def test_one_level_nested() -> None:
    out = flatten_dict({"a": {"x": 1, "y": 2}})
    assert out == {"a.x": 1, "a.y": 2}


def test_max_depth_cuts_off() -> None:
    out = flatten_dict({"a": {"b": {"c": 1}}}, max_depth=2)
    assert "a.b" in out
    assert out["a.b"].startswith("<dict")


def test_scalar_list_joined() -> None:
    out = flatten_dict({"tags": ["a", "b", "c"]})
    assert out == {"tags": "a, b, c"}


def test_dict_list_placeholder() -> None:
    out = flatten_dict({"rows": [{"x": 1}, {"x": 2}]})
    assert out == {"rows": "<list[2]>"}
