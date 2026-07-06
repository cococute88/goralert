"""Metric threshold evaluator (rsi/vix/price/fx/gold/bitcoin/koreanEtf).

Reuse: RSI is computed by ``original/logic/market.py::compute_rsi`` (Wilder) via
``AlertDataSource.get_metric`` — this evaluator never re-implements the math.
On data-fetch failure the value is None and the result is "not triggered" with
a detail note so the engine can skip+warn.
"""

from __future__ import annotations

from ..compare import compare
from ..models import AlertRule, Condition
from .base import EvalContext, EvalResult


class MetricEvaluator:
    def __init__(self, datasource):
        self._ds = datasource

    def evaluate(self, rule: AlertRule, condition: Condition, ctx: EvalContext) -> EvalResult:
        value = self._ds.get_metric(condition.metric)
        if value is None:
            return EvalResult(False, None, detail=f"metric {condition.kind}: no data (skipped)")
        triggered = compare(value, condition.comparator, condition.threshold, prev=ctx.prev_value)
        detail = (
            f"metric {condition.kind}={value:.4f} {condition.comparator} "
            f"{condition.threshold} -> {triggered}"
        )
        return EvalResult(triggered, value, detail)
