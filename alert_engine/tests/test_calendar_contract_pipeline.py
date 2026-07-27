"""Regression coverage for the Gorani -> Goralert calendar contract."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alert_engine.calendar_contract import resolve_calendar_events
from alert_engine.datasource import AlertDataSource
from alert_engine.engine import STATUS_DELIVERED, STATUS_NOT_TRIGGERED
from alert_engine.models import AlertRule, AlertSettings, Condition

from .conftest import FakeChannel, FakeFirestore, build_engine


EVENT = {
    "id": "dividend:TEST:buy:2026-08-10",
    "canonicalEventId": "dividend:TEST:buy:2026-08-10",
    "legacyEventId": "TEST-buy_by-2026-08-10",
    "ticker": "TEST",
    "type": "buy_by",
    "date": "2026-08-10",
    "title": "TEST Deadline",
    "sourceKind": "declared",
}


def _resolved(metadata: list[dict], events: list[dict] | None = None) -> list[dict]:
    return resolve_calendar_events(events or [EVENT], [], metadata)


class ContractFirestore(FakeFirestore):
    def __init__(self, events: list[dict]):
        super().__init__(
            settings=AlertSettings(
                globalEnabled=True,
                telegramChatId="chat-123",
                pushTokens=["push-token"],
            )
        )
        self.events = events

    def read_calendar_events(self, uid: str, portfolio_id=None):
        return list(self.events)

    def read_calendar_custom_events(self, uid: str, portfolio_id=None):
        return list(self.events)


def _rule(
    event_type: str = "buy_by",
    marks: list[str] | None = None,
    title_contains: str | None = None,
    source: str = "calendarEvents",
) -> AlertRule:
    match: dict = {"type": [event_type]}
    if title_contains is not None:
        match["titleContains"] = title_contains
    return AlertRule.from_dict({
        "id": f"rule-{event_type}-{source}",
        "uid": "user-1",
        "kind": "date",
        "name": "캘린더 계약 테스트",
        "enabled": True,
        "condition": {
            "kind": "date",
            "selector": {
                "source": source,
                "match": match,
                "markFilter": marks or [],
            },
        },
        "trigger": {
            "mode": "recurring",
            "recurrence": {"kind": "calendar", "tz": "Asia/Seoul", "time": "09:00"},
        },
        "delivery": {
            "channels": ["push", "telegram"],
            "message": {"title": "일정", "body": "{ticker} 일정"},
        },
    })


def _dividend_rule(event_type: str = "ex_div") -> AlertRule:
    rule = _rule(event_type=event_type)
    rule.id = f"rule-dividend-{event_type}"
    rule.kind = "dividend"
    rule.condition.kind = "dividend"
    rule.condition.ticker = "TEST"
    rule.trigger.recurrence.time = "08:00"
    return rule


def _nested_composite_rule() -> AlertRule:
    rule = _rule()
    selector = rule.condition
    rule.id = "rule-nested-calendar-composite"
    rule.kind = "composite"
    rule.condition = Condition(
        kind="composite",
        operator="and",
        conditions=[
            Condition(
                kind="composite",
                operator="or",
                conditions=[selector],
            ),
        ],
    )
    rule.trigger.recurrence.time = "23:45"
    return rule


def test_metadata_without_date_joins_authoritative_body_by_canonical_id():
    metadata = [{
        "id": "different-firestore-doc-id",
        "eventId": "old-generated-id",
        "canonicalEventId": EVENT["canonicalEventId"],
        "heart": True,
    }]

    joined = _resolved(metadata)

    assert len(joined) == 1
    assert joined[0]["date"] == "2026-08-10"
    assert joined[0]["type"] == "buy_by"
    assert joined[0]["heart"] is True


def test_firestore_document_id_and_legacy_generated_id_are_compatible():
    metadata = [{
        "id": "metadata-payload-id",
        "firestoreDocumentId": EVENT["legacyEventId"],
        "star": True,
    }]

    joined = _resolved(metadata)

    assert len(joined) == 1
    assert joined[0]["star"] is True


def test_saved_cache_supersedes_legacy_rows_for_the_same_ticker():
    stale_legacy = {**EVENT, "id": "legacy-row", "date": "2026-07-01"}
    metadata = [{"canonicalEventId": EVENT["canonicalEventId"], "heart": True}]

    joined = resolve_calendar_events([EVENT], [stale_legacy], metadata, ["TEST"])

    assert [event["date"] for event in joined] == ["2026-08-10"]


def test_empty_authoritative_cache_suppresses_stale_legacy_event():
    stale_legacy = {**EVENT, "id": "legacy-row"}

    assert resolve_calendar_events([], [stale_legacy], [], ["TEST"]) == []


def test_missing_cache_document_preserves_legacy_fallback():
    stale_legacy = {**EVENT, "id": "legacy-row"}

    resolved = resolve_calendar_events([], [stale_legacy], [], [])

    assert [(event["ticker"], event["id"], event["date"]) for event in resolved] == [
        ("TEST", "legacy-row", "2026-08-10")
    ]


def test_empty_and_populated_cache_tickers_resolve_independently():
    stale_test = {**EVENT, "id": "legacy-test"}
    stale_abc = {**EVENT, "id": "legacy-abc", "ticker": "ABC"}
    cache_abc = {
        **EVENT,
        "id": "cache-abc",
        "canonicalEventId": "dividend:ABC:buy:2026-08-10",
        "ticker": "ABC",
    }

    resolved = resolve_calendar_events(
        [cache_abc],
        [stale_test, stale_abc],
        [],
        ["TEST", "ABC"],
    )

    assert [(event["ticker"], event["id"]) for event in resolved] == [("ABC", "cache-abc")]


@pytest.mark.parametrize(
    ("metadata", "marks", "expected"),
    [
        ({"heart": True}, ["heart"], 1),
        ({"heart": False}, ["heart"], 0),
        ({"star": True}, ["star"], 1),
        ({"star": True}, ["star", "heart"], 1),
        ({}, [], 1),
    ],
)
def test_mark_filter_contract(metadata, marks, expected):
    rows = _resolved([{**metadata, "canonicalEventId": EVENT["canonicalEventId"]}])
    firestore = ContractFirestore(rows)
    datasource = AlertDataSource(firestore=firestore)
    selector = _rule(marks=marks).condition.selector

    assert len(datasource.get_calendar_events("user-1", selector)) == expected


def test_title_contains_empty_and_unicode_safe_case_insensitive_partial_match():
    rows = _resolved([{"canonicalEventId": EVENT["canonicalEventId"], "heart": True}])
    firestore = ContractFirestore(rows)
    datasource = AlertDataSource(firestore=firestore)

    assert len(datasource.get_calendar_events("user-1", _rule(title_contains=" ").condition.selector)) == 1
    assert len(datasource.get_calendar_events("user-1", _rule(title_contains="deadline").condition.selector)) == 1
    assert len(datasource.get_calendar_events("user-1", _rule(title_contains="DEADLINE").condition.selector)) == 1


def test_metadata_without_authoritative_body_never_creates_an_event():
    metadata = [{"canonicalEventId": EVENT["canonicalEventId"], "heart": True}]
    assert resolve_calendar_events([], [], metadata) == []


def test_sample_or_mock_fallback_is_never_promoted_to_an_alert_event():
    metadata = [{"canonicalEventId": EVENT["canonicalEventId"], "heart": True}]
    sample = {**EVENT, "sourceKind": "sample"}
    mock = {**EVENT, "source": "mock"}

    assert resolve_calendar_events([sample, mock], [], metadata) == []


def test_custom_event_uses_body_and_ignores_inapplicable_legacy_mark_default():
    custom = [{
        "id": "custom:test-event",
        "canonicalEventId": "custom:test-event",
        "sourceKind": "custom",
        "ticker": "TEST",
        "type": "custom",
        "date": "2026-08-10",
        "title": "사용자 일정",
    }]
    firestore = ContractFirestore(custom)
    datasource = AlertDataSource(firestore=firestore)
    selector = _rule(event_type="custom", marks=["star", "heart"], source="calendarCustomEvents").condition.selector

    assert datasource.get_calendar_events("user-1", selector) == custom


def test_matching_calendar_event_runs_log_push_and_telegram_once():
    rows = _resolved([{"canonicalEventId": EVENT["canonicalEventId"], "heart": True}])
    firestore = ContractFirestore(rows)
    datasource = AlertDataSource(firestore=firestore)
    push = FakeChannel("push")
    telegram = FakeChannel("telegram")
    engine = build_engine(datasource, firestore, {"push": push, "telegram": telegram})
    now = datetime(2026, 8, 10, 0, 5, tzinfo=timezone.utc)
    rule = _rule(marks=["heart"])

    first = engine.process_rule(rule, now=now)
    duplicate = engine.process_rule(rule, now=now.replace(minute=10))

    assert first.status == STATUS_DELIVERED
    assert duplicate.status != STATUS_DELIVERED
    assert len(firestore.logs) == 1
    assert push.calls == 1
    assert telegram.calls == 1
    log = next(iter(firestore.logs.values()))
    assert {result.channel for result in log.channels} == {"push", "telegram"}


def test_custom_event_runs_the_same_log_and_dispatch_pipeline():
    custom = [{
        "id": "custom:test-event",
        "canonicalEventId": "custom:test-event",
        "sourceKind": "custom",
        "ticker": "TEST",
        "type": "custom",
        "date": "2026-08-10",
        "title": "사용자 일정",
    }]
    firestore = ContractFirestore(custom)
    datasource = AlertDataSource(firestore=firestore)
    push = FakeChannel("push")
    telegram = FakeChannel("telegram")
    engine = build_engine(datasource, firestore, {"push": push, "telegram": telegram})

    result = engine.process_rule(
        _rule(event_type="custom", marks=["star"], source="calendarCustomEvents"),
        now=datetime(2026, 8, 10, 0, 5, tzinfo=timezone.utc),
    )

    assert result.status == STATUS_DELIVERED
    assert len(firestore.logs) == 1
    assert push.calls == telegram.calls == 1


def test_non_matching_calendar_event_does_not_log_or_dispatch():
    rows = _resolved([{"canonicalEventId": EVENT["canonicalEventId"], "heart": False}])
    firestore = ContractFirestore(rows)
    datasource = AlertDataSource(firestore=firestore)
    push = FakeChannel("push")
    telegram = FakeChannel("telegram")
    engine = build_engine(datasource, firestore, {"push": push, "telegram": telegram})

    result = engine.process_rule(
        _rule(marks=["heart"]),
        now=datetime(2026, 8, 10, 0, 5, tzinfo=timezone.utc),
    )

    assert result.status == STATUS_NOT_TRIGGERED
    assert firestore.logs == {}
    assert push.calls == 0
    assert telegram.calls == 0


def test_empty_authoritative_cache_does_not_log_or_dispatch():
    stale_legacy = {**EVENT, "id": "legacy-row"}
    rows = resolve_calendar_events([], [stale_legacy], [], ["TEST"])
    firestore = ContractFirestore(rows)
    datasource = AlertDataSource(firestore=firestore)
    push = FakeChannel("push")
    telegram = FakeChannel("telegram")
    engine = build_engine(datasource, firestore, {"push": push, "telegram": telegram})

    result = engine.process_rule(
        _rule(),
        now=datetime(2026, 8, 10, 0, 5, tzinfo=timezone.utc),
    )

    assert result.status == STATUS_NOT_TRIGGERED
    assert firestore.logs == {}
    assert push.calls == 0
    assert telegram.calls == 0


def test_late_calendar_event_triggers_at_midnight_cron_without_changing_event_date():
    rows = _resolved([{"canonicalEventId": EVENT["canonicalEventId"]}])
    firestore = ContractFirestore(rows)
    datasource = AlertDataSource(firestore=firestore)
    push = FakeChannel("push")
    telegram = FakeChannel("telegram")
    engine = build_engine(datasource, firestore, {"push": push, "telegram": telegram})
    rule = _rule()
    rule.trigger.recurrence.time = "23:45"

    result = engine.process_rule(
        rule,
        now=datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc),  # 2026-08-11 00:00 KST
    )

    assert result.status == STATUS_DELIVERED
    assert result.event_id is not None and "2026-08-10T23:45:00+09:00" in result.event_id
    assert len(firestore.logs) == 1
    assert push.calls == telegram.calls == 1


def test_dividend_selector_uses_due_occurrence_date_in_recurrence_timezone():
    ex_div = {
        **EVENT,
        "id": "dividend:TEST:ex_div:2026-08-10",
        "canonicalEventId": "dividend:TEST:ex_div:2026-08-10",
        "legacyEventId": "TEST-ex_div-2026-08-10",
        "type": "ex_div",
        "title": "TEST Ex-Dividend",
    }
    rows = resolve_calendar_events([ex_div], [], [], ["TEST"])
    firestore = ContractFirestore(rows)
    datasource = AlertDataSource(firestore=firestore)
    push = FakeChannel("push")
    telegram = FakeChannel("telegram")
    engine = build_engine(datasource, firestore, {"push": push, "telegram": telegram})

    result = engine.process_rule(
        _dividend_rule(),
        now=datetime(2026, 8, 9, 23, 5, tzinfo=timezone.utc),  # 2026-08-10 08:05 KST
    )

    assert result.status == STATUS_DELIVERED
    assert len(firestore.logs) == 1
    assert push.calls == telegram.calls == 1


def test_nested_calendar_selector_uses_prior_due_date_across_midnight():
    rows = _resolved([{"canonicalEventId": EVENT["canonicalEventId"]}])
    firestore = ContractFirestore(rows)
    datasource = AlertDataSource(firestore=firestore)
    push = FakeChannel("push")
    telegram = FakeChannel("telegram")
    engine = build_engine(datasource, firestore, {"push": push, "telegram": telegram})

    result = engine.process_rule(
        _nested_composite_rule(),
        now=datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc),  # 2026-08-11 00:00 KST
    )

    assert result.status == STATUS_DELIVERED
    assert len(firestore.logs) == 1
    assert push.calls == telegram.calls == 1
