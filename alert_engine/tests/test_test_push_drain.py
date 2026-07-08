"""Test-push drain reuses the production delivery path (single source of truth).

These tests prove the browser "테스트 Push/Telegram" bridge:

  process_test_requests -> AlertEngine.send_test_alert -> deliver()
      -> channel_registry["push"]  (the SAME registry production process_rule uses)

so test and production share ONE push implementation, and success is judged only
by the channel's returned ChannelSendResult (never fabricated).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from alert_engine import config as engine_config
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
        self.marks: List[Dict[str, Any]] = []

    def list_pending_test_requests(self, uid: Optional[str] = None, limit: int = 50):
        return [r for r in self._requests if r.get("status") == "pending"]

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
    assert counts == {"processed": 1, "sent": 1, "failed": 0, "error": 0}

    # Request finalized as done with the channel's REAL result + an isTest log.
    assert len(fs.marks) == 1
    mark = fs.marks[0]
    assert mark["status"] == "done"
    assert mark["results"] == [{"channel": "push", "status": "sent"}]
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
    assert mark["results"] == [{"channel": "push", "status": "failed", "error": "all 1 token(s) failed: UNREGISTERED"}]
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
