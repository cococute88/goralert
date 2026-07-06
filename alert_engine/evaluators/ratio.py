"""Ratio threshold evaluator (e.g. SPY/SCHD >= 25 — US-004/005)."""

from __future__ import annotations

from ..compare import compare
from ..models import AlertRule, Condition
from .base import EvalContext, EvalResult


class RatioEvaluator:
    def __init__(self, datasource):
        self._ds = datasource

    def evaluate(self, rule: AlertRule, condition: Condition, ctx: EvalContext) -> EvalResult:
        if not condition.numerator or not condition.denominator:
            return EvalResult(False, None, detail="ratio: missing numerator/denominator")
        value = self._ds.get_ratio(condition.numerator, condition.denominator)
        if value is None:
            return EvalResult(
                False, None,
                detail=f"ratio {condition.numerator}/{condition.denominator}: no data (skipped)",
            )
        triggered = compare(value, condition.comparator, condition.threshold, prev=ctx.prev_value)
        detail = (
            f"ratio {condition.numerator}/{condition.denominator}={value:.4f} "
            f"{condition.comparator} {condition.threshold} -> {triggered}"
        )
        return EvalResult(triggered, value, detail)
