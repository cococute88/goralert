"""Ratio threshold evaluator (e.g. SPY/SCHD >= 25 — US-004/005)."""

from __future__ import annotations

from ..compare import compare
from ..data import DataResult
from ..models import AlertRule, Condition
from .base import EvalContext, EvalResult


class RatioEvaluator:
    def __init__(self, datasource):
        self._ds = datasource

    def evaluate(self, rule: AlertRule, condition: Condition, ctx: EvalContext) -> EvalResult:
        if not condition.numerator or not condition.denominator:
            return EvalResult(
                False,
                None,
                detail="ratio: missing numerator/denominator",
                status="evaluation_error",
                failure_code="missing_ratio_symbol",
            )
        if condition.comparator is None or condition.threshold is None:
            return EvalResult(
                False,
                None,
                detail="ratio: comparator or finite threshold is missing",
                status="evaluation_error",
                failure_code="invalid_ratio_condition",
            )
        result_method = getattr(self._ds, "get_ratio_result", None)
        if callable(result_method):
            data = result_method(condition.numerator, condition.denominator)
        else:
            value = self._ds.get_ratio(condition.numerator, condition.denominator)
            data = (
                DataResult.success(value)
                if value is not None
                else DataResult.failure("no_data", "ratio_no_data", "ratio returned no value")
            )
        if not data.ok:
            status = data.status if data.status in {"no_data", "stale_data", "provider_error"} else "evaluation_error"
            return EvalResult(
                False,
                None,
                detail=f"ratio {condition.numerator}/{condition.denominator}: {data.detail or data.status}",
                status=status,
                failure_code=data.code or data.status,
                observed_at=data.observed_at,
            )
        value = data.value
        triggered = compare(value, condition.comparator, condition.threshold, prev=ctx.prev_value)
        detail = (
            f"ratio {condition.numerator}/{condition.denominator}={value:.4f} "
            f"{condition.comparator} {condition.threshold} -> {triggered}"
        )
        return EvalResult(triggered, value, detail, observed_at=data.observed_at)
