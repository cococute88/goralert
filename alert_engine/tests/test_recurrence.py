"""Recurrence engine regression — Asia/Seoul cadence + eventId determinism.

Maps to: REQ-035.1/2/3/6. Verifies:
- biweekly Saturday (US-001 default) lands on Saturdays, 14 days apart;
- monthlyLastDay resolves the true last calendar day (incl. leap February);
- monthlyFirstDay resolves the 1st of the (next) month;
- weekly resolves the next matching weekday;
- bucket_time is stable for the same occurrence (eventId idempotency);
- calendar recurrence is event-driven -> next_occurrence None / due_now False.

Weekday convention follows the TS layer: 0=Sun .. 6=Sat.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from alert_engine.event import make_event_id
from alert_engine.models import Recurrence, TriggerPolicy
from alert_engine.recurrence import bucket_time, due_now, get_tz, next_occurrence

KST = get_tz("Asia/Seoul")


def _kst(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


def test_biweekly_saturday_lands_on_saturday():
    rec = Recurrence(kind="biweekly", weekday=6, time="08:00", tz="Asia/Seoul")
    occ = next_occurrence(rec, _kst(2024, 5, 1))  # Wed 2024-05-01
    assert occ is not None
    assert occ.weekday() == 5            # Python: Saturday == 5
    assert occ.date().isoformat() == "2024-05-04"
    assert (occ.hour, occ.minute) == (8, 0)


def test_biweekly_anchored_occurrences_are_14_days_apart():
    rec = Recurrence(kind="biweekly", weekday=6, time="08:00", tz="Asia/Seoul", anchorDate="2024-05-04")
    occ1 = next_occurrence(rec, _kst(2024, 5, 4))
    occ2 = next_occurrence(rec, _kst(2024, 5, 5))  # day after first -> next cadence
    assert occ1 is not None and occ2 is not None
    assert occ1.weekday() == 5 and occ2.weekday() == 5
    assert (occ2 - occ1) == timedelta(days=14)


def test_monthly_last_day_handles_leap_february():
    rec = Recurrence(kind="monthlyLastDay", time="09:00", tz="Asia/Seoul")
    occ = next_occurrence(rec, _kst(2024, 2, 10))  # 2024 is a leap year
    assert occ is not None
    assert occ.date().isoformat() == "2024-02-29"
    assert (occ.hour, occ.minute) == (9, 0)


def test_monthly_last_day_rolls_to_next_month_when_past():
    rec = Recurrence(kind="monthlyLastDay", time="09:00", tz="Asia/Seoul")
    # Already past Jan 31 09:00 -> next is Feb 29 (2024 leap).
    occ = next_occurrence(rec, _kst(2024, 1, 31, 23, 0))
    assert occ.date().isoformat() == "2024-02-29"


def test_monthly_first_day_rolls_to_next_month():
    rec = Recurrence(kind="monthlyFirstDay", time="09:00", tz="Asia/Seoul")
    occ = next_occurrence(rec, _kst(2024, 5, 15))
    assert occ.date().isoformat() == "2024-06-01"


def test_weekly_next_weekday():
    rec = Recurrence(kind="weekly", weekday=1, time="09:00", tz="Asia/Seoul")  # Monday
    occ = next_occurrence(rec, _kst(2024, 5, 1))  # Wed
    assert occ.weekday() == 0            # Python: Monday == 0
    assert occ.date().isoformat() == "2024-05-06"


def test_due_now_true_at_occurrence_false_otherwise():
    rec = Recurrence(kind="weekly", weekday=6, time="08:00", tz="Asia/Seoul")
    occ = next_occurrence(rec, _kst(2024, 5, 1))
    assert due_now(rec, occ, window_minutes=15) is True
    # Far from any occurrence -> not due.
    assert due_now(rec, occ + timedelta(hours=5), window_minutes=15) is False


def test_bucket_time_stable_for_same_occurrence():
    rec = Recurrence(kind="weekly", weekday=6, time="08:00", tz="Asia/Seoul")
    trigger = TriggerPolicy(mode="recurring", recurrence=rec)
    occ = next_occurrence(rec, _kst(2024, 5, 1))
    b1 = bucket_time(occ, trigger, window_minutes=15)
    b2 = bucket_time(occ, trigger, window_minutes=15)
    assert b1 == b2  # deterministic bucket -> deterministic eventId
    # eventId derived from the bucket is identical across calls.
    assert make_event_id("rule-x", b1) == make_event_id("rule-x", b2)


def test_bucket_time_same_across_window_for_scheduled_rule():
    rec = Recurrence(kind="weekly", weekday=6, time="08:00", tz="Asia/Seoul")
    trigger = TriggerPolicy(mode="recurring", recurrence=rec)
    occ = next_occurrence(rec, _kst(2024, 5, 1))
    # Two instants shortly after the occurrence map to the same scheduled bucket.
    b1 = bucket_time(occ + timedelta(minutes=2), trigger, window_minutes=15)
    b2 = bucket_time(occ + timedelta(minutes=10), trigger, window_minutes=15)
    assert b1 == b2 == occ.isoformat()


def test_calendar_recurrence_is_event_driven():
    rec = Recurrence(kind="calendar", tz="Asia/Seoul")
    assert next_occurrence(rec, _kst(2024, 5, 1)) is None
    assert due_now(rec, _kst(2024, 5, 1)) is False
