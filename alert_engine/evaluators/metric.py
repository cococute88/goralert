"""Metric threshold evaluator (rsi/vix/price/fx/gold/bitcoin/koreanEtf).

RSI is computed by ``alert_engine.rsi.compute_rsi`` (Wilder) through the
datasource. Structured datasource failures remain distinct from a valid false
comparison, so the engine can persist or log the real evaluation outcome.
"""

from __future__ import annotations

from ..compare import compare
from ..data import DataResult
from ..models import AlertRule, Condition
from .base import EvalContext, EvalResult


class MetricEvaluator:
    def __init__(self, datasource):
        self._ds = datasource

    def evaluate(self, rule: AlertRule, condition: Condition, ctx: EvalContext) -> EvalResult:
        if condition.comparator is None or condition.threshold is None:
            return EvalResult(
                False,
                None,
                detail=f"metric {condition.kind}: comparator or finite threshold is missing",
                status="evaluation_error",
                failure_code="invalid_metric_condition",
            )
        result_method = getattr(self._ds, "get_metric_result", None)
        if callable(result_method):
            data = result_method(condition.metric)
        else:
            value = self._ds.get_metric(condition.metric)
            data = (
                DataResult.success(value)
                if value is not None
                else DataResult.failure("no_data", "metric_no_data", "metric returned no value")
            )
        if not data.ok:
            status = data.status if data.status in {"no_data", "stale_data", "provider_error"} else "evaluation_error"
            detail = f"metric {condition.kind}: {data.detail or data.status}"
            return EvalResult(
                False,
                None,
                detail=detail,
                status=status,
                failure_code=data.code or data.status,
                observed_at=data.observed_at,
            )
        value = data.value
        triggered = compare(value, condition.comparator, condition.threshold, prev=ctx.prev_value)
        detail = (
            f"metric {condition.kind}={value:.4f} {condition.comparator} "
            f"{condition.threshold} -> {triggered}"
        )
        return EvalResult(triggered, value, detail, observed_at=data.observed_at)
