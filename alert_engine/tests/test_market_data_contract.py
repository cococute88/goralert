"""Market-data result, freshness, ratio, and durable evaluation contracts."""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas", reason="pandas required for market-data contract tests")

from alert_engine.data import DataResult
from alert_engine.datasource import AlertDataSource
from alert_engine.engine import (
    STATUS_DELIVERED,
    STATUS_NO_DATA,
    STATUS_NOT_TRIGGERED,
)
from alert_engine.models import MetricId, Recurrence

from .conftest import FakeChannel, FakeDataSource, FakeFirestore, build_engine, make_metric_rule


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _frame(values, observed_at):
    return pd.DataFrame(
        {"Close": values},
        index=pd.date_range(end=observed_at, periods=len(values), freq="D", tz="UTC"),
    )


def test_price_result_is_finite_and_carries_provider_timestamp(monkeypatch):
    frame = _frame([99.0, 101.5], NOW - timedelta(hours=12))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=lambda *args, **kwargs: frame))

    result = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker=" aapl ")
    )

    assert result.status == "ok"
    assert result.value == pytest.approx(101.5)
    assert result.observed_at == frame.index[-1].to_pydatetime()


def test_single_symbol_multiindex_close_is_normalized(monkeypatch):
    index = pd.date_range(end=NOW - timedelta(hours=1), periods=2, freq="D", tz="UTC")
    frame = pd.DataFrame(
        [[99.0, 98.0], [101.5, 100.0]],
        index=index,
        columns=pd.MultiIndex.from_tuples(
            [("Close", "MSFT"), ("Open", "MSFT")],
            names=["Price", "Ticker"],
        ),
    )
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=lambda *args, **kwargs: frame))

    result = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="MSFT")
    )

    assert result.status == "ok"
    assert result.value == pytest.approx(101.5)
    assert result.metadata["priceField"] == "Close"


def test_multiindex_selects_requested_symbol_when_other_symbol_is_present(monkeypatch):
    index = pd.date_range(end=NOW - timedelta(hours=1), periods=2, freq="D", tz="UTC")
    frame = pd.DataFrame(
        [[99.0, 59.0], [101.5, 61.25]],
        index=index,
        columns=pd.MultiIndex.from_tuples(
            [("Close", "MSFT"), ("Close", "SCHD")],
            names=["Price", "Ticker"],
        ),
    )
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=lambda *args, **kwargs: frame))

    result = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="MSFT")
    )

    assert result.status == "ok"
    assert result.value == pytest.approx(101.5)


def test_ambiguous_flat_close_columns_are_rejected(monkeypatch):
    index = pd.date_range(end=NOW - timedelta(hours=1), periods=2, freq="D", tz="UTC")
    frame = pd.DataFrame(
        [[99.0, 59.0], [101.5, 61.25]],
        index=index,
        columns=["Close", "Close"],
    )
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=lambda *args, **kwargs: frame))

    result = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="MSFT")
    )

    assert (result.status, result.code) == ("provider_error", "malformed_response")


def test_reversed_multiindex_and_adj_close_only_are_supported(monkeypatch):
    index = pd.date_range(end=NOW - timedelta(hours=1), periods=2, freq="D", tz="UTC")
    reversed_frame = pd.DataFrame(
        [[99.0], [101.5]],
        index=index,
        columns=pd.MultiIndex.from_tuples(
            [("MSFT", "Close")],
            names=["Ticker", "Price"],
        ),
    )
    adj_only_frame = pd.DataFrame({"Adj Close": [60.0, 61.25]}, index=index)
    frames = {"MSFT": reversed_frame, "SCHD": adj_only_frame}
    monkeypatch.setitem(
        sys.modules,
        "yfinance",
        SimpleNamespace(download=lambda symbol, **kwargs: frames[symbol]),
    )
    source = AlertDataSource(now_fn=lambda: NOW)

    msft = source.get_metric_result(MetricId(metric="price", ticker="MSFT"))
    schd = source.get_metric_result(MetricId(metric="price", ticker="SCHD"))
    ratio = source.get_ratio_result("MSFT", "SCHD")

    assert (msft.status, msft.value, msft.metadata["priceField"]) == ("ok", 101.5, "Close")
    assert (schd.status, schd.value, schd.metadata["priceField"]) == ("ok", 61.25, "Adj Close")
    assert ratio.status == "ok"
    assert ratio.value == pytest.approx(101.5 / 61.25)


def test_mixed_string_numeric_close_keeps_usable_rows(monkeypatch):
    frame = _frame(["not-a-number", "100.25", 101], NOW - timedelta(hours=1))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=lambda *args, **kwargs: frame))

    result = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="MSFT")
    )

    assert result.status == "ok"
    assert result.value == pytest.approx(101.0)


