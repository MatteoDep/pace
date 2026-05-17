"""Unicode-block chart helpers: sparkline + horizontal bar."""

_BLOCKS = " ▁▂▃▄▅▆▇█"


def sparkline(values: list[float | int | None], width: int | None = None) -> str:
    """Render a one-line unicode-block sparkline.

    `None` values render as space.
    If `width` is given and shorter than `values`, samples are bucketed.
    """
    series = list(values)
    if width is not None and width > 0 and len(series) > width:
        series = _bucket(series, width)

    numeric = [v for v in series if isinstance(v, (int, float))]
    if not numeric:
        return " " * len(series)

    lo, hi = min(numeric), max(numeric)
    span = hi - lo
    if span == 0:
        return _BLOCKS[4] * len(series)

    out = []
    n = len(_BLOCKS) - 1
    for v in series:
        if not isinstance(v, (int, float)):
            out.append(" ")
            continue
        idx = int(round((v - lo) / span * (n - 1))) + 1
        idx = max(1, min(n, idx))
        out.append(_BLOCKS[idx])
    return "".join(out)


def bar_row(value: float, max_value: float, width: int = 20) -> str:
    """Return a `width`-char horizontal bar filled proportional to value/max."""
    if max_value <= 0:
        return " " * width
    filled = max(0, min(width, int(round((value / max_value) * width))))
    return "█" * filled + "·" * (width - filled)


def _bucket(values: list, n: int) -> list:
    """Down-sample to `n` buckets by averaging numeric values per bucket."""
    out = []
    size = len(values) / n
    for i in range(n):
        chunk = values[int(i * size) : int((i + 1) * size)] or [None]
        nums = [v for v in chunk if isinstance(v, (int, float))]
        out.append(sum(nums) / len(nums) if nums else None)
    return out
