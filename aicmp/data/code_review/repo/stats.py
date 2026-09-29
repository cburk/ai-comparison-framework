"""Small statistics helpers used by the reporting jobs."""


def average(values: list[float]) -> float:
    """Return the arithmetic mean of `values`."""
    return sum(values) / len(values)


def running_max(values: list[float]) -> list[float]:
    """Return the running maximum of `values`."""
    out = []
    current = None
    for v in values:
        current = v if current is None or v > current else current
        out.append(current)
    return out