def test_close_normalization_sorts_deduplicates_and_accepts_naive_time(monkeypatch):
    duplicate = datetime(2026, 8, 1, 0, 0)
    frame = pd.DataFrame(
        {"Close": ["101.5", math.nan, "100.25", "102.0"]},
        index=pd.DatetimeIndex([
            duplicate,
            datetime(2026, 7, 31, 0, 0),
            datetime(2026, 7, 30, 0, 0),
            duplicate,
        ]),
    )
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=lambda *args, **kwargs: frame))

    result = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="MSFT")
    )

    assert result.status == "ok"
    assert result.value == pytest.approx(102.0)
    assert result.observed_at == datetime(2026, 8, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("metric", "expected_symbol"),
    [
        (MetricId(metric="price", ticker=" aapl "), "AAPL"),
        (MetricId(metric="vix"), "^VIX"),
        (MetricId(metric="fx", pair="usd/krw"), "USDKRW=X"),
        (MetricId(metric="gold"), "GC=F"),
        (MetricId(metric="bitcoin"), "BTC-USD"),
        (MetricId(metric="koreanEtf", code="069500"), "069500.KS"),
    ],
)
def test_supported_metric_kinds_use_the_expected_provider_symbol(
    monkeypatch,
    metric,
    expected_symbol,
):
    frame = _frame([99.0, 101.5], NOW - timedelta(hours=12))
    calls = []

    def download(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return frame

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=download))

    result = AlertDataSource(now_fn=lambda: NOW).get_metric_result(metric)

    assert result.status == "ok"
    assert result.value == pytest.approx(101.5)
    assert calls[0][0] == expected_symbol
    assert calls[0][1]["interval"] == "1d"
    assert calls[0][1]["timeout"] == 10.0


def test_price_provider_timeout_and_malformed_response_are_distinct(monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=timeout))
    timed_out = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="AAPL")
    )
    assert timed_out.status == "provider_error"
    assert timed_out.code == "provider_timeout"

    malformed = pd.DataFrame(
        {"Open": [1.0]},
        index=pd.DatetimeIndex([NOW - timedelta(hours=1)]),
    )
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=lambda *args, **kwargs: malformed))
    bad_payload = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="AAPL")
    )
    assert bad_payload.status == "provider_error"
    assert bad_payload.code == "malformed_response"


def test_ticker_history_json_parse_exception_is_provider_error(monkeypatch):
    class BrokenTicker:
        def history(self, **kwargs):
            raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setitem(
        sys.modules,
        "yfinance",
        SimpleNamespace(Ticker=lambda symbol: BrokenTicker()),
    )

    result = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="MSFT")
    )

    assert result.status == "provider_error"
    assert result.code == "provider_response_parse_error"


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (RuntimeError("HTTP 429 rate limit"), "provider_rate_limit"),
        (PermissionError("HTTP 401 unauthorized"), "provider_authentication_error"),
    ],
)
def test_provider_rate_limit_and_authentication_errors_are_classified(monkeypatch, error, expected_code):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=fail))
    result = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="AAPL")
    )
    assert result.status == "provider_error"
    assert result.code == expected_code


def test_empty_provider_result_and_blank_ticker_are_distinct(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "yfinance",
        SimpleNamespace(download=lambda *args, **kwargs: pd.DataFrame()),
    )
    no_rows = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="NOT-A-REAL-SYMBOL")
    )
    invalid = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker=" ")
    )
    assert (no_rows.status, no_rows.code) == ("no_data", "symbol_no_data")
    assert (invalid.status, invalid.code) == ("invalid_input", "missing_ticker")


def test_empty_frame_with_yfinance_parse_error_is_provider_error(monkeypatch):
    fake_yfinance = SimpleNamespace(
        download=lambda *args, **kwargs: pd.DataFrame(),
        shared=SimpleNamespace(
            _ERRORS={"MSFT": "JSONDecodeError('Expecting value: line 1 column 1 (char 0)')"},
        ),
    )
    monkeypatch.setitem(sys.modules, "yfinance", fake_yfinance)

    result = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="MSFT")
    )

    assert result.status == "provider_error"
    assert result.code == "provider_response_parse_error"
    assert result.metadata == {"symbol": "MSFT"}


