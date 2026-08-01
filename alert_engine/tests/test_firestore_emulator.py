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

    assert firestore_client.begin_channel_attempt(uid, event_id, "telegram", "worker-new", first_now)
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


def test_real_legacy_cursor_backfill_catches_up_and_history_query_exposes_occurrence():
    scheduled = datetime(2026, 7, 31, 12, 15, tzinfo=KST)
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

    result = engine.process_rule(rule, now=datetime(2026, 7, 31, 12, 20, tzinfo=KST))

    assert result.status == "delivered"
    assert telegram.calls == push.calls == 1
    stored_rule = rule_ref.get().to_dict()
    assert stored_rule and stored_rule["nextScheduledAt"] == datetime(2026, 8, 31, 3, 15, tzinfo=timezone.utc)
    history = list(rule_ref.parent.parent.collection("notificationLogs").stream())
    assert len(history) == 1
    assert history[0].to_dict()["scheduledFor"] == scheduled.astimezone(timezone.utc).isoformat()

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
