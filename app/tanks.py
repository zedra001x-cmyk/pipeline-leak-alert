"""Tank level helpers (pure Python): read 10 tank levels and compute the average."""
from __future__ import annotations

import math
from collections.abc import Iterable

TANK_COUNT = 10


class TankError(ValueError):
    """A tank level list is the wrong size or contains an impossible value."""


def validate_levels(levels: Iterable[float], count: int = TANK_COUNT) -> list[float]:
    """Return the levels as a list; require exactly `count` numbers between 0 and 100 (percent)."""
    values = list(levels)
    if len(values) != count:
        raise TankError(f"Expected {count} tank levels, got {len(values)}")
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 100:
            raise TankError(f"Tank level must be a number from 0 to 100, got {v!r}")
    return values


def average_level(levels: Iterable[float]) -> float:
    """Average of the tank levels (percent). Rejects an empty list and values outside 0-100."""
    values = list(levels)
    if not values:
        raise TankError("No tank levels given")
    validate_levels(values, count=len(values))
    return sum(values) / len(values)
