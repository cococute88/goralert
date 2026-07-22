"""Calendar-date derivation for the 매수 마감일-1 alert selector."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alert_engine.engine import STATUS_DELIVERED, STATUS_NOT_TRIGGERED
from alert_engine.models import AlertRule

from .conftest import FakeChannel, FakeDataSource, FakeFirestore, build_engine


def _calendar_rule(event_types: list[str], rule_id: str = "buy-by-rule") -> AlertRule:
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
                "match": {"type": event_types},
                "markFilter": ["star"],
            },
        },
        "trigger": {"mode": "recurring", "recurrence": {"kind": "calendar", "tz": "Asia/Seoul", "time": "09:00"}},
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
    return engine.process_rule(_calendar_rule(event_types, rule_id), now=now), firestore


@pytest.mark.parametrize(
    ("source_date", "notification_now"),
    [
        # 2026-07-27 is Monday; the alert must remain on Sunday, not Friday.
        ("2026-07-27", datetime(2026, 7, 25, 15, 30, tzinfo=timezone.utc)),
        # Calendar month boundary: 2026-08-01 -> 2026-07-31.
        ("2026-08-01", datetime(2026, 7, 30, 15, 30, tzinfo=timezone.utc)),
        # Calendar year boundary: 2027-01-01 -> 2026-12-31.
        ("2027-01-01", datetime(2026, 12, 30, 15, 30, tzinfo=timezone.utc)),
    ],
)
def test_buy_by_minus_one_uses_exact_calendar_day_in_seoul_timezone(source_date, notification_now):
    result, firestore = _process(source_date, ["buy_by_minus_1"], notification_now)

    assert result.status == STATUS_DELIVERED
    assert len(firestore.logs) == 1


def test_buy_by_and_buy_by_minus_one_are_independent_and_can_both_fire():
    source_date = "2026-07-27"  # Monday
    sunday_now = datetime(2026, 7, 25, 15, 30, tzinfo=timezone.utc)  # Sunday 00:30 KST
    monday_now = datetime(2026, 7, 26, 15, 30, tzinfo=timezone.utc)  # Monday 00:30 KST

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

    assert engine.process_rule(rule, now=sunday_now).status == STATUS_DELIVERED
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
