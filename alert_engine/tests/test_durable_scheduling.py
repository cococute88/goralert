"""Deterministic state-transition tests for durable scheduled occurrences."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from alert_engine.engine import (
    STATUS_DELIVERED,
    STATUS_DISABLED,
    STATUS_DUPLICATE,
    STATUS_ERROR,
    STATUS_NOT_DUE,
)
from alert_engine.models import AlertRule
from alert_engine.recurrence import get_tz, next_scheduled_occurrence

from .conftest import FakeChannel, FakeDataSource, FakeFirestore, build_engine

KST = get_tz("Asia/Seoul")


def _rule(kind: str, scheduled_for: datetime, rule_id: str = "scheduled-rule") -> AlertRule:
    recurrence = {"kind": kind, "time": scheduled_for.strftime("%H:%M"), "tz": "Asia/Seoul"}
    return AlertRule.from_dict({
        "id": rule_id,
        "uid": "u1",
        "kind": "date",
        "name": "정기 이체",
        "enabled": True,
        "condition": {"kind": "date"},
        "trigger": {"mode": "recurring", "recurrence": recurrence},
        "delivery": {
            "channels": ["telegram", "push"],
            "message": {"title": "이체", "body": "이체하세요"},
        },
        "createdAt": datetime(2026, 7, 2, tzinfo=KST),
        "nextScheduledAt": scheduled_for.astimezone(timezone.utc),
    })


def _engine(fs=None, telegram=None, push=None):
    store = fs or FakeFirestore()
    tg = telegram or FakeChannel("telegram")
    ps = push or FakeChannel("push")
    return build_engine(FakeDataSource(), store, {"telegram": tg, "push": ps}), store, tg, ps


def test_month_end_occurrence_does_not_advance_or_send_before_due():
    scheduled = datetime(2026, 7, 31, 12, 15, tzinfo=KST)
    engine, fs, telegram, push = _engine()

    result = engine.process_rule(_rule("monthlyLastDay", scheduled), now=scheduled - timedelta(minutes=1))

    assert result.status == STATUS_NOT_DUE
    assert fs.logs == {}
    assert fs.state_updates == []
    assert telegram.calls == push.calls == 0


def test_case_a_month_end_delayed_run_is_caught_up_once_then_advances():
    scheduled = datetime(2026, 7, 31, 12, 15, tzinfo=KST)
    engine, fs, telegram, push = _engine()
    rule = _rule("monthlyLastDay", scheduled, "month-end")

    first = engine.process_rule(rule, now=datetime(2026, 7, 31, 12, 20, tzinfo=KST))
    duplicate = engine.process_rule(rule, now=datetime(2026, 7, 31, 12, 21, tzinfo=KST))

    assert first.status == STATUS_DELIVERED
    assert duplicate.status == STATUS_DUPLICATE
    assert telegram.calls == push.calls == 1
    log = next(iter(fs.logs.values()))
    assert log.scheduledFor == "2026-07-31T03:15:00+00:00"
    assert log.processingStartedAt == "2026-07-31T03:20:00+00:00"
    assert log.status == "sent"
    assert {item.channel: item.status for item in log.channels} == {"telegram": "sent", "push": "sent"}
    claim_update = fs.state_updates[0]
    assert claim_update["next_scheduled_at"] == datetime(2026, 8, 31, 3, 15, tzinfo=timezone.utc)


def test_case_b_month_first_0719_occurrence_recovers_at_0919():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    engine, fs, telegram, push = _engine()

    result = engine.process_rule(
        _rule("monthlyFirstDay", scheduled, "month-first"),
        now=datetime(2026, 8, 1, 9, 19, tzinfo=KST),
    )

    assert result.status == STATUS_DELIVERED
    assert telegram.calls == push.calls == 1
    log = next(iter(fs.logs.values()))
    assert log.scheduledFor == "2026-07-31T22:00:00+00:00"
    assert log.processingStartedAt == "2026-08-01T00:19:00+00:00"
    assert fs.state_updates[0]["next_scheduled_at"] == datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("year", "month", "day"),
    [(2025, 1, 31), (2025, 2, 28), (2024, 2, 29), (2025, 4, 30),
     (2026, 7, 31), (2026, 8, 31), (2026, 9, 30), (2026, 12, 31)],
)
def test_month_end_calendar_dates(year, month, day):
    current = datetime(year, month, day, 12, 15, tzinfo=KST)
    nxt = next_scheduled_occurrence(
        _rule("monthlyLastDay", current).trigger.recurrence,
        current,
    )
    assert current.day == day
    assert nxt is not None and nxt > current
    assert (nxt.hour, nxt.minute, str(nxt.tzinfo)) == (12, 15, "Asia/Seoul")


def test_two_workers_claim_same_occurrence_only_one_sends():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    engine, fs, telegram, push = _engine()
    rule = _rule("monthlyFirstDay", scheduled, "concurrent")
    now = datetime(2026, 8, 1, 9, 19, tzinfo=KST)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: engine.process_rule(rule, now=now), range(2)))

    assert sorted(result.status for result in results) == sorted([STATUS_DELIVERED, STATUS_DUPLICATE])
    assert telegram.calls == push.calls == 1
    assert len(fs.logs) == 1


class FailChannelResultOnce(FakeFirestore):
    def __init__(self):
        super().__init__()
        self.failed = False

    def record_channel_result(self, uid, event_id, result, worker_id):
        if not self.failed:
            self.failed = True
            raise RuntimeError("simulated DB outage after provider call")
        return super().record_channel_result(uid, event_id, result, worker_id)


class FailBeginOnce(FakeFirestore):
    def __init__(self):
        super().__init__()
        self.failed = False

    def begin_channel_attempt(self, uid, event_id, channel, worker_id, attempted_at):
        if not self.failed:
            self.failed = True
            raise RuntimeError("simulated crash before provider call")
        return super().begin_channel_attempt(uid, event_id, channel, worker_id, attempted_at)


class FailFinalizeOnce(FakeFirestore):
    def __init__(self):
        super().__init__()
        self.failed = False

    def finalize_occurrence(self, uid, rule_id, event_id, status, updates, rule_updates=None, worker_id=None):
        if not self.failed:
            self.failed = True
            raise RuntimeError("simulated finalization outage")
        return super().finalize_occurrence(uid, rule_id, event_id, status, updates, rule_updates, worker_id)


class FailClaim(FakeFirestore):
    def claim_occurrence(self, *args, **kwargs):
        raise RuntimeError("simulated transaction failure")


class InactiveClaim(FakeFirestore):
    def claim_occurrence(self, *args, **kwargs):
        return {"claim": "inactive", "reason": "rule_disabled", "record": None}


def test_crash_after_provider_call_never_resends_ambiguous_channel():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    fs = FailChannelResultOnce()
    telegram = FakeChannel("telegram")
    push = FakeChannel("push")
    engine, _, _, _ = _engine(fs, telegram, push)
    rule = _rule("monthlyFirstDay", scheduled, "ambiguous")
    now = datetime(2026, 8, 1, 9, 19, tzinfo=KST)

    first = engine.process_rule(rule, now=now)
    assert first.status == STATUS_ERROR
    assert telegram.calls == 1
    assert push.calls == 0

    # Simulate lease expiry/restart. The sending Telegram channel becomes
    # unknown and is never called again; the still-pending push can proceed.
    record = next(iter(fs.logs.values()))
    record["leaseExpiresAt"] = now.astimezone(timezone.utc) - timedelta(seconds=1)
    rule.scheduleStatus = "processing"
    rule.lastOccurrenceId = first.event_id
    rule.nextScheduledAt = datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)
    second = engine.process_rule(rule, now=now + timedelta(minutes=30))

    assert second.status == STATUS_DELIVERED
    assert telegram.calls == 1
    assert push.calls == 1
    final = next(iter(fs.logs.values()))
    assert final.status == "delivery_unknown"
    assert {item.channel: item.status for item in final.channels} == {"telegram": "unknown", "push": "sent"}


def test_crash_before_provider_call_is_resumed_after_lease_without_loss():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    fs = FailBeginOnce()
    engine, _, telegram, push = _engine(fs)
    rule = _rule("monthlyFirstDay", scheduled, "resume-pending")
    now = datetime(2026, 8, 1, 9, 19, tzinfo=KST)

    first = engine.process_rule(rule, now=now)
    assert first.status == STATUS_ERROR
    assert telegram.calls == push.calls == 0
    record = next(iter(fs.logs.values()))
    record["leaseExpiresAt"] = now.astimezone(timezone.utc) - timedelta(seconds=1)
    rule.scheduleStatus = "processing"
    rule.lastOccurrenceId = first.event_id
    rule.nextScheduledAt = datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)

    second = engine.process_rule(rule, now=now + timedelta(minutes=30))

    assert second.status == STATUS_DELIVERED
    assert telegram.calls == push.calls == 1
    assert next(iter(fs.logs.values())).status == "sent"


def test_finalization_failure_recovers_without_resending_completed_channels():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    fs = FailFinalizeOnce()
    engine, _, telegram, push = _engine(fs)
    rule = _rule("monthlyFirstDay", scheduled, "resume-finalize")
    now = datetime(2026, 8, 1, 9, 19, tzinfo=KST)

    first = engine.process_rule(rule, now=now)
    assert first.status == STATUS_ERROR
    assert telegram.calls == push.calls == 1
    record = next(iter(fs.logs.values()))
    record["leaseExpiresAt"] = now.astimezone(timezone.utc) - timedelta(seconds=1)
    rule.scheduleStatus = "processing"
    rule.lastOccurrenceId = first.event_id
    rule.nextScheduledAt = datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)

    second = engine.process_rule(rule, now=now + timedelta(minutes=30))

    assert second.status == STATUS_DELIVERED
    assert telegram.calls == push.calls == 1
    assert next(iter(fs.logs.values())).status == "sent"


def test_claim_transaction_failure_cannot_send_or_advance_cursor():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    fs = FailClaim()
    engine, _, telegram, push = _engine(fs)

    result = engine.process_rule(
        _rule("monthlyFirstDay", scheduled, "claim-failure"),
        now=scheduled,
    )

    assert result.status == STATUS_ERROR
    assert telegram.calls == push.calls == 0
    assert fs.logs == {}
    assert fs.state_updates == []


def test_rule_disabled_between_query_and_claim_never_sends():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    fs = InactiveClaim()
    engine, _, telegram, push = _engine(fs)

    result = engine.process_rule(_rule("monthlyFirstDay", scheduled), now=scheduled)

    assert result.status == STATUS_DISABLED
    assert result.detail == "rule_disabled"
    assert telegram.calls == push.calls == 0


def test_schedule_change_anchor_does_not_replay_new_cadence_from_creation():
    rule = _rule("monthlyFirstDay", datetime(2026, 7, 1, 7, 0, tzinfo=KST), "edited")
    rule.nextScheduledAt = None
    rule.createdAt = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    rule.lastProcessedScheduledAt = datetime(2026, 7, 1, 7, 0, tzinfo=KST)
    rule.scheduleChangedAt = datetime(2026, 8, 1, 9, 19, tzinfo=KST)
    engine, fs, telegram, push = _engine()

    result = engine.process_rule(rule, now=datetime(2026, 8, 1, 9, 20, tzinfo=KST))

    assert result.status == STATUS_NOT_DUE
    assert fs.logs == {}
    assert telegram.calls == push.calls == 0


def test_stale_worker_cannot_finalize_after_lease_owner_changes():
    fs = FakeFirestore()
    fs.logs["occurrence"] = {
        "eventId": "occurrence",
        "status": "processing",
        "leaseOwner": "worker-new",
        "channels": [],
        "message": {"title": "t", "body": "b"},
    }

    with pytest.raises(RuntimeError, match="lease lost"):
        fs.finalize_occurrence(
            "u1", "rule", "occurrence", "sent", {}, worker_id="worker-old",
        )

    assert fs.logs["occurrence"]["status"] == "processing"


def test_legacy_reservation_without_channel_state_is_never_resent():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    engine, fs, telegram, push = _engine()
    rule = _rule("monthlyFirstDay", scheduled, "legacy-reservation")
    event_id = f"{rule.id}:{scheduled.isoformat()}"
    fs.logs[event_id] = {
        "eventId": event_id,
        "pending": True,
        "reservedAt": datetime(2026, 8, 1, 7, 0, tzinfo=KST),
    }

    result = engine.process_rule(rule, now=datetime(2026, 8, 1, 9, 19, tzinfo=KST))

    assert result.status == STATUS_DELIVERED
    assert telegram.calls == push.calls == 0
    log = next(iter(fs.logs.values()))
    assert log.status == "delivery_unknown"
    assert all(channel.status == "unknown" for channel in log.channels)


def test_one_shot_calendar_rule_advances_daily_until_target_then_disables():
    fs = FakeFirestore()
    telegram = FakeChannel("telegram")
    engine = build_engine(
        FakeDataSource(events=[{
            "id": "event-1",
            "canonicalEventId": "event-1",
            "date": "2026-08-03",
            "ticker": "TEST",
            "type": "custom",
            "title": "목표 일정",
        }]),
        fs,
        {"telegram": telegram},
    )
    rule = AlertRule.from_dict({
        "id": "once-calendar",
        "uid": "u1",
        "kind": "date",
        "name": "목표 일정",
        "enabled": True,
        "condition": {
            "kind": "date",
            "selector": {
                "source": "calendarEvents",
                "match": {"eventId": "event-1", "date": "2026-08-03"},
            },
        },
        "trigger": {
            "mode": "once",
            "recurrence": {"kind": "calendar", "time": "09:00", "tz": "Asia/Seoul"},
        },
        "delivery": {"channels": ["telegram"], "message": {"title": "일정", "body": "확인"}},
        "createdAt": datetime(2026, 8, 1, 8, 0, tzinfo=KST),
        "nextScheduledAt": datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
    })

    day1 = engine.process_rule(rule, now=datetime(2026, 8, 1, 9, 5, tzinfo=KST))
    assert day1.status != STATUS_DELIVERED
    assert telegram.calls == 0
    rule.nextScheduledAt = datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc)
    rule.lastProcessedScheduledAt = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

    day2 = engine.process_rule(rule, now=datetime(2026, 8, 2, 9, 5, tzinfo=KST))
    assert day2.status != STATUS_DELIVERED
    assert telegram.calls == 0
    rule.nextScheduledAt = datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)
    rule.lastProcessedScheduledAt = datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc)

    target = engine.process_rule(rule, now=datetime(2026, 8, 3, 9, 5, tzinfo=KST))
    assert target.status == STATUS_DELIVERED
    assert telegram.calls == 1
    assert any(update.get("enabled") is False for update in fs.state_updates)


@pytest.mark.parametrize(
    ("telegram_status", "push_status", "expected"),
    [
        ("sent", "sent", "sent"),
        ("sent", "failed", "partial_failure"),
        ("failed", "sent", "partial_failure"),
        ("failed", "failed", "failed"),
    ],
)
def test_channel_results_are_preserved(telegram_status, push_status, expected):
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    engine, fs, _, _ = _engine(
        telegram=FakeChannel("telegram", telegram_status),
        push=FakeChannel("push", push_status),
    )

    result = engine.process_rule(
        _rule("monthlyFirstDay", scheduled, f"combo-{telegram_status}-{push_status}"),
        now=scheduled,
    )

    assert result.status == STATUS_DELIVERED
    log = next(iter(fs.logs.values()))
    assert log.status == expected
    assert {item.channel: item.status for item in log.channels} == {
        "telegram": telegram_status,
        "push": push_status,
    }


def test_invalid_timezone_is_recorded_and_never_sent():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    engine, fs, telegram, push = _engine()
    rule = _rule("monthlyFirstDay", scheduled, "bad-tz")
    rule.trigger.recurrence.tz = "Not/A-Timezone"

    result = engine.process_rule(rule, now=scheduled)

    assert result.status == STATUS_ERROR
    assert telegram.calls == push.calls == 0
    log = next(iter(fs.logs.values()))
    assert log.failureCode == "invalid_schedule_or_timezone"
