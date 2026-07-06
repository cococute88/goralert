"""RSI reuse — the metric path uses alert_engine.rsi.compute_rsi.

Maps to: REQ-038.3 / REQ-033.1. The engine uses the Wilder RSI
implementation from ``alert_engine/rsi.py``. We feed a known close series
through the real ``AlertDataSource`` (with only the network download stubbed)
and assert:
- the value returned by the metric path equals ``compute_rsi(...).iloc[-1]``
  exactly, and
- the MetricEvaluator triggers against an RSI threshold accordingly.

Skipped entirely when pandas is unavailable (offline CI).
"""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas", reason="pandas required for RSI reuse test")

from alert_engine.datasource import AlertDataSource
from alert_engine.rsi import compute_rsi
from alert_engine.evaluators.metric import MetricEvaluator
from alert_engine.evaluators.base import EvalContext
from alert_engine.models import Condition, MetricId

from datetime import datetime, timezone


class _StubDataSource(AlertDataSource):
    """Real AlertDataSource with only the price download stubbed (no network)."""

    def __init__(self, close_series):
        super().__init__()
        self._stub_close = close_series

    def _download_close(self, symbol, period=None):
        return self._stub_close


def _rule_stub():
    from alert_engine.models import AlertRule
    return AlertRule.from_dict({
        "id": "r-rsi", "uid": "u1", "kind": "rsi", "name": "rsi", "enabled": True,
        "condition": {"kind": "rsi"}, "trigger": {}, "delivery": {"channels": ["telegram"]},
    })


def test_metric_path_value_equals_compute_rsi():
    # Strictly increasing closes -> all gains -> RSI == 100 (per Wilder).
    closes = pd.Series([float(100 + i) for i in range(40)])
    ds = _StubDataSource(closes)
    metric = MetricId(metric="rsi", ticker="KOSPI", period=14)

    value = ds.get_metric(metric)
    expected = float(compute_rsi(closes, 14).iloc[-1])

    assert value is not None
    assert value == pytest.approx(expected)
    assert value == pytest.approx(100.0)


def test_metric_evaluator_triggers_on_rsi_threshold():
    closes = pd.Series([float(100 + i) for i in range(40)])
    ds = _StubDataSource(closes)
    evaluator = MetricEvaluator(ds)
    rule = _rule_stub()
    cond = Condition(kind="rsi", metric=MetricId(metric="rsi", ticker="KOSPI", period=14),
                     comparator="gte", threshold=70.0)
    ctx = EvalContext(uid="u1", now=datetime(2024, 5, 1, tzinfo=timezone.utc))

    result = evaluator.evaluate(rule, cond, ctx)
    assert result.triggered is True
    assert result.value == pytest.approx(100.0)


def test_metric_evaluator_not_triggered_when_below_threshold():
    closes = pd.Series([float(100 + i) for i in range(40)])  # RSI ~100
    ds = _StubDataSource(closes)
    evaluator = MetricEvaluator(ds)
    rule = _rule_stub()
    cond = Condition(kind="rsi", metric=MetricId(metric="rsi", ticker="KOSPI", period=14),
                     comparator="lte", threshold=30.0)
    ctx = EvalContext(uid="u1", now=datetime(2024, 5, 1, tzinfo=timezone.utc))

    result = evaluator.evaluate(rule, cond, ctx)
    assert result.triggered is False


def test_downtrend_rsi_matches_and_is_low():
    # Strictly decreasing closes -> all losses -> RSI == 0.
    closes = pd.Series([float(200 - i) for i in range(40)])
    ds = _StubDataSource(closes)
    metric = MetricId(metric="rsi", ticker="KOSPI", period=14)

    value = ds.get_metric(metric)
    expected = float(compute_rsi(closes, 14).iloc[-1])
    assert value == pytest.approx(expected)
    assert value == pytest.approx(0.0)
