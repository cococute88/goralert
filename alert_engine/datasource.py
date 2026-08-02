"""Market + calendar data source for evaluators.

RSI is computed by ``alert_engine.rsi.compute_rsi`` (Wilder method, pandas).

All network/data fetches are defensive and return a structured ``DataResult``
to evaluators. Compatibility scalar methods still return ``None`` on failure,
but the production evaluator path preserves the exact reason and timestamp.

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
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .data import (
    DATA_CALCULATION_ERROR,
    DATA_INVALID_INPUT,
    DATA_NO_DATA,
    DATA_PROVIDER_ERROR,
    DATA_STALE,
    DataResult,
)
from .models import DateEventSelector, MetricId
from .calendar_contract import (
    calendar_event_identity_keys,
    calendar_selector_match_types,
    calendar_selector_title_contains,
    normalize_calendar_event_type,
)
from .rsi import compute_rsi

logger = logging.getLogger("alert_engine.datasource")

DEFAULT_MAX_DAILY_BAR_AGE_HOURS = 72.0
DEFAULT_RATIO_MAX_TIMESTAMP_SKEW_HOURS = 36.0
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 10.0


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
    return _TICKER_ALIASES.get(key, key)


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

    def __init__(
        self,
        now_fn=None,
        history_period: str = "6mo",
        firestore=None,
        max_daily_bar_age_hours: Optional[float] = None,
        ratio_max_timestamp_skew_hours: Optional[float] = None,
    ):
        self._now_fn = now_fn
        self._history_period = history_period
        self._close_cache: Dict[str, DataResult] = {}
        self._firestore = firestore
        self._max_daily_bar_age = timedelta(hours=(
            max_daily_bar_age_hours
            if max_daily_bar_age_hours is not None
            else float(os.environ.get(
                "ALERT_MAX_DAILY_BAR_AGE_HOURS",
                DEFAULT_MAX_DAILY_BAR_AGE_HOURS,
            ))
        ))
        self._ratio_max_timestamp_skew = timedelta(hours=(
            ratio_max_timestamp_skew_hours
            if ratio_max_timestamp_skew_hours is not None
            else float(os.environ.get(
                "ALERT_RATIO_MAX_TIMESTAMP_SKEW_HOURS",
                DEFAULT_RATIO_MAX_TIMESTAMP_SKEW_HOURS,
            ))
        ))
        self._provider_timeout_seconds = float(os.environ.get(
            "ALERT_PROVIDER_TIMEOUT_SECONDS",
            DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        ))

    # --- time ----------------------------------------------------------------

    def now(self) -> datetime:
        if self._now_fn is not None:
            return self._now_fn()
        return datetime.now(timezone.utc)

    # --- raw price history ---------------------------------------------------

    @staticmethod
    def _provider_error(exc: Exception, action: str) -> DataResult:
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        if "timeout" in name or "timeout" in message or "timed out" in message:
            code = "provider_timeout"
        elif "ratelimit" in name or "rate limit" in message or "429" in message:
            code = "provider_rate_limit"
        elif any(token in name or token in message for token in ("auth", "unauthorized", "forbidden", "401", "403")):
            code = "provider_authentication_error"
        elif "jsondecodeerror" in name or "expecting value" in message:
            code = "provider_response_parse_error"
        else:
            code = "provider_error"
        return DataResult.failure(
            DATA_PROVIDER_ERROR,
            code,
            f"{action} failed ({type(exc).__name__})",
            provider="yfinance",
        )

    @staticmethod
    def _download_error(yf, symbol: str) -> Optional[str]:
        """Return yfinance's per-symbol error when download returned an empty frame."""
        shared = getattr(yf, "shared", None)
        if shared is None:
            try:
                from yfinance import shared  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - optional private diagnostic surface
                return None
        errors = getattr(shared, "_ERRORS", None)
        if not isinstance(errors, dict):
            return None
        for key, value in errors.items():
            if str(key).upper() == symbol.upper() and value:
                return str(value)
        return None

    @staticmethod
    def _select_close_payload(data, symbol: str):
        """Select one Close/Adj Close series from flat or MultiIndex columns."""
        import pandas as pd

        for field in ("Close", "Adj Close"):
            payload = None
            if isinstance(data.columns, pd.MultiIndex):
                exact = [
                    column for column in data.columns
                    if field in tuple(str(part) for part in column)
                    and symbol in tuple(str(part).upper() for part in column)
                ]
                matches = exact or [
                    column for column in data.columns
                    if field in tuple(str(part) for part in column)
                ]
                if len(matches) == 1:
                    payload = data.loc[:, matches[0]]
                elif len(matches) > 1:
                    return None, field, "ambiguous"
            elif field in data.columns:
                payload = data[field]
            if payload is None:
                continue
            if isinstance(payload, pd.DataFrame):
                if payload.shape[1] != 1:
                    return None, field, "ambiguous"
                payload = payload.iloc[:, 0]
            return payload, field, None
        return None, None, "missing"

    def _download_history(self, yf, symbol: str, period: str):
        """Fetch one symbol while preserving provider exceptions when supported."""
        ticker_factory = getattr(yf, "Ticker", None)
        if callable(ticker_factory):
            return ticker_factory(symbol).history(
                period=period,
                interval="1d",
                auto_adjust=False,
                timeout=self._provider_timeout_seconds,
                raise_errors=True,
            )
        # Test doubles and older extension seams may only expose download().
        return yf.download(
            symbol,
            period=period,
            interval="1d",
            progress=False,
            auto_adjust=False,
            timeout=self._provider_timeout_seconds,
        )

    @staticmethod
    def _series_observed_at(close) -> Optional[datetime]:
        try:
            import pandas as pd

            raw_timestamp = close.index[-1]
            if isinstance(raw_timestamp, (int, float)):
                return None
            timestamp = pd.Timestamp(raw_timestamp)
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize("UTC")
            else:
                timestamp = timestamp.tz_convert("UTC")
            return timestamp.to_pydatetime().astimezone(timezone.utc)
        except Exception:
            return None

    def _download_close_result(self, symbol: str, period: Optional[str] = None) -> DataResult:
        """Return a normalized daily close series with provider provenance."""
        if not symbol:
            return DataResult.failure(DATA_INVALID_INPUT, "missing_symbol", "market symbol is empty")
        cache_key = f"{symbol}:{period or self._history_period}"
        if cache_key in self._close_cache:
            return self._close_cache[cache_key]
        try:
            import yfinance as yf  # lazy import
        except Exception as exc:  # noqa: BLE001
            result = self._provider_error(exc, "provider import")
            logger.warning("market data status=%s code=%s symbol=%s", result.status, result.code, symbol)
            return result
        try:
            data = self._download_history(yf, symbol, period or self._history_period)
            if data is None or len(data) == 0:
                download_error = self._download_error(yf, symbol)
                if download_error:
                    lowered = download_error.lower()
                    if "jsondecodeerror" in lowered or "expecting value" in lowered:
                        code = "provider_response_parse_error"
                        detail = "provider returned no rows after a JSON response parse failure"
                    else:
                        code = "provider_download_error"
                        detail = "provider returned no rows after a recorded download error"
                    result = DataResult.failure(
                        DATA_PROVIDER_ERROR,
                        code,
                        detail,
                        provider="yfinance",
                        metadata={"symbol": symbol},
                    )
                    self._close_cache[cache_key] = result
                    return result
                result = DataResult.failure(
                    DATA_NO_DATA,
                    "symbol_no_data",
                    "provider returned no rows; symbol may be invalid or temporarily unavailable",
                    provider="yfinance",
                )
                self._close_cache[cache_key] = result
                return result
            close, price_field, payload_error = self._select_close_payload(data, symbol)
            if payload_error == "missing":
                result = DataResult.failure(
                    DATA_PROVIDER_ERROR,
                    "malformed_response",
                    "provider response did not contain Close or Adj Close",
                    provider="yfinance",
                )
                self._close_cache[cache_key] = result
                return result
            if payload_error == "ambiguous":
                result = DataResult.failure(
                    DATA_PROVIDER_ERROR,
                    "malformed_response",
                    f"provider returned an ambiguous {price_field} payload",
                    provider="yfinance",
                )
                self._close_cache[cache_key] = result
                return result
            import pandas as pd  # lazy import
            close = pd.to_numeric(close, errors="coerce")
            close = close[~close.index.duplicated(keep="last")].sort_index().dropna()
            if len(close) == 0:
                result = DataResult.failure(
                    DATA_PROVIDER_ERROR,
                    "malformed_response",
                    "provider Close payload contained no numeric rows",
                    provider="yfinance",
                )
                self._close_cache[cache_key] = result
                return result
            observed_at = self._series_observed_at(close)
            if observed_at is None:
                result = DataResult.failure(
                    DATA_PROVIDER_ERROR,
                    "missing_provider_timestamp",
                    "provider Close payload had no usable timestamp",
                    provider="yfinance",
                )
                self._close_cache[cache_key] = result
                return result
            now = self.now()
            aware_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
            age = aware_now.astimezone(timezone.utc) - observed_at
            if age < timedelta(0):
                result = DataResult.failure(
                    DATA_PROVIDER_ERROR,
                    "future_provider_timestamp",
                    "provider timestamp is later than the evaluation time",
                    observed_at=observed_at,
                    provider="yfinance",
                )
            elif age > self._max_daily_bar_age:
                result = DataResult.failure(
                    DATA_STALE,
                    "daily_bar_too_old",
                    f"latest daily bar age {age.total_seconds() / 3600:.1f}h exceeds "
                    f"{self._max_daily_bar_age.total_seconds() / 3600:.1f}h",
                    observed_at=observed_at,
                    provider="yfinance",
                )
            else:
                result = DataResult.success(
                    close,
                    observed_at=observed_at,
                    provider="yfinance",
                    metadata={"interval": "1d", "symbol": symbol, "priceField": price_field},
                )
            self._close_cache[cache_key] = result
            return result
        except Exception as exc:  # noqa: BLE001
            result = self._provider_error(exc, "price download")
            logger.warning("market data status=%s code=%s symbol=%s", result.status, result.code, symbol)
            self._close_cache[cache_key] = result
            return result

    def _download_close(self, symbol: str, period: Optional[str] = None):
        """Compatibility scalar for older callers; structured users call the result API."""
        result = self._download_close_result(symbol, period)
        return result.value if result.ok else None

    def _last_close_result(self, symbol: str) -> DataResult:
        downloaded = self._download_close_result(symbol)
        if not downloaded.ok:
            return downloaded
        try:
            value = float(downloaded.value.iloc[-1])
        except Exception as exc:  # noqa: BLE001
            return DataResult.failure(
                DATA_CALCULATION_ERROR,
                "value_parse_error",
                f"latest Close could not be parsed ({type(exc).__name__})",
                observed_at=downloaded.observed_at,
                provider=downloaded.provider,
            )
        if not math.isfinite(value):
            return DataResult.failure(
                DATA_CALCULATION_ERROR,
                "non_finite_value",
                "latest Close is NaN or Infinity",
                observed_at=downloaded.observed_at,
                provider=downloaded.provider,
            )
        return DataResult.success(
            value,
            observed_at=downloaded.observed_at,
            provider=downloaded.provider,
            metadata=downloaded.metadata,
        )

    def _last_close(self, symbol: str) -> Optional[float]:
        result = self._last_close_result(symbol)
        return result.value if result.ok else None

    # --- metrics -------------------------------------------------------------

    def get_metric_result(self, metric: Optional[MetricId]) -> DataResult:
        """Return a metric value with absence/error/freshness classification."""
        if metric is None:
            return DataResult.failure(DATA_INVALID_INPUT, "missing_metric", "metric is missing")
        kind = metric.metric
        try:
            if kind == "rsi":
                if not (metric.ticker or "").strip():
                    return DataResult.failure(DATA_INVALID_INPUT, "missing_ticker", "RSI ticker is empty")
                return self._get_rsi_result(metric.ticker or "", metric.period or 14)
            if kind == "vix":
                return self._last_close_result("^VIX")
            if kind == "price":
                if not (metric.ticker or "").strip():
                    return DataResult.failure(DATA_INVALID_INPUT, "missing_ticker", "price ticker is empty")
                return self._last_close_result(_resolve_symbol(metric.ticker or ""))
            if kind == "fx":
                symbol = _resolve_fx_symbol(metric.pair or "")
                if not symbol:
                    return DataResult.failure(DATA_INVALID_INPUT, "missing_fx_pair", "FX pair is empty")
                return self._last_close_result(symbol)
            if kind == "gold":
                return self._last_close_result("GC=F")
            if kind == "bitcoin":
                return self._last_close_result("BTC-USD")
            if kind == "koreanEtf":
                symbol = _resolve_korean_etf_symbol(metric.code or "")
                if not symbol:
                    return DataResult.failure(DATA_INVALID_INPUT, "missing_etf_code", "Korean ETF code is empty")
                return self._last_close_result(symbol)
        except Exception as exc:  # noqa: BLE001
            result = DataResult.failure(
                DATA_CALCULATION_ERROR,
                "metric_calculation_error",
                f"metric calculation failed ({type(exc).__name__})",
            )
            logger.warning("metric status=%s code=%s kind=%s", result.status, result.code, kind)
            return result
        logger.warning("Unknown metric kind: %s", kind)
        return DataResult.failure(DATA_INVALID_INPUT, "unknown_metric", f"unsupported metric kind: {kind}")

    def get_metric(self, metric: Optional[MetricId]) -> Optional[float]:
        result = self.get_metric_result(metric)
        return result.value if result.ok else None

    def _get_rsi_result(self, ticker: str, period: int) -> DataResult:
        """Compute Wilder RSI via alert_engine.rsi.compute_rsi."""
        # Preserve the test/extension seam that predates structured results:
        # subclasses may inject a deterministic Series by overriding
        # ``_download_close``. Production uses ``_download_close_result``.
        if type(self)._download_close is not AlertDataSource._download_close:
            close = self._download_close(_resolve_symbol(ticker), period="1y")
            downloaded = (
                DataResult.success(close, observed_at=self._series_observed_at(close))
                if close is not None
                else DataResult.failure(DATA_NO_DATA, "symbol_no_data", "close history returned no rows")
            )
        else:
            downloaded = self._download_close_result(_resolve_symbol(ticker), period="1y")
        if not downloaded.ok:
            return downloaded
        close = downloaded.value
        if len(close) <= period:
            return DataResult.failure(
                DATA_NO_DATA,
                "insufficient_rsi_history",
                f"RSI({period}) requires at least {period + 1} closes; received {len(close)}",
                observed_at=downloaded.observed_at,
                provider=downloaded.provider,
            )
        try:
            rsi_series = compute_rsi(close, period)
            if rsi_series is None or len(rsi_series) == 0:
                return DataResult.failure(
                    DATA_NO_DATA,
                    "insufficient_rsi_history",
                    f"RSI({period}) produced no usable values",
                    observed_at=downloaded.observed_at,
                    provider=downloaded.provider,
                )
            value = float(rsi_series.iloc[-1])
            if not math.isfinite(value):
                return DataResult.failure(
                    DATA_CALCULATION_ERROR,
                    "non_finite_rsi",
                    "RSI result is NaN or Infinity",
                    observed_at=downloaded.observed_at,
                    provider=downloaded.provider,
                )
            return DataResult.success(
                value,
                observed_at=downloaded.observed_at,
                provider=downloaded.provider,
                metadata={**downloaded.metadata, "period": period},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("compute_rsi failed ticker=%s error=%s", ticker, type(exc).__name__)
            return DataResult.failure(
                DATA_CALCULATION_ERROR,
                "rsi_calculation_error",
                f"RSI calculation failed ({type(exc).__name__})",
                observed_at=downloaded.observed_at,
                provider=downloaded.provider,
            )

    def _get_rsi(self, ticker: str, period: int) -> Optional[float]:
        result = self._get_rsi_result(ticker, period)
        return result.value if result.ok else None

    # --- ratio ---------------------------------------------------------------

    def get_ratio_result(self, numerator: str, denominator: str) -> DataResult:
        """Return numerator/denominator without combining unavailable or skewed quotes."""
        numerator_symbol = _resolve_symbol(numerator)
        denominator_symbol = _resolve_symbol(denominator)
        if not numerator_symbol or not denominator_symbol:
            return DataResult.failure(
                DATA_INVALID_INPUT,
                "missing_ratio_symbol",
                "ratio numerator and denominator are required",
            )
        num = self._last_close_result(numerator_symbol)
        if not num.ok:
            return DataResult.failure(
                num.status,
                f"numerator_{num.code or num.status}",
                f"numerator {numerator_symbol}: {num.detail or num.status}",
                observed_at=num.observed_at,
                provider=num.provider,
            )
        den = self._last_close_result(denominator_symbol)
        if not den.ok:
            return DataResult.failure(
                den.status,
                f"denominator_{den.code or den.status}",
                f"denominator {denominator_symbol}: {den.detail or den.status}",
                observed_at=den.observed_at,
                provider=den.provider,
            )
        if den.value == 0:
            return DataResult.failure(
                DATA_CALCULATION_ERROR,
                "ratio_zero_denominator",
                f"denominator {denominator_symbol} is zero",
                observed_at=den.observed_at,
                provider=den.provider,
            )
        if num.observed_at and den.observed_at:
            skew = abs(num.observed_at - den.observed_at)
            if skew > self._ratio_max_timestamp_skew:
                return DataResult.failure(
                    DATA_STALE,
                    "ratio_timestamp_mismatch",
                    f"ratio quote timestamps differ by {skew.total_seconds() / 3600:.1f}h; "
                    f"maximum is {self._ratio_max_timestamp_skew.total_seconds() / 3600:.1f}h",
                    observed_at=min(num.observed_at, den.observed_at),
                    provider="yfinance",
                    metadata={
                        "numeratorObservedAt": num.observed_at.isoformat(),
                        "denominatorObservedAt": den.observed_at.isoformat(),
                    },
                )
        value = num.value / den.value
        if not math.isfinite(value):
            return DataResult.failure(
                DATA_CALCULATION_ERROR,
                "non_finite_ratio",
                "ratio result is NaN or Infinity",
                provider="yfinance",
            )
        return DataResult.success(
            value,
            observed_at=min(
                (timestamp for timestamp in (num.observed_at, den.observed_at) if timestamp),
                default=None,
            ),
            provider="yfinance",
            metadata={
                "numerator": numerator_symbol,
                "denominator": denominator_symbol,
                "numeratorObservedAt": num.observed_at.isoformat() if num.observed_at else None,
                "denominatorObservedAt": den.observed_at.isoformat() if den.observed_at else None,
            },
        )

    def get_ratio(self, numerator: str, denominator: str) -> Optional[float]:
        result = self.get_ratio_result(numerator, denominator)
        return result.value if result.ok else None

    # --- dividend ------------------------------------------------------------

    def get_dividend_metric_result(self, ticker: str) -> DataResult:
        """Return the most recent dividend amount for ``ticker`` via yfinance.

        Defensive: returns None when unavailable. Calendar-driven ex-dividend
        date alerts are handled by the date evaluator; this is the numeric
        payout metric used by dividend threshold conditions.
        """
        symbol = _resolve_symbol(ticker)
        if not symbol:
            return DataResult.failure(DATA_INVALID_INPUT, "missing_ticker", "dividend ticker is empty")
        try:
            import yfinance as yf  # lazy import
        except Exception as exc:  # noqa: BLE001
            return self._provider_error(exc, "dividend provider import")
        try:
            divs = yf.Ticker(symbol).dividends
            if divs is None or len(divs) == 0:
                return DataResult.failure(
                    DATA_NO_DATA,
                    "dividend_no_data",
                    "provider returned no dividend history",
                    provider="yfinance",
                )
            value = float(divs.iloc[-1])
            observed_at = self._series_observed_at(divs)
            if not math.isfinite(value):
                return DataResult.failure(
                    DATA_CALCULATION_ERROR,
                    "non_finite_dividend",
                    "latest dividend is NaN or Infinity",
                    observed_at=observed_at,
                    provider="yfinance",
                )
            return DataResult.success(value, observed_at=observed_at, provider="yfinance")
        except Exception as exc:  # noqa: BLE001
            result = self._provider_error(exc, "dividend fetch")
            logger.warning("dividend status=%s code=%s ticker=%s", result.status, result.code, ticker)
            return result

    def get_dividend_metric(self, ticker: str) -> Optional[float]:
        result = self.get_dividend_metric_result(ticker)
        return result.value if result.ok else None

    # --- calendar (READ-ONLY) ------------------------------------------------

    def get_calendar_events_result(
        self,
        uid: str,
        selector: Optional[DateEventSelector],
        firestore=None,
    ) -> DataResult:
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
            firestore = self._firestore
        if firestore is None:
            from . import firestore_client as firestore  # lazy import

        source = selector.source if selector and selector.source else "calendarEvents"
        portfolio_id = selector.portfolioId if selector else None
        try:
            if source == "calendarCustomEvents":
                events = firestore.read_calendar_custom_events(uid, portfolio_id=portfolio_id)
            else:
                events = firestore.read_calendar_events(uid, portfolio_id=portfolio_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("calendar read failure source=%s error=%s", source, type(exc).__name__)
            return DataResult.failure(
                DATA_PROVIDER_ERROR,
                "calendar_read_error",
                f"calendar read failed ({type(exc).__name__})",
                provider="firestore",
            )

        match = (selector.match if selector else None) or {}
        mark_filter = (selector.markFilter if selector else None) or []
        # Custom events do not have Gorani star/heart metadata. Older rules can
        # retain the form's former default marks after switching the source.
        if source == "calendarCustomEvents":
            mark_filter = []

        result: List[Dict[str, Any]] = []
        for event in events:
            if not self._event_matches(event, match):
                continue
            if mark_filter and not self._event_has_mark(event, mark_filter):
                continue
            result.append(event)
        logger.info(
            "calendar selector source=%s read=%d filtered=%d markFilter=%s",
            source,
            len(events),
            len(result),
            mark_filter,
        )
        return DataResult.success(result, provider="firestore")

    def get_calendar_events(
        self,
        uid: str,
        selector: Optional[DateEventSelector],
        firestore=None,
    ) -> List[Dict[str, Any]]:
        result = self.get_calendar_events_result(uid, selector, firestore)
        return result.value if result.ok else []

    @staticmethod
    def _event_matches(event: Dict[str, Any], match: Dict[str, Any]) -> bool:
        if not match:
            return True
        event_id = match.get("eventId")
        if event_id and str(event_id).strip() not in calendar_event_identity_keys(event):
            return False
        event_date = match.get("date")
        if event_date and str(event.get("date", ""))[:10] != str(event_date)[:10]:
            return False
        ticker = match.get("ticker")
        if ticker and str(event.get("ticker", "")).upper() != str(ticker).upper():
            return False
        accepted_types = calendar_selector_match_types(match)
        if accepted_types:
            # Old UI hints stored these two values even though the calendar's
            # actual persisted codes are ex_div/buy_by. `buy_by_minus_1` is an
            # alert-only selector: it reads the same buy_by source event and
            # DateEvaluator derives the notification date one calendar day
            # earlier. Keep existing rules functional while new rules write
            # canonical selector codes.
            accepted_types = {
                "buy_by" if normalize_calendar_event_type(item) == "buy_by_minus_1"
                else normalize_calendar_event_type(item)
                for item in accepted_types
            }
            if accepted_types and normalize_calendar_event_type(event.get("type")) not in accepted_types:
                return False
        contains = calendar_selector_title_contains(match)
        if contains:
            title = str(event.get("title", "")).casefold()
            if contains.casefold() not in title:
                return False
        return True

    @staticmethod
    def _event_has_mark(event: Dict[str, Any], mark_filter: List[str]) -> bool:
        """Selected marks use the existing Gorani UI contract: logical OR."""
        for mark in mark_filter:
            if mark == "star" and bool(event.get("star")):
                return True
            if mark == "heart" and bool(event.get("heart")):
                return True
        return False
