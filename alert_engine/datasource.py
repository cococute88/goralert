"""Market + calendar data source for evaluators.

RSI is computed by ``alert_engine.rsi.compute_rsi`` (Wilder method, pandas).

All network/data fetches are DEFENSIVE: on any failure we return ``None`` and
let the engine skip+warn rather than crash the whole run.

MetricId mapping (mirrors TS MetricId union):
- rsi        -> RSI(period) of ``ticker`` close (KOSPI -> ^KS11)
- vix        -> ^VIX last close
- price      -> ``ticker`` last close
- fx         -> ``pair`` last close (e.g. "USDKRW" -> "USDKRW=X")
- gold       -> GC=F last close
- bitcoin    -> BTC-USD last close
- koreanEtf  -> ``code`` last close (".KS" suffix added when bare numeric code)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import DateEventSelector, MetricId
from .rsi import compute_rsi

logger = logging.getLogger("alert_engine.datasource")


# Ticker symbol aliases for yfinance.
_TICKER_ALIASES = {
    "KOSPI": "^KS11",
    "KOSDAQ": "^KQ11",
    "VIX": "^VIX",
    "GOLD": "GC=F",
    "BITCOIN": "BTC-USD",
    "BTC": "BTC-USD",
}


def _resolve_symbol(ticker: str) -> str:
    if not ticker:
        return ticker
    key = ticker.strip().upper()
    return _TICKER_ALIASES.get(key, ticker.strip())


def _resolve_fx_symbol(pair: str) -> str:
    """Map an FX pair like 'USDKRW' or 'USD/KRW' to yfinance 'USDKRW=X'."""
    cleaned = (pair or "").replace("/", "").replace("-", "").strip().upper()
    if not cleaned:
        return ""
    if cleaned.endswith("=X"):
        return cleaned
    return f"{cleaned}=X"


def _resolve_korean_etf_symbol(code: str) -> str:
    """Map a Korean ETF code (e.g. '069500') to a yfinance symbol ('069500.KS')."""
    raw = (code or "").strip().upper()
    if not raw:
        return ""
    if "." in raw:
        return raw
    if raw.isdigit():
        return f"{raw}.KS"
    return raw


class AlertDataSource:
    """Fetches market metrics and calendar data for evaluators.

    Stateless aside from an in-run cache to avoid duplicate yfinance calls
    within a single engine run. Inject ``now_fn`` for deterministic tests.
    """

    def __init__(self, now_fn=None, history_period: str = "6mo"):
        self._now_fn = now_fn
        self._history_period = history_period
        self._close_cache: Dict[str, Any] = {}

    # --- time ----------------------------------------------------------------

    def now(self) -> datetime:
        if self._now_fn is not None:
            return self._now_fn()
        return datetime.now(timezone.utc)

    # --- raw price history ---------------------------------------------------

    def _download_close(self, symbol: str, period: Optional[str] = None):
        """Return a pandas close Series for ``symbol`` or None on failure."""
        if not symbol:
            return None
        cache_key = f"{symbol}:{period or self._history_period}"
        if cache_key in self._close_cache:
            return self._close_cache[cache_key]
        try:
            import yfinance as yf  # lazy import
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance unavailable (%s)", exc)
            return None
        try:
            data = yf.download(
                symbol,
                period=period or self._history_period,
                interval="1d",
                progress=False,
                auto_adjust=False,
            )
            if data is None or len(data) == 0:
                self._close_cache[cache_key] = None
                return None
            close = data["Close"]
            # yfinance may return a single-column DataFrame for one symbol.
            try:
                import pandas as pd  # lazy import
                if isinstance(close, pd.DataFrame):
                    close = close.iloc[:, 0]
            except Exception:
                pass
            self._close_cache[cache_key] = close
            return close
        except Exception as exc:  # noqa: BLE001
            logger.warning("Price download failed for %s (%s)", symbol, exc)
            self._close_cache[cache_key] = None
            return None

    def _last_close(self, symbol: str) -> Optional[float]:
        close = self._download_close(symbol)
        if close is None:
            return None
        try:
            value = float(close.iloc[-1])
            return value if value == value else None  # NaN guard
        except Exception:
            return None

    # --- metrics -------------------------------------------------------------

    def get_metric(self, metric: Optional[MetricId]) -> Optional[float]:
        """Return the current scalar value for a MetricId, or None on failure."""
        if metric is None:
            return None
        kind = metric.metric
        try:
            if kind == "rsi":
                return self._get_rsi(metric.ticker or "", metric.period or 14)
            if kind == "vix":
                return self._last_close("^VIX")
            if kind == "price":
                return self._last_close(_resolve_symbol(metric.ticker or ""))
            if kind == "fx":
                return self._last_close(_resolve_fx_symbol(metric.pair or ""))
            if kind == "gold":
                return self._last_close("GC=F")
            if kind == "bitcoin":
                return self._last_close("BTC-USD")
            if kind == "koreanEtf":
                return self._last_close(_resolve_korean_etf_symbol(metric.code or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_metric failed for %s (%s)", kind, exc)
            return None
        logger.warning("Unknown metric kind: %s", kind)
        return None

    def _get_rsi(self, ticker: str, period: int) -> Optional[float]:
        """Compute Wilder RSI via alert_engine.rsi.compute_rsi."""
        close = self._download_close(_resolve_symbol(ticker), period="1y")
        if close is None:
            return None
        try:
            rsi_series = compute_rsi(close, period)
            if rsi_series is None or len(rsi_series) == 0:
                return None
            value = float(rsi_series.iloc[-1])
            return value if value == value else None  # NaN guard
        except Exception as exc:  # noqa: BLE001
            logger.warning("compute_rsi failed for %s (%s)", ticker, exc)
            return None

    # --- ratio ---------------------------------------------------------------

    def get_ratio(self, numerator: str, denominator: str) -> Optional[float]:
        """Return last close(numerator) / last close(denominator), or None."""
        num = self._last_close(_resolve_symbol(numerator))
        den = self._last_close(_resolve_symbol(denominator))
        if num is None or den is None or den == 0:
            return None
        return num / den

    # --- dividend ------------------------------------------------------------

    def get_dividend_metric(self, ticker: str) -> Optional[float]:
        """Return the most recent dividend amount for ``ticker`` via yfinance.

        Defensive: returns None when unavailable. Calendar-driven ex-dividend
        date alerts are handled by the date evaluator; this is the numeric
        payout metric used by dividend threshold conditions.
        """
        symbol = _resolve_symbol(ticker)
        if not symbol:
            return None
        try:
            import yfinance as yf  # lazy import
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance unavailable for dividends (%s)", exc)
            return None
        try:
            divs = yf.Ticker(symbol).dividends
            if divs is None or len(divs) == 0:
                return None
            value = float(divs.iloc[-1])
            return value if value == value else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("dividend fetch failed for %s (%s)", ticker, exc)
            return None

    # --- calendar (READ-ONLY) ------------------------------------------------

    def get_calendar_events(
        self,
        uid: str,
        selector: Optional[DateEventSelector],
        firestore=None,
    ) -> List[Dict[str, Any]]:
        """Return calendar events matching ``selector`` (read-only).

        Honors:
        - selector.source: "calendarEvents" | "calendarCustomEvents"
        - selector.match: {ticker?, type?, titleContains?}
        - selector.markFilter: ["star"|"heart"] — restricts to ⭐/❤️ events
          (star/heart live on calendarEvents meta).

        NOTE: 🔔 bell marks (``calendarAlertMarks``, Goralert-owned) are NOT
        merged here — this read covers only ⭐/❤️ on calendar event meta. Bell
        marks are written/read by the web app; wire ``read_calendar_alert_marks``
        in if bell-driven evaluation is needed.

        ``firestore`` is the firestore_client module (injected for testability).
        """
        if firestore is None:
            from . import firestore_client as firestore  # lazy import

        source = selector.source if selector and selector.source else "calendarEvents"
        try:
            if source == "calendarCustomEvents":
                events = firestore.read_calendar_custom_events(uid)
            else:
                events = firestore.read_calendar_events(uid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("calendar read failed for uid=%s (%s)", uid, exc)
            return []

        match = (selector.match if selector else None) or {}
        mark_filter = (selector.markFilter if selector else None) or []

        result: List[Dict[str, Any]] = []
        for event in events:
            if not self._event_matches(event, match):
                continue
            if mark_filter and not self._event_has_mark(event, mark_filter):
                continue
            result.append(event)
        return result

    @staticmethod
    def _event_matches(event: Dict[str, Any], match: Dict[str, Any]) -> bool:
        if not match:
            return True
        ticker = match.get("ticker")
        if ticker and str(event.get("ticker", "")).upper() != str(ticker).upper():
            return False
        ev_type = match.get("type")
        if ev_type:
            raw_types = ev_type if isinstance(ev_type, list) else [ev_type]
            # Old UI hints stored these two values even though the calendar's
            # actual persisted codes are ex_div/buy_by. `buy_by_minus_1` is an
            # alert-only selector: it reads the same buy_by source event and
            # DateEvaluator derives the notification date one calendar day
            # earlier. Keep existing rules functional while new rules write
            # canonical selector codes.
            aliases = {
                "ex-dividend": "ex_div",
                "buy-deadline": "buy_by",
                "buy_by_minus_1": "buy_by",
            }
            accepted_types = {
                aliases.get(str(item).strip(), str(item).strip())
                for item in raw_types
                if str(item).strip()
            }
            if accepted_types and str(event.get("type", "")) not in accepted_types:
                return False
        contains = match.get("titleContains")
        if isinstance(contains, str) and contains.strip():
            title = str(event.get("title", "")).casefold()
            if contains.strip().casefold() not in title:
                return False
        return True

    @staticmethod
    def _event_has_mark(event: Dict[str, Any], mark_filter: List[str]) -> bool:
        """star/heart flags live on calendarEvents meta as booleans."""
        for mark in mark_filter:
            if mark == "star" and bool(event.get("star")):
                return True
            if mark == "heart" and bool(event.get("heart")):
                return True
        return False
