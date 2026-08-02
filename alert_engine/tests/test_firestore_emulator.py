"""Real Firestore transaction tests, enabled only under Firebase Emulator Suite.

Run without production credentials:

    firebase emulators:exec --only firestore --project demo-goralert \
      "python -m pytest alert_engine/tests/test_firestore_emulator.py -q"
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

if not os.getenv("FIRESTORE_EMULATOR_HOST"):
    pytest.skip("requires FIRESTORE_EMULATOR_HOST", allow_module_level=True)

os.environ.setdefault("FIREBASE_PROJECT_ID", "demo-goralert")

from alert_engine import firestore_client
from alert_engine import audit
from alert_engine.models import AlertRule
from alert_engine.recurrence import get_tz

from .conftest import FakeChannel, FakeDataSource, build_engine

KST = get_tz("Asia/Seoul")


def _refs(rule_id: str):
    uid = f"emulator-{uuid4().hex}"
    db = firestore_client.get_db()
    rule_ref = db.collection("users").document(uid).collection("alertRules").document(rule_id)
    return uid, rule_ref


def _rule_data(uid: str, recurrence: dict, created_at: datetime, next_scheduled_at=None) -> dict:
    data = {
        "id": "rule",
        "uid": uid,
        "kind": "date",
        "name": "Emulator 반복 알림",
        "enabled": True,
        "condition": {"kind": "date"},
        "trigger": {"mode": "recurring", "recurrence": recurrence},
        "delivery": {
            "channels": ["telegram", "push"],
            "message": {"title": "통합 테스트", "body": "실제 전송 없음"},
        },
        "createdAt": created_at,
    }
    if next_scheduled_at is not None:
        data["nextScheduledAt"] = next_scheduled_at
    return data


def _payload(event_id: str, scheduled: datetime) -> dict:
    scheduled_utc = scheduled.astimezone(timezone.utc).isoformat()
    return {
        "id": event_id,
        "eventId": event_id,
        "ruleId": "rule",
        "kind": "date",
        "firedAt": scheduled_utc,
        "evaluatedAt": scheduled_utc,
        "message": {"title": "통합 테스트", "body": "실제 전송 없음"},
        "channels": [
            {"channel": "telegram", "status": "pending", "attemptCount": 0},
            {"channel": "push", "status": "pending", "attemptCount": 0},
        ],
        "isTest": False,
        "status": "processing",
        "scheduledFor": scheduled_utc,
        "timezone": "Asia/Seoul",
    }


def test_real_transaction_atomically_creates_occurrence_and_advances_cursor_once():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    next_scheduled = datetime(2026, 9, 1, 7, 0, tzinfo=KST)
    uid, rule_ref = _refs("rule")
    rule_ref.set(_rule_data(uid, {"kind": "monthlyFirstDay", "time": "07:00", "tz": "Asia/Seoul"}, scheduled, scheduled))
    event_id = f"rule:{scheduled.isoformat()}"
    now = datetime(2026, 8, 1, 9, 19, tzinfo=KST)

    def claim(worker: str):
        return firestore_client.claim_occurrence(
            uid, "rule", event_id, _payload(event_id, scheduled),
            next_scheduled, worker, now,
        )["claim"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, ["worker-a", "worker-b"]))

    assert sorted(outcomes) == ["claimed", "in_progress"]
    log = rule_ref.parent.parent.collection("notificationLogs").document(event_id).get().to_dict()
    stored_rule = rule_ref.get().to_dict()
    assert log and log["attemptCount"] == 1
    assert stored_rule and stored_rule["lastOccurrenceId"] == event_id
    assert stored_rule["nextScheduledAt"] == next_scheduled.astimezone(timezone.utc)


def test_real_lease_recovery_rejects_stale_finalizer_and_persists_channel_results():
    scheduled = datetime(2026, 7, 31, 12, 15, tzinfo=KST)
    uid, rule_ref = _refs("rule")
    rule_ref.set(_rule_data(uid, {"kind": "monthlyLastDay", "time": "12:15", "tz": "Asia/Seoul"}, scheduled, scheduled))
    event_id = f"rule:{scheduled.isoformat()}"
    first_now = datetime(2026, 7, 31, 12, 20, tzinfo=KST)
    firestore_client.claim_occurrence(
        uid, "rule", event_id, _payload(event_id, scheduled),
        datetime(2026, 8, 31, 12, 15, tzinfo=KST), "worker-old", first_now, lease_seconds=1,
    )

    reclaimed = firestore_client.claim_occurrence(
        uid, "rule", event_id, _payload(event_id, scheduled),
        datetime(2026, 8, 31, 12, 15, tzinfo=KST), "worker-new", first_now + timedelta(seconds=2),
    )
    assert reclaimed["claim"] == "claimed"
    assert reclaimed["record"]["attemptCount"] == 2
    with pytest.raises(RuntimeError, match="lease lost"):
        firestore_client.finalize_occurrence(uid, "rule", event_id, "sent", {}, worker_id="worker-old")

    assert firestore_client.begin_channel_attempt(uid, "rule", event_id, "telegram", "worker-new", first_now) == "began"
    firestore_client.record_channel_result(uid, event_id, {
        "channel": "telegram", "status": "sent", "attemptCount": 1,
        "completedAt": first_now.astimezone(timezone.utc).isoformat(),
    }, "worker-new")
    firestore_client.finalize_occurrence(
        uid, "rule", event_id, "partial_failure",
        {"completedAt": first_now.astimezone(timezone.utc).isoformat()},
        worker_id="worker-new",
    )
    stored = rule_ref.parent.parent.collection("notificationLogs").document(event_id).get().to_dict()
    assert stored and stored["status"] == "partial_failure" and stored["pending"] is False
    assert {row["channel"]: row["status"] for row in stored["channels"]}["telegram"] == "sent"


def test_real_legacy_cursor_initialization_skips_backlog_and_writes_no_history():
    uid, rule_ref = _refs("rule")
    data = _rule_data(
        uid,
        {"kind": "monthlyLastDay", "time": "12:15", "tz": "Asia/Seoul"},
        datetime(2026, 7, 2, 9, 0, tzinfo=KST),
    )
    rule_ref.set(data)
    rule = AlertRule.from_dict(data)
    telegram = FakeChannel("telegram")
    push = FakeChannel("push")
    engine = build_engine(FakeDataSource(), firestore_client, {"telegram": telegram, "push": push})

    result = engine.process_rule(rule, now=datetime(2026, 8, 2, 9, 0, tzinfo=KST))

    assert result.status == "legacy_cursor_initialized"
    assert telegram.calls == push.calls == 0
    stored_rule = rule_ref.get().to_dict()
    assert stored_rule and stored_rule["nextScheduledAt"] == datetime(2026, 8, 31, 3, 15, tzinfo=timezone.utc)
    assert stored_rule["durableSchedulerVersion"] == 1
    assert stored_rule["schedulerMigration"]["backlogPolicy"] == "skip_automatic_backlog"
    assert stored_rule["schedulerMigration"]["backlogSkippedThrough"] == datetime(
        2026, 8, 2, 0, 0, tzinfo=timezone.utc,
    )
    history = list(rule_ref.parent.parent.collection("notificationLogs").stream())
    assert history == []

    # The configured collection-group index path is exercised as well.
    loaded = firestore_client.list_enabled_rules()
    assert any(item.uid == uid and item.id == "rule" for item in loaded)


def test_deleted_rule_is_not_resurrected_when_occurrence_finalizes():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    uid, rule_ref = _refs("rule")
    rule_ref.set(_rule_data(uid, {"kind": "monthlyFirstDay", "time": "07:00", "tz": "Asia/Seoul"}, scheduled, scheduled))
    event_id = f"rule:{scheduled.isoformat()}"
    firestore_client.claim_occurrence(
        uid, "rule", event_id, _payload(event_id, scheduled), None,
        "worker", scheduled,
    )
    rule_ref.delete()

    firestore_client.finalize_occurrence(uid, "rule", event_id, "cancelled", {}, worker_id="worker")

    assert not rule_ref.get().exists
    stored = rule_ref.parent.parent.collection("notificationLogs").document(event_id).get().to_dict()
    assert stored and stored["status"] == "cancelled"


def test_schedule_edit_between_query_and_claim_rejects_stale_occurrence():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    uid, rule_ref = _refs("rule")
    rule_ref.set(_rule_data(
        uid,
        {"kind": "monthlyFirstDay", "time": "07:00", "tz": "Asia/Seoul"},
        scheduled,
        scheduled,
    ))
    changed_at = datetime(2026, 8, 1, 9, 18, tzinfo=KST)
    rule_ref.update({
        "trigger": {
            "mode": "recurring",
            "recurrence": {"kind": "monthlyLastDay", "time": "12:15", "tz": "Asia/Seoul"},
        },
        "nextScheduledAt": datetime(2026, 8, 31, 12, 15, tzinfo=KST),
        "scheduleChangedAt": changed_at,
        "scheduleStatus": "schedule_changed",
    })
    event_id = f"rule:{scheduled.isoformat()}"

    result = firestore_client.claim_occurrence(
        uid, "rule", event_id, _payload(event_id, scheduled),
        datetime(2026, 9, 1, 7, 0, tzinfo=KST), "stale-worker", scheduled,
        expected_next_scheduled_at=scheduled,
        expected_schedule_changed_at=None,
    )

    assert result["claim"] == "schedule_changed"
    assert not rule_ref.parent.parent.collection("notificationLogs").document(event_id).get().exists
    stored_rule = rule_ref.get().to_dict()
    assert stored_rule and stored_rule["scheduleChangedAt"] == changed_at.astimezone(timezone.utc)
    assert stored_rule["nextScheduledAt"] == datetime(2026, 8, 31, 3, 15, tzinfo=timezone.utc)


def test_disable_after_occurrence_claim_cancels_before_provider_boundary():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    uid, rule_ref = _refs("rule")
    rule_ref.set(_rule_data(uid, {"kind": "monthlyFirstDay", "time": "07:00", "tz": "Asia/Seoul"}, scheduled, scheduled))
    event_id = f"rule:{scheduled.isoformat()}"
    firestore_client.claim_occurrence(
        uid, "rule", event_id, _payload(event_id, scheduled), None,
        "worker", scheduled,
    )
    rule_ref.update({"enabled": False})

    outcome = firestore_client.begin_channel_attempt(
        uid, "rule", event_id, "telegram", "worker", scheduled,
    )

    assert outcome == "rule_disabled"
    stored = rule_ref.parent.parent.collection("notificationLogs").document(event_id).get().to_dict()
    assert stored and stored["status"] == "disabled" and stored["pending"] is False
    assert all(row["status"] == "failed" for row in stored["channels"])


def test_audit_recovery_rejects_legacy_log_with_random_document_id():
    scheduled = datetime(2026, 8, 1, 7, 0, tzinfo=KST)
    uid, rule_ref = _refs("rule")
    rule_ref.set(_rule_data(
        uid,
        {"kind": "monthlyFirstDay", "time": "07:00", "tz": "Asia/Seoul"},
        scheduled,
        datetime(2026, 9, 1, 7, 0, tzinfo=KST),
    ))
    event_id = f"rule:{scheduled.isoformat()}"
    rule_ref.parent.parent.collection("notificationLogs").document("legacy-random-id").set({
        "eventId": event_id,
        "ruleId": "rule",
        "status": "sent",
    })

    with pytest.raises(SystemExit, match="legacy occurrence log already exists"):
        audit._prepare_recovery(
            uid, "rule", "2026-08-01T07:00:00+09:00", acknowledged=True,
        )


def test_two_real_workers_initialize_legacy_cursor_once_and_converge():
    uid, rule_ref = _refs("rule")
    recurrence = {"kind": "monthlyLastDay", "time": "12:15", "tz": "Asia/Seoul"}
    data = _rule_data(uid, recurrence, datetime(2025, 1, 1, tzinfo=KST))
    rule_ref.set(data)
    trigger = data["trigger"]
    future = datetime(2026, 8, 31, 12, 15, tzinfo=KST)
    cutoff = datetime(2026, 8, 2, 9, 0, tzinfo=KST)

    def initialize(_worker: str):
        return firestore_client.initialize_legacy_scheduler_cursor(
            uid, "rule", trigger, None, future, cutoff,
        )["initialization"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(initialize, ["worker-a", "worker-b"]))

    assert sorted(outcomes) == ["already_initialized", "initialized"]
    stored = rule_ref.get().to_dict()
    assert stored and stored["nextScheduledAt"] == future.astimezone(timezone.utc)
    assert stored["schedulerMigration"]["initializedNextScheduledAt"] == future.astimezone(timezone.utc)
    assert list(rule_ref.parent.parent.collection("notificationLogs").stream()) == []


@pytest.mark.parametrize("race", ["edit", "delete", "disable"])
def test_real_legacy_initialization_rechecks_rule_state_without_partial_recreation(race):
    uid, rule_ref = _refs("rule")
    recurrence = {"kind": "weekly", "weekday": 1, "time": "10:00", "tz": "Asia/Seoul"}
    data = _rule_data(uid, recurrence, datetime(2025, 1, 1, tzinfo=KST))
    rule_ref.set(data)
    if race == "edit":
        rule_ref.update({
            "trigger": {
                "mode": "recurring",
                "recurrence": {"kind": "weekly", "weekday": 2, "time": "11:00", "tz": "Asia/Seoul"},
            },
        })
    elif race == "delete":
        rule_ref.delete()
    else:
        rule_ref.update({"enabled": False})

    result = firestore_client.initialize_legacy_scheduler_cursor(
        uid,
        "rule",
        data["trigger"],
        None,
        datetime(2026, 8, 3, 10, 0, tzinfo=KST),
        datetime(2026, 8, 2, 9, 0, tzinfo=KST),
    )

    expected = "schedule_changed" if race == "edit" else "inactive"
    assert result["initialization"] == expected
    stored = rule_ref.get()
    if race == "delete":
        assert not stored.exists
    else:
        assert "schedulerMigration" not in (stored.to_dict() or {})
        assert "nextScheduledAt" not in (stored.to_dict() or {})


def test_worker_immediately_after_legacy_initialization_uses_future_cursor_normally():
    uid, rule_ref = _refs("rule")
    recurrence = {"kind": "monthlyLastDay", "time": "12:15", "tz": "Asia/Seoul"}
    data = _rule_data(uid, recurrence, datetime(2025, 1, 1, tzinfo=KST))
    rule_ref.set(data)
    telegram = FakeChannel("telegram")
    push = FakeChannel("push")
    engine = build_engine(FakeDataSource(), firestore_client, {"telegram": telegram, "push": push})

    first = engine.process_rule(
        AlertRule.from_dict(data),
        now=datetime(2026, 8, 2, 9, 0, tzinfo=KST),
    )
    reloaded = AlertRule.from_dict({"id": "rule", "uid": uid, **(rule_ref.get().to_dict() or {})})
    second = engine.process_rule(
        reloaded,
        now=datetime(2026, 8, 31, 12, 16, tzinfo=KST),
    )

    assert first.status == "legacy_cursor_initialized"
    assert second.status == "delivered"
    assert telegram.calls == push.calls == 1
    history = list(rule_ref.parent.parent.collection("notificationLogs").stream())
    assert len(history) == 1
    assert history[0].to_dict()["scheduledFor"] == "2026-08-31T03:15:00+00:00"


def test_explicit_recovery_and_legacy_initialization_race_converges_to_recovery_request():
    uid, rule_ref = _refs("rule")
    recurrence = {"kind": "monthlyFirstDay", "time": "07:00", "tz": "Asia/Seoul"}
    data = _rule_data(uid, recurrence, datetime(2019, 1, 1, tzinfo=KST))
    rule_ref.set(data)

    def initialize():
        return firestore_client.initialize_legacy_scheduler_cursor(
            uid,
            "rule",
            data["trigger"],
            None,
            datetime(2026, 9, 1, 7, 0, tzinfo=KST),
            datetime(2026, 8, 2, 9, 0, tzinfo=KST),
        )

    def recover():
        return audit._prepare_recovery(
            uid, "rule", "2020-08-01T07:00:00+09:00", acknowledged=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        init_future = pool.submit(initialize)
        recovery_future = pool.submit(recover)
        init_future.result()
        recovery_future.result()

    stored = rule_ref.get().to_dict()
    assert stored and stored["durableSchedulerVersion"] == 1
    assert stored["scheduleStatus"] == "recovery_requested"
    assert stored["nextScheduledAt"] == datetime(2020, 7, 31, 22, 0, tzinfo=timezone.utc)
    assert stored["schedulerRecovery"]["status"] == "requested"
    assert stored["schedulerRecovery"]["duplicateRiskAcknowledged"] is True
    assert list(rule_ref.parent.parent.collection("notificationLogs").stream()) == []
