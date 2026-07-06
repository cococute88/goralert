"""Comparator evaluation shared by every threshold-based condition.

Supports the TS Comparator union: gt/gte/lt/lte/eq plus crossUp/crossDown.
The cross comparators need the previous observed value (``prev``) to detect a
threshold crossing between two evaluations:

- crossUp:   prev < threshold AND value >= threshold
- crossDown: prev > threshold AND value <= threshold

``eq`` uses a small relative+absolute tolerance so float noise does not break
equality checks.
"""

from __future__ import annotations

from typing import Optional

_EQ_ABS_TOL = 1e-9
_EQ_REL_TOL = 1e-9


def _almost_equal(a: float, b: float) -> bool:
    return abs(a - b) <= max(_EQ_ABS_TOL, _EQ_REL_TOL * max(abs(a), abs(b)))


def compare(
    value: Optional[float],
    comparator: Optional[str],
    threshold: Optional[float],
    prev: Optional[float] = None,
) -> bool:
    """Return True when ``value`` satisfies ``comparator`` against ``threshold``.

    Returns False (never raises) when inputs are missing or the comparator is
    unknown — the engine treats "cannot evaluate" as "not triggered".
    """
    if value is None or comparator is None or threshold is None:
        return False

    if comparator == "gt":
        return value > threshold
    if comparator == "gte":
        return value >= threshold
    if comparator == "lt":
        return value < threshold
    if comparator == "lte":
        return value <= threshold
    if comparator == "eq":
        return _almost_equal(float(value), float(threshold))
    if comparator == "crossUp":
        if prev is None:
            return False
        return prev < threshold <= value
    if comparator == "crossDown":
        if prev is None:
            return False
        return prev > threshold >= value
    return False
