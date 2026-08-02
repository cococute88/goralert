"""Calendar-date derivation for the 매수 마감일-1 alert selector."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alert_engine.datasource import AlertDataSource
from alert_engine.engine import STATUS_DELIVERED, STATUS_NOT_TRIGGERED
from alert_engine.models import AlertRule

from .conftest import FakeChannel, FakeDataSource, FakeFirestore, build_engine


def _calendar_rule(
    event_types: list[str],
    rule_id: str = "buy-by-rule",
    *,
    title_contains: str | None = None,
    time: str = "09:00",
) -> AlertRule:
    match = {"type": event_types}
    if title_contains is not None:
        match["titleContains"] = title_contains
    return AlertRule.from_dict({
        "id": rule_id,
        "uid": "u1",
        "kind": "date",
        "name": "매수 마감일 알림",
        "enabled": True,
        "condition": {
            "kind": "date",
            "selector": {
                "source": "calendarEvents",
                "match": match,
                "markFilter": ["star"],
            },
        },
        "trigger": {"mode": "recurring", "recurrence": {"kind": "calendar", "tz": "Asia/Seoul", "time": time}},
        "delivery": {"channels": ["telegram"], "message": {"title": "매수 마감", "body": "{ticker}"}},
    })


def _process(event_date: str, event_types: list[str], now: datetime, rule_id: str = "buy-by-rule"):
    datasource = FakeDataSource(events=[{
        "id": "buy-by-event",
        "date": event_date,
        "ticker": "SCHD",
        "title": "SCHD 매수 마감일",
        "type": "buy_by",
        "star": True,
    }])
    firestore = FakeFirestore()
    engine = build_engine(datasource, firestore, {"telegram": FakeChannel("telegram")})
    rule = _calendar_rule(event_types, rule_id)
    rule.durableSchedulerVersion = 1
    rule.nextScheduledAt = now.replace(minute=0, second=0, microsecond=0)
    return engine.process_rule(rule, now=now), firestore


@pytest.mark.parametrize(
    ("source_date", "notification_now"),
    [
        # 2026-07-27 is Monday; the alert must remain on Sunday, not Friday.
        ("2026-07-27", datetime(2026, 7, 26, 0, 5, tzinfo=timezone.utc)),
        # Calendar month boundary: 2026-08-01 -> 2026-07-31.
        ("2026-08-01", datetime(2026, 7, 31, 0, 5, tzinfo=timezone.utc)),
        # Calendar year boundary: 2027-01-01 -> 2026-12-31.
        ("2027-01-01", datetime(2026, 12, 31, 0, 5, tzinfo=timezone.utc)),
    ],
)
def test_buy_by_minus_one_uses_exact_calendar_day_in_seoul_timezone(source_date, notification_now):
    result, firestore = _process(source_date, ["buy_by_minus_1"], notification_now)

    assert result.status == STATUS_DELIVERED
    assert len(firestore.logs) == 1


def test_buy_by_and_buy_by_minus_one_are_independent_and_can_both_fire():
    source_date = "2026-07-27"  # Monday
    sunday_now = datetime(2026, 7, 26, 0, 5, tzinfo=timezone.utc)  # Sunday 09:05 KST
    monday_now = datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc)  # Monday 09:05 KST

    # Existing buy-by selection remains on the original date.
    direct_sunday, _ = _process(source_date, ["buy_by"], sunday_now, "direct-sunday")
    direct_monday, _ = _process(source_date, ["buy_by"], monday_now, "direct-monday")
    assert direct_sunday.status == STATUS_NOT_TRIGGERED
    assert direct_monday.status == STATUS_DELIVERED

    # The new selection fires only on the preceding calendar date.
    minus_sunday, _ = _process(source_date, ["buy_by_minus_1"], sunday_now, "minus-sunday")
    minus_monday, _ = _process(source_date, ["buy_by_minus_1"], monday_now, "minus-monday")
    assert minus_sunday.status == STATUS_DELIVERED
    assert minus_monday.status == STATUS_NOT_TRIGGERED

    # Selecting both creates one derived Sunday candidate and one original
    # Monday candidate for the same read-only source event.
    datasource = FakeDataSource(events=[{
        "id": "buy-by-event",
        "date": source_date,
        "ticker": "SCHD",
        "title": "SCHD 매수 마감일",
        "type": "buy_by",
        "star": True,
    }])
    firestore = FakeFirestore()
    engine = build_engine(datasource, firestore, {"telegram": FakeChannel("telegram")})
    rule = _calendar_rule(["buy_by", "buy_by_minus_1"], "both")
    rule.durableSchedulerVersion = 1
    rule.nextScheduledAt = sunday_now.replace(minute=0, second=0, microsecond=0)

    assert engine.process_rule(rule, now=sunday_now).status == STATUS_DELIVERED
    rule.nextScheduledAt = monday_now.replace(minute=0, second=0, microsecond=0)
    assert engine.process_rule(rule, now=monday_now).status == STATUS_DELIVERED
    assert len(firestore.logs) == 2
    # The derived notification never creates or modifies a calendar event.
    assert datasource.calendar_store.events == [{
        "id": "buy-by-event",
        "date": source_date,
        "ticker": "SCHD",
        "title": "SCHD 매수 마감일",
        "type": "buy_by",
        "star": True,
    }]


def test_sgov_legacy_title_token_fires_only_on_calendar_day_minus_one_at_1800():
    source_event = {
        "id": "dividend:SGOV:buy:2026-08-10",
        "date": "2026-08-10",
        "ticker": "SGOV",
        "title": "SGOV 매수 마감",
        "type": "buy_by",
        "star": True,
        "heart": True,
    }
    firestore = FakeFirestore()
    firestore.read_calendar_events = lambda uid, portfolio_id=None: [source_event]
    datasource = AlertDataSource(firestore=firestore)
    telegram = FakeChannel("telegram")
    engine = build_engine(datasource, firestore, {"telegram": telegram})
    rule = _calendar_rule(
        ["buy_by_minus_1"],
        "sgov-sell",
        title_contains="buy-deadline",
        time="18:00",
    )
    sunday_due = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
    monday_due = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    rule.nextScheduledAt = sunday_due
    rule.durableSchedulerVersion = 1

    first = engine.process_rule(rule, now=sunday_due)
    rule.nextScheduledAt = monday_due
    duplicate_date = engine.process_rule(rule, now=monday_due)

    assert first.status == STATUS_DELIVERED
    assert duplicate_date.status == STATUS_NOT_TRIGGERED
    assert telegram.calls == 1
    assert len(firestore.logs) == 2
    assert any(
        getattr(log, "status", None) == "condition_false"
        for log in firestore.logs.values()
    )
