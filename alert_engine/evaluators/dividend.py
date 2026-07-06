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
from ..models import AlertRule, Condition
from .base import EvalContext, EvalResult


class DividendEvaluator:
    def __init__(self, datasource):
        self._ds = datasource

    def evaluate(self, rule: AlertRule, condition: Condition, ctx: EvalContext) -> EvalResult:
        # Numeric threshold mode.
        if condition.comparator is not None and condition.threshold is not None and condition.ticker:
            value = self._ds.get_dividend_metric(condition.ticker)
            if value is None:
                return EvalResult(False, None, detail=f"dividend {condition.ticker}: no data (skipped)")
            triggered = compare(value, condition.comparator, condition.threshold, prev=ctx.prev_value)
            detail = (
                f"dividend {condition.ticker}={value} {condition.comparator} "
                f"{condition.threshold} -> {triggered}"
            )
            return EvalResult(triggered, value, detail)

        # Calendar-driven ex-dividend mode.
        events = self._ds.get_calendar_events(ctx.uid, condition.selector)
        today = ctx.now.date().isoformat()
        todays = [e for e in events if str(e.get("date", ""))[:10] == today]
        triggered = len(todays) > 0
        detail = f"dividend calendar: {len(todays)} matching event(s) on {today} -> {triggered}"
        return EvalResult(triggered, float(len(todays)), detail)
