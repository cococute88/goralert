"""Date / calendar evaluator.

Handles two flavors of ``kind:"date"`` rules:

1. Pure scheduled date (no selector): the cadence lives entirely in
   ``trigger.recurrence`` (biweekly/monthlyLastDay/monthlyFirstDay/weekly). The
   engine's recurrence ``due_now`` gate decides timing; this evaluator simply
   confirms the rule should fire today (returns triggered=True with the date).

2. Calendar-driven (selector present, recurrence.kind == "calendar"): fire when
   a matching calendar event lands on the evaluation date. Honors
   selector.match {eventId,date,ticker,type,titleContains} and selector.markFilter
   (⭐ star / ❤️ heart) per US-006/US-007. The matched ticker is exposed via
   ``extra["ticker"]`` so the engine can render ``{ticker}`` in messages.
"""

from __future__ import annotations

from datetime import date, timedelta

from ..calendar_contract import (
    calendar_selector_match_types,
    normalize_calendar_event_type,
)
from ..data import DataResult
from ..models import AlertRule, Condition
from ..recurrence import get_tz
from .base import EvalContext, EvalResult


BUY_BY_MINUS_ONE_EVENT_TYPE = "buy_by_minus_1"
def _calendar_date(value: object) -> date | None:
    """Parse the date portion stored by the calendar without timezone conversion."""
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _selected_event_types(condition: Condition) -> set[str]:
    """Return normalized selector types; an empty set means every raw event type."""
    selector = condition.selector
    match = selector.match if selector and selector.match else {}
    return calendar_selector_match_types(match)


def _notification_events(events: list[dict], selected_types: set[str]) -> list[dict]:
    """Expand matching source events into their actual notification dates.

    Calendar data remains read-only. The only derived condition is
    ``buy_by_minus_1``: for each selected original ``buy_by`` event its ISO
    calendar date is reduced by exactly one day. This deliberately does not
    apply business-day, weekend, holiday, or timezone adjustments.
    """
    candidates: list[dict] = []
    for event in events:
        event_date = _calendar_date(event.get("date"))
        if event_date is None:
            continue
        raw_type = normalize_calendar_event_type(event.get("type"))

        # No event-type filter retains the existing behavior: the raw calendar
        # event fires on its own stored date.
        if not selected_types or raw_type in selected_types:
            candidates.append({**event, "notificationDate": event_date.isoformat(), "alertEventType": raw_type})

        if BUY_BY_MINUS_ONE_EVENT_TYPE in selected_types and raw_type == "buy_by":
            candidates.append({
                **event,
                "notificationDate": (event_date - timedelta(days=1)).isoformat(),
                "alertEventType": BUY_BY_MINUS_ONE_EVENT_TYPE,
                "sourceEventDate": event_date.isoformat(),
            })
    return candidates


class DateEvaluator:
    def __init__(self, datasource):
        self._ds = datasource

    def evaluate(self, rule: AlertRule, condition: Condition, ctx: EvalContext) -> EvalResult:
        recurrence = rule.trigger.recurrence if rule.trigger else None
        # Calendar event dates follow the rule's existing recurrence timezone
        # (Asia/Seoul by default), never the host/UTC date.
        calendar_tz = get_tz(recurrence.tz if recurrence else None)
        local_now = ctx.now.replace(tzinfo=calendar_tz) if ctx.now.tzinfo is None else ctx.now.astimezone(calendar_tz)
        today = local_now.date().isoformat()

        # Calendar-driven mode.
        if condition.selector is not None:
            result_method = getattr(self._ds, "get_calendar_events_result", None)
            if callable(result_method):
                data = result_method(ctx.uid, condition.selector)
            else:
                events = self._ds.get_calendar_events(ctx.uid, condition.selector)
                data = DataResult.success(events)
            if not data.ok:
                status = data.status if data.status in {"no_data", "stale_data", "provider_error"} else "evaluation_error"
                return EvalResult(
                    False,
                    None,
                    detail=f"date calendar[{condition.selector.source}]: {data.detail or data.status}",
                    status=status,
                    failure_code=data.code or data.status,
                    observed_at=data.observed_at,
                )
            events = data.value
            candidates = _notification_events(events, _selected_event_types(condition))
            todays = [event for event in candidates if event["notificationDate"] == today]
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
                f"date matched on {today} from {len(candidates)} candidate(s) "
                f"(markFilter={condition.selector.markFilter}) -> {triggered}"
            )
            return EvalResult(triggered, float(len(todays)), detail)

        # Pure scheduled date: timing is gated by the engine's recurrence check.
        return EvalResult(True, today, detail=f"scheduled date due on {today}")
