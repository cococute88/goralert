"""Legacy durable-scheduler bootstrap tests with a fixed migration clock."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from alert_engine.engine import (
    STATUS_DELIVERED,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_LEGACY_CURSOR_INITIALIZED,
    STATUS_NOT_DUE,
)
from alert_engine.models import AlertRule
from alert_engine.recurrence import get_tz

from .conftest import FakeChannel, FakeDataSource, FakeFirestore, build_engine

KST = get_tz("Asia/Seoul")
CUTOVER = datetime(2026, 8, 2, 9, 0, tzinfo=KST)


def _legacy_rule(rule_id: str, recurrence: dict) -> AlertRule:
    return AlertRule.from_dict({
        "id": rule_id,
        "uid": "u1",
        "kind": "date",
        "name": rule_id,
        "enabled": True,
        "condition": {"kind": "date"},
        "trigger": {"mode": "recurring", "recurrence": recurrence},
        "delivery": {
            "channels": ["telegram", "push"],
            "message": {"title": rule_id, "body": rule_id},
        },
        "createdAt": datetime(2025, 1, 1, tzinfo=KST),
        "lastTriggeredAt": "2026-07-01T00:00:00+09:00",
    })


def _run(rule: AlertRule, store: FakeFirestore | None = None, now: datetime = CUTOVER):
    firestore = store or FakeFirestore()
    telegram = FakeChannel("telegram")
    push = FakeChannel("push")
    engine = build_engine(
        FakeDataSource(), firestore, {"telegram": telegram, "push": push},
    )
    result = engine.process_rule(rule, now=now)
    return result, firestore, telegram, push


@pytest.mark.parametrize(
    ("rule_id", "recurrence", "expected_utc"),
    [
        (
            "한투->토스하나이체",
            {"kind": "monthlyLastDay", "time": "12:15", "tz": "Asia/Seoul"},
            datetime(2026, 8, 31, 3, 15, tzinfo=timezone.utc),
        ),
        (
            "미래에셋 예약매도",
            {"kind": "monthlyFirstDay", "time": "07:00", "tz": "Asia/Seoul"},
            datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc),
        ),
        (
            "daily-calendar",
            {"kind": "calendar", "time": "07:00", "tz": "Asia/Seoul"},
            datetime(2026, 8, 2, 22, 0, tzinfo=timezone.utc),
        ),
        (
            "weekly",
            {"kind": "weekly", "weekday": 0, "time": "08:00", "tz": "Asia/Seoul"},
            datetime(2026, 8, 8, 23, 0, tzinfo=timezone.utc),
        ),
        (
            "VR 주문",
            {
                "kind": "biweekly", "weekday": 6, "time": "09:00",
                "tz": "Asia/Seoul", "anchorDate": "2026-07-04",
            },
            datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc),
        ),
    ],
)
def test_legacy_bootstrap_skips_backlog_and_sets_exact_future_cursor(
    rule_id, recurrence, expected_utc,
):
    store = FakeFirestore()
    store.logs["existing-history"] = {"status": "sent", "eventId": "old-event"}
    original_history = dict(store.logs)

    result, store, telegram, push = _run(_legacy_rule(rule_id, recurrence), store)

    assert result.status == STATUS_LEGACY_CURSOR_INITIALIZED
    assert store.legacy_cursor == expected_utc
    assert store.legacy_initializations == 1
    assert store.logs == original_history
    assert telegram.calls == push.calls == 0
    migration = store.state_updates[0]["scheduler_migration"]
    assert migration["kind"] == "legacy_cursor_bootstrap"
    assert migration["backlogPolicy"] == "skip_automatic_backlog"
    assert migration["backlogSkippedThrough"] == CUTOVER.astimezone(timezone.utc)
    assert migration["initializedNextScheduledAt"] == expected_utc


def test_legacy_bootstrap_is_strictly_future_at_exact_wall_clock_boundary():
    rule = _legacy_rule(
        "calendar-boundary",
        {"kind": "calendar", "time": "09:00", "tz": "Asia/Seoul"},
    )

    result, store, telegram, push = _run(rule)

    assert result.status == STATUS_LEGACY_CURSOR_INITIALIZED
    assert store.legacy_cursor == datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)
    assert telegram.calls == push.calls == 0


def test_monthly_specific_day_calendar_rule_bootstraps_without_evaluating_event():
    rule = _legacy_rule(
        "monthly-specific-calendar-date",
        {"kind": "calendar", "time": "08:30", "tz": "Asia/Seoul"},
    )
    rule.condition.selector = {
        "source": "calendarEvents",
        "match": {"date": "2026-08-15", "type": "custom"},
    }
    datasource = FakeDataSource(events=[{
        "id": "monthly-specific-event",
        "date": "2026-08-15",
        "type": "custom",
        "title": "월 특정일",
    }])
    store = FakeFirestore()
    telegram = FakeChannel("telegram")
    push = FakeChannel("push")
    engine = build_engine(datasource, store, {"telegram": telegram, "push": push})

    result = engine.process_rule(rule, now=CUTOVER)

    assert result.status == STATUS_LEGACY_CURSOR_INITIALIZED
    assert store.legacy_cursor == datetime(2026, 8, 2, 23, 30, tzinfo=timezone.utc)
    assert datasource.calendar_store.reads == 0
    assert store.logs == {}
    assert telegram.calls == push.calls == 0


@pytest.mark.parametrize(
    ("cutover", "expected"),
    [
        (
            datetime(2024, 2, 28, 23, 0, tzinfo=KST),
            datetime(2024, 2, 29, 3, 15, tzinfo=timezone.utc),
        ),
        (
            datetime(2026, 12, 31, 13, 0, tzinfo=KST),
            datetime(2027, 1, 31, 3, 15, tzinfo=timezone.utc),
        ),
    ],
)
def test_month_end_legacy_bootstrap_handles_leap_year_and_year_rollover(cutover, expected):
    rule = _legacy_rule(
        "month-end-edge",
        {"kind": "monthlyLastDay", "time": "12:15", "tz": "Asia/Seoul"},
    )
    result, store, telegram, push = _run(rule, now=cutover)

    assert result.status == STATUS_LEGACY_CURSOR_INITIALIZED
    assert store.legacy_cursor == expected
    assert telegram.calls == push.calls == 0


def test_two_workers_converge_on_one_legacy_initialization_without_provider_calls():
    store = FakeFirestore()
    telegram = FakeChannel("telegram")
    push = FakeChannel("push")
    engine = build_engine(FakeDataSource(), store, {"telegram": telegram, "push": push})
    rule = _legacy_rule(
        "concurrent-legacy",
        {"kind": "monthlyLastDay", "time": "12:15", "tz": "Asia/Seoul"},
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: engine.process_rule(rule, now=CUTOVER), range(2)))

    assert [result.status for result in results] == [
        STATUS_LEGACY_CURSOR_INITIALIZED,
        STATUS_LEGACY_CURSOR_INITIALIZED,
    ]
    assert store.legacy_initializations == 1
    assert store.legacy_cursor == datetime(2026, 8, 31, 3, 15, tzinfo=timezone.utc)
    assert telegram.calls == push.calls == 0
    assert store.logs == {}


class ScheduleChangedDuringInitialization(FakeFirestore):
    def initialize_legacy_scheduler_cursor(self, *args, **kwargs):
        return {"initialization": "schedule_changed"}


class DeletedDuringInitialization(FakeFirestore):
    def initialize_legacy_scheduler_cursor(self, *args, **kwargs):
        return {"initialization": "inactive", "reason": "rule_deleted"}


@pytest.mark.parametrize(
    ("store", "expected_status"),
    [
        (ScheduleChangedDuringInitialization(), STATUS_NOT_DUE),
        (DeletedDuringInitialization(), STATUS_DISABLED),
    ],
)
def test_legacy_initialization_races_never_claim_or_send(store, expected_status):
    result, store, telegram, push = _run(_legacy_rule(
        "race",
        {"kind": "weekly", "weekday": 1, "time": "10:00", "tz": "Asia/Seoul"},
    ), store)

    assert result.status == expected_status
    assert store.logs == {}
    assert telegram.calls == push.calls == 0


def test_disabled_legacy_rule_never_initializes_or_sends():
    rule = _legacy_rule(
        "disabled",
        {"kind": "calendar", "time": "09:00", "tz": "Asia/Seoul"},
    )
    rule.enabled = False

    result, store, telegram, push = _run(rule)

    assert result.status == STATUS_DISABLED
    assert store.legacy_initializations == 0
    assert store.logs == {}
    assert telegram.calls == push.calls == 0


def test_invalid_timezone_legacy_rule_records_scheduler_error_without_history_or_send():
    rule = _legacy_rule(
        "invalid-timezone",
        {"kind": "monthlyFirstDay", "time": "07:00", "tz": "Not/A-Timezone"},
    )

    result, store, telegram, push = _run(rule)

    assert result.status == STATUS_ERROR
    assert store.scheduler_errors[0]["code"] == "invalid_schedule_or_timezone"
    assert store.logs == {}
    assert telegram.calls == push.calls == 0


def test_versioned_rule_without_cursor_is_corrupt_not_legacy():
    rule = _legacy_rule(
        "corrupt-versioned",
        {"kind": "monthlyFirstDay", "time": "07:00", "tz": "Asia/Seoul"},
    )
    rule.durableSchedulerVersion = 1

    result, store, telegram, push = _run(rule)

    assert result.status == STATUS_ERROR
    assert store.legacy_initializations == 0
    assert store.scheduler_errors[0]["code"] == "missing_cursor_for_versioned_rule"
    assert store.logs == {}
    assert telegram.calls == push.calls == 0


def test_explicit_recovery_after_migration_processes_only_requested_occurrence():
    requested = datetime(2026, 7, 31, 12, 15, tzinfo=KST)
    rule = _legacy_rule(
        "recovered",
        {"kind": "monthlyLastDay", "time": "12:15", "tz": "Asia/Seoul"},
    )
    rule.durableSchedulerVersion = 1
    rule.schedulerMigration = {
        "kind": "legacy_cursor_bootstrap",
        "backlogPolicy": "skip_automatic_backlog",
    }
    rule.schedulerRecovery = {
        "status": "requested",
        "scheduledFor": requested.astimezone(timezone.utc),
        "duplicateRiskAcknowledged": True,
    }
    rule.scheduleStatus = "recovery_requested"
    rule.nextScheduledAt = requested.astimezone(timezone.utc)

    result, store, telegram, push = _run(rule)

    assert result.status == STATUS_DELIVERED
    assert telegram.calls == push.calls == 1
    assert len(store.logs) == 1
    assert next(iter(store.logs.values())).scheduledFor == "2026-07-31T03:15:00+00:00"


def test_worker_after_initialization_processes_future_occurrence_normally():
    legacy = _legacy_rule(
        "second-run",
        {"kind": "monthlyLastDay", "time": "12:15", "tz": "Asia/Seoul"},
    )
    first, store, telegram, push = _run(legacy)
    assert first.status == STATUS_LEGACY_CURSOR_INITIALIZED
    assert telegram.calls == push.calls == 0

    reloaded = _legacy_rule("second-run", legacy.trigger.recurrence.to_dict())
    reloaded.durableSchedulerVersion = 1
    reloaded.schedulerMigration = store.state_updates[0]["scheduler_migration"]
    reloaded.nextScheduledAt = store.legacy_cursor
    second, _, telegram, push = _run(
        reloaded,
        store,
        datetime(2026, 8, 31, 12, 16, tzinfo=KST),
    )

    assert second.status == STATUS_DELIVERED
    assert telegram.calls == push.calls == 1
