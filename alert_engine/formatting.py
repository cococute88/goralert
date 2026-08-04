"""Display-only formatting for user-facing alert message values.

The engine evaluates, compares, and persists every metric at FULL provider
precision. Only the string that a human reads (Telegram text, FCM notification
title/body) is shortened here — nothing in this module is ever fed back into
``compare()``, ``lastValue``, ``DataResult``, or a Firestore write.

``format_display_value`` is applied at exactly one place: the ``{value}``
placeholder substitution in :func:`alert_engine.event.render_message`. Both the
Telegram and the Push channel consume the resulting ``MessageTemplate``, so the
two channels always show an identical number.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

_DISPLAY_QUANTUM = Decimal("0.01")
_MAX_DISPLAY_DECIMALS = 2


def format_display_value(value: Any) -> str:
    """Return the user-facing text for an evaluated value.

    Numeric values carrying more than two decimal places (raw float noise such
    as ``33.470001220703125``) are rounded half-up to two decimals. Everything
    else is returned exactly as ``str`` already rendered it:

    - ints and bools -> unchanged
    - non-finite floats (NaN / inf) -> unchanged, never coerced to ``0.00``
    - strings, dates, ``None`` and any other type -> unchanged
    - floats already within two decimals (``30.0``, ``2.5``) -> unchanged

    The rounding operates on ``str(value)`` (the shortest round-trip repr) so a
    value a user would write as ``2.005`` rounds up to ``2.01`` rather than
    following the binary representation downwards.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        # NaN / inf keep their existing rendering; the caller's failure
        # classification is unaffected by how they are displayed.
        return str(value)

    text = str(value)
    try:
        decimal_value = Decimal(text)
    except (InvalidOperation, ValueError):  # pragma: no cover - defensive
        return text

    exponent = decimal_value.as_tuple().exponent
    if not isinstance(exponent, int) or -exponent <= _MAX_DISPLAY_DECIMALS:
        # Already two decimals or fewer (including integral/scientific forms):
        # keep the original rendering untouched.
        return text

    quantized = decimal_value.quantize(_DISPLAY_QUANTUM, rounding=ROUND_HALF_UP)
    if quantized == 0:
        # Avoid rendering "-0.00" for a tiny negative value.
        quantized = quantized.copy_abs()
    return f"{quantized:f}"
