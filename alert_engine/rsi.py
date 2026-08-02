"""Canonical Wilder RSI computation for the alert worker.

Pure pandas implementation — no external TA libraries required.
"""

from __future__ import annotations

import pandas as pd


def _coerce_close(close) -> pd.Series:
    """Normalize input to a numeric close Series."""
    if close is None:
        return pd.Series(dtype="float64")

    if isinstance(close, pd.DataFrame):
        if close.shape[1] == 0:
            return pd.Series(dtype="float64")
        series = close.iloc[:, 0]
    elif isinstance(close, pd.Series):
        series = close
    else:
        try:
            series = pd.Series(close)
        except Exception:
            return pd.Series(dtype="float64")

    series = pd.to_numeric(series, errors="coerce")
    series = series.dropna()
    return series


def compute_rsi(close, period: int = 14) -> pd.Series:
    """Compute Wilder RSI with an SMA seed and recursive smoothing.

    Returns a Series aligned to the input index. NaN where insufficient data.
    """
    series = _coerce_close(close)

    try:
        period = int(period)
    except (TypeError, ValueError):
        period = 14
    if period < 1:
        period = 14

    if series.empty or len(series) <= period:
        return pd.Series(index=series.index, dtype="float64")

    delta = series.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    result = pd.Series(float("nan"), index=series.index, dtype="float64")

    avg_gain = float(gains.iloc[1:period + 1].mean())
    avg_loss = float(losses.iloc[1:period + 1].mean())

    def rsi_value(gain: float, loss: float) -> float:
        if gain == 0 and loss == 0:
            return 50.0
        if loss == 0:
            return 100.0
        if gain == 0:
            return 0.0
        relative_strength = gain / loss
        return 100.0 - (100.0 / (1.0 + relative_strength))

    result.iloc[period] = rsi_value(avg_gain, avg_loss)
    for position in range(period + 1, len(series)):
        avg_gain = ((period - 1) * avg_gain + float(gains.iloc[position])) / period
        avg_loss = ((period - 1) * avg_loss + float(losses.iloc[position])) / period
        result.iloc[position] = rsi_value(avg_gain, avg_loss)

    return result
