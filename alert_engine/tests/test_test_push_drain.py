"""Test-push drain reuses the production delivery path (single source of truth).

These tests prove the browser "테스트 Push/Telegram" bridge:

  process_test_requests -> atomic request claim -> deliver()
      -> channel_registry["push"]  (the SAME registry production process_rule uses)

so test and production share ONE push implementation, and success is judged only
by the channel's returned ChannelSendResult (never fabricated).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from alert_engine import config as engine_config
from alert_engine import firestore_client
from alert_engine import test_push
from alert_engine.channels import build_default_channels
from alert_engine.channels.push import PushChannel
from alert_engine.engine import AlertEngine
from alert_engine.models import AlertSettings

from .conftest import FakeChannel, FakeFirestore


@pytest.fixture(autouse=True)
def _no_retry(monkeypatch):
    """Disable delivery retries/backoff so a failed FakeChannel is attempted
    exactly once (fast). deliver() reads these config globals via load_config."""
    monkeypatch.setattr(engine_config, "DELIVERY_MAX_RETRIES", 0)
    monkeypatch.setattr(engine_config, "DELIVERY_BACKOFF_BASE_SECONDS", 0.0)


class QueueFirestore(FakeFirestore):
    """FakeFirestore + the test-push request queue surface."""

    def __init__(self, requests: List[Dict[str, Any]], settings: Optional[AlertSettings] = None):
        super().__init__(settings=settings)
        self._requests = requests
        for request in self._requests:
            request.setdefault("requestedAt", datetime.now(timezone.utc))
        self.marks: List[Dict[str, Any]] = []

    def list_pending_test_requests(self, uid: Optional[str] = None, limit: int = 50):
        return [dict(r) for r in self._requests if r.get("status") in {"pending", "processing"}]

    def claim_test_request(self, uid, request_id, payload, worker_id, now, lease_seconds=600):
        with self._lock:
            req = next(r for r in self._requests if r["id"] == request_id)
            if req["status"] not in {"pending", "processing"}:
                return {"claim": "terminal"}
            record = self.logs.get(payload["id"])
            if record and record.get("leaseExpiresAt", now) > now:
                return {"claim": "in_progress"}
            if record is None:
                record = dict(payload)
                self.logs[payload["id"]] = record
            record.update(leaseOwner=worker_id, leaseExpiresAt=now + timedelta(seconds=lease_seconds))
            req["status"] = "processing"
            return {"claim": "claimed", "record": dict(record)}

    def begin_test_channel_attempt(self, uid, request_id, channel, worker_id, now):
        with self._lock:
            record = self.logs[f"settings-test:request:{request_id}"]
            if record["leaseOwner"] != worker_id:
                return False
            for row in record["channels"]:
                if row["channel"] == channel and row["status"] == "pending":
                    row["status"] = "sending"
                    return True
            return False

    def finish_test_request(self, uid, request_id, worker_id, status, results, error=None):
        with self._lock:
            record = self.logs[f"settings-test:request:{request_id}"]
            if record["leaseOwner"] != worker_id:
                raise RuntimeError("lost lease")
            self.mark_test_request(uid, request_id, status, results, record["id"], error)
            next(r for r in self._requests if r["id"] == request_id)["status"] = status
            record.update(status=status, channels=[SimpleNamespace(**row) for row in results])
            self.logs[record["id"]] = SimpleNamespace(**record)

    def mark_test_request(self, uid, req_id, status, results=None, log_id=None, error=None):
        self.marks.append({
            "uid": uid, "req_id": req_id, "status": status,
            "results": results, "log_id": log_id, "error": error,
        })


def _engine(firestore, push: FakeChannel, telegram: Optional[FakeChannel] = None) -> AlertEngine:
    registry = {"push": push, "telegram": telegram or FakeChannel("telegram")}
    return AlertEngine(channel_registry=registry, firestore=firestore)


def test_default_registry_push_is_pushchannel():
    """The registry the drain/engine build by default is the production one:
    the only 'push' sender is PushChannel."""
    assert isinstance(build_default_channels()["push"], PushChannel)


def test_drain_delivers_through_channel_registry_and_marks_done():
    fs = QueueFirestore(
        [{"id": "req-1", "uid": "u1", "status": "pending", "channels": ["push"]}],
        settings=AlertSettings(globalEnabled=True, pushTokens=["tok-1"]),
    )
    push = FakeChannel("push", status="sent")
    counts = test_push.process_test_requests(engine=_engine(fs, push), firestore=fs)

    # The SAME channel the engine would use for production was invoked exactly once.
    assert push.calls == 1
    assert counts["processed"] == counts["sent"] == 1
    assert counts["error"] == counts["failed"] == 0

    # Request finalized as done with the channel's REAL result + an isTest log.
    assert len(fs.marks) == 1
    mark = fs.marks[0]
    assert mark["status"] == "done"
    assert mark["results"][0]["channel"] == "push" and mark["results"][0]["status"] == "sent"
    assert mark["results"][0]["attemptCount"] == 1
    assert mark["results"][0]["attemptedAt"] <= mark["results"][0]["completedAt"]
    log = fs.logs[mark["log_id"]]
    assert log.isTest is True
    assert [c.status for c in log.channels] == ["sent"]


def test_drain_reports_channel_failure_verbatim_not_fabricated():
    fs = QueueFirestore(
        [{"id": "req-2", "uid": "u1", "status": "pending", "channels": ["push"]}],
        settings=AlertSettings(globalEnabled=True, pushTokens=["tok-1"]),
    )
    push = FakeChannel("push", status="failed", error="all 1 token(s) failed: UNREGISTERED")
    counts = test_push.process_test_requests(engine=_engine(fs, push), firestore=fs)

    assert push.calls == 1
    assert counts["failed"] == 1 and counts["sent"] == 0
    mark = fs.marks[0]
    assert mark["status"] == "failed"
    # Success is judged only by the channel's return value.
    assert mark["results"][0]["status"] == "failed"
    assert mark["results"][0]["error"] == "all 1 token(s) failed: UNREGISTERED"
    assert "UNREGISTERED" in mark["error"]


def test_drain_skips_when_no_credentials():
    fs = QueueFirestore(
        [{"id": "req-3", "uid": "u1", "status": "pending", "channels": ["push"]}],
        settings=AlertSettings(globalEnabled=True, pushTokens=[]),  # no token
    )
    push = FakeChannel("push", status="sent")
    counts = test_push.process_test_requests(engine=_engine(fs, push), firestore=fs)

    # REQ-022.4: no credential -> nothing delivered, channel never called.
    assert push.calls == 0
    assert counts["failed"] == 1
    assert fs.marks[0]["status"] == "failed"


def test_drain_respects_global_disabled_before_channel_delivery():
    fs = QueueFirestore(
        [{"id": "req-off", "uid": "u1", "status": "pending", "channels": ["push"]}],
        settings=AlertSettings(globalEnabled=False, pushTokens=["tok-1"]),
    )
    push = FakeChannel("push", status="sent")

    counts = test_push.process_test_requests(engine=_engine(fs, push), firestore=fs)

    assert counts["processed"] == counts["failed"] == 1
    assert counts["sent"] == counts["error"] == 0
    assert push.calls == 0
    assert len(fs.logs) == 1  # Explicit failure evidence, no provider call.
    assert fs.marks[0]["status"] == "failed"
    assert fs.marks[0]["error"] == "global alerts are disabled"


def test_drain_isolates_one_bad_request():
    fs = QueueFirestore(
        [
            {"id": "boom", "uid": "u1", "status": "pending", "channels": ["push"]},
            {"id": "ok", "uid": "u1", "status": "pending", "channels": ["push"]},
        ],
        settings=AlertSettings(globalEnabled=True, pushTokens=["tok-1"]),
    )
    push = FakeChannel("push", status="sent", raises=True)  # raises on first, but deliver isolates
    # deliver() catches channel exceptions -> failed result, so no crash; both processed.
    counts = test_push.process_test_requests(engine=_engine(fs, push), firestore=fs)
    assert counts["processed"] == 2
    assert {m["req_id"] for m in fs.marks} == {"boom", "ok"}


def test_all_user_pending_query_requires_index_without_full_scan_fallback(monkeypatch):
    class MissingIndexQuery:
        def where(self, **kwargs):
            return self

        def stream(self):
            raise RuntimeError("The query requires an index")

    class FakeDb:
        def __init__(self):
            self.collection_group_calls = 0

        def collection_group(self, name):
            assert name == "testPushRequests"
            self.collection_group_calls += 1
            return MissingIndexQuery()

    db = FakeDb()
    monkeypatch.setattr(firestore_client, "get_db", lambda: db)

    with pytest.raises(RuntimeError, match="field override in firestore.indexes.json"):
        firestore_client.list_pending_test_requests()

    assert db.collection_group_calls == 1
