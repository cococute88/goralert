"""Date / calendar evaluator.

Handles two flavors of ``kind:"date"`` rules:

1. Pure scheduled date (no selector): the cadence lives entirely in
   ``trigger.recurrence`` (biweekly/monthlyLastDay/monthlyFirstDay/weekly). The
   engine's recurrence ``due_now`` gate decides timing; this evaluator simply
   confirms the rule should fire today (returns triggered=True with the date).

2. Calendar-driven (selector present, recurrence.kind == "calendar"): fire when
   a matching calendar event lands on the evaluation date. Honors
   selector.match {ticker,type,titleContains} and selector.markFilter
   (⭐ star / ❤️ heart) per US-006/US-007. The matched ticker is exposed via
   ``extra["ticker"]`` so the engine can render ``{ticker}`` in messages.
"""

from __future__ import annotations

from ..models import AlertRule, Condition
from .base import EvalContext, EvalResult


class DateEvaluator:
    def __init__(self, datasource):
        self._ds = datasource

    def evaluate(self, rule: AlertRule, condition: Condition, ctx: EvalContext) -> EvalResult:
        today = ctx.now.date().isoformat()

        # Calendar-driven mode.
        if condition.selector is not None:
            events = self._ds.get_calendar_events(ctx.uid, condition.selector)
            todays = [e for e in events if str(e.get("date", ""))[:10] == today]
            triggered = len(todays) > 0
            if triggered:
                # Surface the first matching ticker for message templating.
                first = todays[0]
                ticker = first.get("ticker")
                if ticker:
                    ctx.extra["ticker"] = ticker
                ctx.extra["matchedEvents"] = todays
            detail = (
                f"date calendar[{condition.selector.source}]: {len(todays)} "
                f"event(s) on {today} (markFilter={condition.selector.markFilter}) -> {triggered}"
            )
            return EvalResult(triggered, float(len(todays)), detail)

        # Pure scheduled date: timing is gated by the engine's recurrence check.
        return EvalResult(True, today, detail=f"scheduled date due on {today}")
