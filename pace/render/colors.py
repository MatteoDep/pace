"""Color / threshold helpers for metric values."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Threshold:
    """Three-band threshold.

    Direction: `higher_better=True` means values >= good are green,
    < warn are red; reversed when `higher_better=False`.
    """

    warn: float
    good: float
    higher_better: bool = True


def threshold_color(value: float | None, threshold: Threshold) -> str:
    """Return a Rich style name (green/yellow/red/dim) for `value`."""
    if value is None:
        return "dim"
    if threshold.higher_better:
        if value >= threshold.good:
            return "green"
        if value >= threshold.warn:
            return "yellow"
        return "red"
    # lower-is-better
    if value <= threshold.good:
        return "green"
    if value <= threshold.warn:
        return "yellow"
    return "red"


def colored(value: object, color: str) -> str:
    """Wrap `value` in Rich markup. Empty/None returns dim placeholder."""
    if value is None or value == "":
        return "[dim]-[/dim]"
    return f"[{color}]{value}[/{color}]"
