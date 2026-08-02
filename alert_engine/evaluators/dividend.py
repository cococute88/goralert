"""Dividend evaluator.

Two modes:
1. selector present  -> calendar-driven (ex-dividend events), like the date
   evaluator but scoped to dividend-type events. Triggered when at least one
   matching calendar event lands on the evaluation date.
2. comparator+threshold present -> numeric dividend-amount threshold via
   ``AlertDataSource.get_dividend_metric``.
"""

from __future__ import annotations

from ..compare import compare
from ..data import DataResult
from ..models import AlertRule, Condition
from .base import EvalContext, EvalResult


class DividendEvaluator:
    def __init__(self, datasource):
        self._ds = datasource

    def evaluate(self, rule: AlertRule, condition: Condition, ctx: EvalContext) -> EvalResult:
        # Numeric threshold mode.
        if condition.comparator is not None and condition.threshold is not None and condition.ticker:
            result_method = getattr(self._ds, "get_dividend_metric_result", None)
            if callable(result_method):
                data = result_method(condition.ticker)
            else:
                value = self._ds.get_dividend_metric(condition.ticker)
                data = (
                    DataResult.success(value)
                    if value is not None
                    else DataResult.failure("no_data", "dividend_no_data", "dividend returned no value")
                )
            if not data.ok:
                status = data.status if data.status in {"no_data", "stale_data", "provider_error"} else "evaluation_error"
                return EvalResult(
                    False,
                    None,
                    detail=f"dividend {condition.ticker}: {data.detail or data.status}",
                    status=status,
                    failure_code=data.code or data.status,
                    observed_at=data.observed_at,
                )
            value = data.value
            triggered = compare(value, condition.comparator, condition.threshold, prev=ctx.prev_value)
            detail = (
                f"dividend {condition.ticker}={value} {condition.comparator} "
                f"{condition.threshold} -> {triggered}"
            )
            return EvalResult(triggered, value, detail, observed_at=data.observed_at)

        # Calendar-driven ex-dividend mode.
        result_method = getattr(self._ds, "get_calendar_events_result", None)
        if callable(result_method):
            data = result_method(ctx.uid, condition.selector)
        else:
            data = DataResult.success(self._ds.get_calendar_events(ctx.uid, condition.selector))
        if not data.ok:
            status = data.status if data.status in {"no_data", "stale_data", "provider_error"} else "evaluation_error"
            return EvalResult(
                False,
                None,
                detail=f"dividend calendar: {data.detail or data.status}",
                status=status,
                failure_code=data.code or data.status,
                observed_at=data.observed_at,
            )
        events = data.value
        today = ctx.now.date().isoformat()
        todays = [e for e in events if str(e.get("date", ""))[:10] == today]
        triggered = len(todays) > 0
        detail = f"dividend calendar: {len(todays)} matching event(s) on {today} -> {triggered}"
        return EvalResult(triggered, float(len(todays)), detail)
