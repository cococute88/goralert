"""Outage backlog quarantine. Planning is read-only; applying is transactional."""
from datetime import timedelta

from .recurrence import as_utc, first_future_scheduled_occurrence


def backlog_plan(rule, scheduled_for, now, max_age_minutes=1440):
    if as_utc(now) - as_utc(scheduled_for) <= timedelta(minutes=max_age_minutes):
        return None
    recurrence = rule.trigger.recurrence
    # Keep any recent due occurrence, rather than blindly jumping to 'now'.
    boundary = now - timedelta(minutes=max_age_minutes)
    follows_schedule = rule.trigger.mode == "recurring" or recurrence.kind == "calendar"
    next_cursor = first_future_scheduled_occurrence(recurrence, boundary) if follows_schedule else None
    return {
        "policy": "hold_for_operator_review",
        "fromInclusive": as_utc(scheduled_for).isoformat(),
        "untilExclusive": as_utc(next_cursor).isoformat() if next_cursor else None,
        "nextCursor": as_utc(next_cursor) if next_cursor else None,
        "detectedAt": as_utc(now).isoformat(),
        "maxAgeMinutes": max_age_minutes,
    }


def protect_backlog(engine, rule, scheduled_for, now, dry_run, resume_event_id):
    from .engine import ProcessResult, STATUS_DRY_RUN, STATUS_ERROR
    recovery = rule.schedulerRecovery or {}
    from .event import make_event_id
    occurrence_id = make_event_id(rule.id, scheduled_for.isoformat())
    if (recovery.get("status") in {"requested", "processing"}
            and recovery.get("occurrenceId") == occurrence_id
            and recovery.get("duplicateRiskAcknowledged") is True):
        return None
    plan = backlog_plan(rule, scheduled_for, now, engine.config.backlog_max_age_minutes)
    if plan is None:
        return None
    if dry_run:
        return ProcessResult(rule.id, rule.uid, STATUS_DRY_RUN, "backlog would be held; no writes or delivery")
    try:
        outcome = engine.firestore.hold_scheduler_backlog(rule, plan, resume_event_id, now)
    except Exception:
        return ProcessResult(rule.id, rule.uid, STATUS_ERROR, "backlog_hold_unavailable")
    return ProcessResult(rule.id, rule.uid, "recovery_held", outcome, event_id=occurrence_id)
