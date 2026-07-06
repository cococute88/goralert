"""Wilder RSI computation (extracted from original/logic/market.py).

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
    """Compute Wilder RSI using pandas ewm (alpha=1/period).

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
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))

    rsi = rsi.where(avg_loss != 0, 100.0)
    flat_mask = (avg_gain == 0) & (avg_loss == 0)
    rsi = rsi.mask(flat_mask, 50.0)

    return rsi
