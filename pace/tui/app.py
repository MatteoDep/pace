"""Textual app for `pace browse`."""

from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from pace.api import paginate, safe_call
from pace.tui.widgets import DetailTree

_ACTIVITY_COLUMNS = [
    "activityId",
    "activityName",
    "startTimeLocal",
    "activityType",
    "distance",
    "duration",
    "averageHR",
]


def _flatten_activity(a: dict) -> dict:
    out = dict(a)
    if isinstance(a.get("activityType"), dict):
        out["activityType"] = a["activityType"].get("typeKey")
    return out


class ActivityScreen(Screen):
    """List activities; press Enter on a row to drill in."""

    BINDINGS = [
        Binding("q", "app.quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "expand", "Drill in"),
    ]

    def __init__(self, limit: int = 50) -> None:
        super().__init__()
        self.limit = limit
        self._activities: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            yield DetailTree(name="detail")
        yield Static(id="status")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns(*_ACTIVITY_COLUMNS)
        await self.refresh_data()

    async def action_refresh(self) -> None:
        await self.refresh_data()

    async def refresh_data(self) -> None:
        self.set_status("loading activities…")
        try:
            data = await paginate(
                "get_activities", page_size=min(self.limit, 1000), max_items=self.limit
            )
        except RuntimeError as err:
            self.set_status(f"error: {err}")
            return
        self._activities = [_flatten_activity(a) for a in data]
        table = self.query_one("#table", DataTable)
        table.clear()
        for row in self._activities:
            table.add_row(*[str(row.get(c, "")) for c in _ACTIVITY_COLUMNS])
        self.set_status(f"{len(self._activities)} activities loaded")
        if self._activities:
            self._show_detail(self._activities[0])

    def set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def _show_detail(self, payload: Any) -> None:
        self.query_one(DetailTree).populate(payload)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Live-preview the highlighted row's summary in the side tree."""
        if not self._activities:
            return
        idx = event.cursor_row
        if idx is None or idx >= len(self._activities):
            return
        self._show_detail(self._activities[idx])

    async def action_expand(self) -> None:
        table = self.query_one("#table", DataTable)
        if not self._activities:
            return
        idx = table.cursor_row
        if idx is None or idx >= len(self._activities):
            return
        summary = self._activities[idx]
        activity_id = summary.get("activityId")
        if activity_id is None:
            self._show_detail(summary)
            return
        self.set_status(f"loading activity {activity_id}…")
        ok, detail, err = await safe_call("get_activity", int(activity_id))
        if not ok:
            self.set_status(f"error: {err}")
            self._show_detail(summary)
            return
        self._show_detail(detail or summary)
        self.set_status(f"detail for {activity_id}")


class PaceTuiApp(App):
    CSS = """
    DataTable { width: 60%; }
    DetailTree { width: 40%; border: solid cyan; }
    #status { height: 1; color: $accent; padding: 0 1; }
    """

    def __init__(self, target: str = "activity", limit: int = 50) -> None:
        super().__init__()
        self.target = target
        self.limit = limit

    def on_mount(self) -> None:
        if self.target == "activity":
            self.push_screen(ActivityScreen(limit=self.limit))
        else:
            raise NotImplementedError(f"browse target {self.target!r} not implemented yet.")


def run_tui(target: str = "activity", limit: int = 50) -> None:
    """Synchronous entry: spawn the Textual app."""
    PaceTuiApp(target=target, limit=limit).run()
