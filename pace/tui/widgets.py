"""Reusable Textual widgets for pace."""

from typing import Any

from textual.widgets import Tree


class DetailTree(Tree):
    """A Tree widget that lazy-expands a nested dict/list payload."""

    def __init__(self, *, name: str = "details") -> None:
        super().__init__(name)
        self.show_root = False

    def populate(self, data: Any) -> None:
        """Reset the tree from `data` (typically a Garmin response dict)."""
        self.clear()
        _add_node(self.root, "", data)
        self.root.expand_all()


def _add_node(parent, label: str, value: Any) -> None:
    if isinstance(value, dict):
        if not value:
            parent.add_leaf(f"{label}: {{}}")
            return
        branch = parent.add(label or "(root)")
        for k, v in value.items():
            _add_node(branch, str(k), v)
        return
    if isinstance(value, list):
        if not value:
            parent.add_leaf(f"{label}: []")
            return
        branch = parent.add(f"{label} [{len(value)}]")
        for i, item in enumerate(value):
            _add_node(branch, f"[{i}]", item)
        return
    parent.add_leaf(f"{label}: {value!r}")
