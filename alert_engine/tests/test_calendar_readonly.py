"""Property 6 — 캘린더 읽기 전용 (calendar is read-only during evaluation).

Maps to: REQ-013.2 / REQ-004.2. The evaluation path may READ calendar
collections (calendarEvents / calendarCustomEvents) but must perform ZERO
writes to them (or to RTDB). We assert the fake calendar store records reads but
never a write, across triggered AND not-triggered calendar paths.
"""

from __future__ import annotations

from datetime import datetime, timezone

from alert_engine.engine import STATUS_DELIVERED, STATUS_NOT_TRIGGERED

from .conftest import (
    FakeCalendarStore,
    FakeChannel,
    FakeDataSource,
    FakeFirestore,
    build_engine,
    make_calendar_date_rule,
)

# 2024-05-01 03:00 UTC == 12:00 KST; evaluation date is 2024-05-01.
NOW = datetime(2024, 5, 1, 3, 0, tzinfo=timezone.utc)
TODAY = "2024-05-01"


def _channels():
    return {"telegram": FakeChannel("telegram"), "push": FakeChannel("push")}


def test_calendar_triggered_path_performs_no_writes():
    store = FakeCalendarStore([{"id": "e1", "date": TODAY, "ticker": "AAPL", "type": "exDiv"}])
    ds = FakeDataSource(calendar_store=store)
    fs = FakeFirestore(calendar_store=store)
    engine = build_engine(ds, fs, _channels())
    rule = make_calendar_date_rule()

    r = engine.process_rule(rule, now=NOW)

    assert r.status == STATUS_DELIVERED  # matching event fired
    assert store.reads >= 1              # calendar was read
    assert store.writes == 0             # Property 6: zero calendar writes
    assert fs.calendar_writes == 0


def test_calendar_not_triggered_path_performs_no_writes():
    # No event today -> not triggered, but still must not write calendar data.
    store = FakeCalendarStore([{"id": "e2", "date": "2030-01-01", "ticker": "AAPL"}])
    ds = FakeDataSource(calendar_store=store)
    fs = FakeFirestore(calendar_store=store)
    engine = build_engine(ds, fs, _channels())
    rule = make_calendar_date_rule()

    r = engine.process_rule(rule, now=NOW)

    assert r.status == STATUS_NOT_TRIGGERED
    assert store.reads >= 1
    assert store.writes == 0
    assert fs.calendar_writes == 0


def test_no_notification_log_written_to_calendar_collections():
    """Notification logs are NOT calendar writes; calendar store stays untouched."""
    store = FakeCalendarStore([{"id": "e1", "date": TODAY, "ticker": "AAPL"}])
    ds = FakeDataSource(calendar_store=store)
    fs = FakeFirestore(calendar_store=store)
    engine = build_engine(ds, fs, _channels())
    rule = make_calendar_date_rule()

    engine.process_rule(rule, now=NOW)

    # The engine wrote a notificationLog (allowed) but never a calendar doc.
    assert len(fs.logs) == 1
    assert store.writes == 0
    assert fs.calendar_writes == 0