def test_stale_price_and_non_finite_price_are_not_comparable(monkeypatch):
    stale_frame = _frame([100.0], NOW - timedelta(hours=73))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=lambda *args, **kwargs: stale_frame))
    stale = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="AAPL")
    )
    assert stale.status == "stale_data"
    assert stale.code == "daily_bar_too_old"

    infinite_frame = _frame([math.inf], NOW - timedelta(hours=1))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=lambda *args, **kwargs: infinite_frame))
    invalid = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="price", ticker="AAPL")
    )
    assert invalid.status == "calculation_error"
    assert invalid.code == "non_finite_value"


def test_rsi_insufficient_history_is_no_data(monkeypatch):
    frame = _frame([float(100 + value) for value in range(14)], NOW - timedelta(hours=1))
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=lambda *args, **kwargs: frame))

    result = AlertDataSource(now_fn=lambda: NOW).get_metric_result(
        MetricId(metric="rsi", ticker="KOSPI", period=14)
    )

    assert result.status == "no_data"
    assert result.code == "insufficient_rsi_history"


class _RatioDataSource(AlertDataSource):
    def __init__(self, rows):
        super().__init__(now_fn=lambda: NOW)
        self.rows = rows

    def _last_close_result(self, symbol):
        return self.rows[symbol]


def test_ratio_direction_zero_denominator_and_timestamp_skew():
    recent = NOW - timedelta(hours=1)
    direction = _RatioDataSource({
        "MSFT": DataResult.success(180.0, observed_at=recent),
        "SCHD": DataResult.success(60.0, observed_at=recent),
    }).get_ratio_result(" msft ", " schd ")
    assert direction.status == "ok"
    assert direction.value == pytest.approx(3.0)

    zero = _RatioDataSource({
        "MSFT": DataResult.success(180.0, observed_at=recent),
        "SCHD": DataResult.success(0.0, observed_at=recent),
    }).get_ratio_result("MSFT", "SCHD")
    assert zero.status == "calculation_error"
    assert zero.code == "ratio_zero_denominator"

    mismatch = _RatioDataSource({
        "MSFT": DataResult.success(180.0, observed_at=recent),
        "SCHD": DataResult.success(60.0, observed_at=recent - timedelta(hours=37)),
    }).get_ratio_result("MSFT", "SCHD")
    assert mismatch.status == "stale_data"
    assert mismatch.code == "ratio_timestamp_mismatch"

    numerator_only = _RatioDataSource({
        "MSFT": DataResult.success(180.0, observed_at=recent),
        "SCHD": DataResult.failure("no_data", "symbol_no_data", "no rows"),
    }).get_ratio_result("MSFT", "SCHD")
    assert numerator_only.status == "no_data"
    assert numerator_only.code == "denominator_symbol_no_data"


class _NoDataMetricSource(FakeDataSource):
    def get_metric_result(self, metric):
        return DataResult.failure("no_data", "symbol_no_data", "provider returned no rows")


def _scheduled_metric_rule(comparator="gte", threshold=70.0):
    rule = make_metric_rule(comparator=comparator, threshold=threshold, channels=["telegram"])
    rule.trigger.recurrence = Recurrence(
        kind="calendar",
        weekday=None,
        time="15:35",
        tz="Asia/Seoul",
        anchorDate=None,
    )
    rule.durableSchedulerVersion = 1
    rule.nextScheduledAt = NOW
    return rule


def test_scheduled_no_data_is_recorded_without_overwriting_previous_value():
    firestore = FakeFirestore()
    engine = build_engine(
        _NoDataMetricSource(),
        firestore,
        {"telegram": FakeChannel("telegram")},
    )
    rule = _scheduled_metric_rule(comparator="crossUp")
    rule.lastValue = 55.0

    result = engine.process_rule(rule, now=NOW)

    assert result.status == STATUS_NO_DATA
    log = firestore.logs[result.event_id]
    assert log.status == "no_data"
    assert log.failureCode == "symbol_no_data"
    assert all(update.get("lastValue") is None for update in firestore.state_updates)


def test_scheduled_crossing_persists_false_observation_then_fires_once():
    firestore = FakeFirestore()
    telegram = FakeChannel("telegram")
    source = FakeDataSource(metric=55.0)
    engine = build_engine(source, firestore, {"telegram": telegram})
    rule = _scheduled_metric_rule(comparator="crossUp")

    first = engine.process_rule(rule, now=NOW)
    assert first.status == STATUS_NOT_TRIGGERED
    assert firestore.state_updates[-1]["lastValue"] == 55.0

    rule.lastValue = 55.0
    rule.nextScheduledAt = NOW + timedelta(days=1)
    source._metric = 75.0
    second = engine.process_rule(rule, now=NOW + timedelta(days=1))
    assert second.status == STATUS_DELIVERED
    assert telegram.calls == 1
