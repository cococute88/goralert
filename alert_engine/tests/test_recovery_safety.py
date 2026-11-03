"""Outage, read-only preview and provider/DB crash-boundary regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from alert_engine import test_push
from alert_engine.backlog import backlog_plan
from alert_engine.engine import STATUS_ERROR, STATUS_DRY_RUN
from alert_engine.event import make_event_id
from alert_engine.models import AlertSettings
from .conftest import FakeChannel, FakeDataSource, FakeFirestore, build_engine, make_ratio_rule
from .test_test_push_drain import QueueFirestore, _engine
from .test_durable_scheduling import _rule, KST


def queue(channels=None, **kwargs):
    return QueueFirestore([{"id": "stable", "uid": "u1", "status": "pending", "channels": channels or ["push"], **kwargs}],
                          settings=AlertSettings(globalEnabled=True, pushTokens=["test"], telegramChatId="chat"))


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("broken_settings", [True, False])
def test_dry_run_never_attempts_any_write_or_channel(enabled, broken_settings):
    fs = queue()
    fs._settings.globalEnabled = enabled
    def forbidden(*args, **kwargs):
        raise AssertionError("write attempted")
    fs.claim_test_request = fs.mark_test_request = fs.finish_test_request = forbidden
    fs.record_channel_result = fs.begin_test_channel_attempt = fs.write_notification_log = forbidden
    if broken_settings:
        def unavailable(uid):
            raise RuntimeError("read unavailable")
        fs.load_alert_settings = unavailable
    push = FakeChannel("push")
    result = test_push.process_test_requests(engine=_engine(fs, push), firestore=fs, dry_run=True)
    assert result["sent"] == result["failed"] == 0
    assert result["error" if broken_settings else "preview"] == 1
    assert fs.logs == {} and fs.marks == []
    assert fs._requests[0]["status"] == "pending"
    assert push.calls == 0


@pytest.mark.parametrize("channels,push_status,tg_status,key", [
    (["push"], "sent", "sent", "sent"),
    (["telegram"], "sent", "sent", "sent"),
    (["push", "telegram"], "failed", "sent", "partial"),
    (["push", "telegram"], "sent", "failed", "partial"),
    (["push", "telegram"], "failed", "failed", "failed"),
    (["push", "telegram"], "unknown", "sent", "unknown"),
])
def test_channels_have_independent_truthful_outcomes(channels, push_status, tg_status, key):
    fs = queue(channels)
    push, tg = FakeChannel("push", push_status), FakeChannel("telegram", tg_status)
    engine = _engine(fs, push, tg)
    result = test_push.process_test_requests(engine=engine, firestore=fs)
    assert result[key] == 1
    assert push.calls == int("push" in channels)
    assert tg.calls == int("telegram" in channels)
    test_push.process_test_requests(engine=engine, firestore=fs)
    assert push.calls == int("push" in channels)  # terminal requests are never consumed twice


def test_two_workers_claim_same_request_once():
    fs = queue()
    push = FakeChannel("push")
    engine = _engine(fs, push)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: test_push.process_test_requests(engine=engine, firestore=fs), range(2)))
    assert push.calls == 1
    assert len(fs.logs) == 1
    assert "settings-test:request:stable" in fs.logs


def test_unexpired_lease_waits_and_expired_pending_lease_resumes_once():
    fs = queue()
    push = FakeChannel("push")
    engine = _engine(fs, push)
    event_id = "settings-test:request:stable"
    payload = {"id": event_id, "status": "processing", "isTest": True,
               "message": {"title": "saved", "body": "immutable"},
               "channels": [{"channel": "push", "status": "pending"}]}
    fs.claim_test_request("u1", "stable", payload, "stopped-worker", datetime.now(timezone.utc))
    assert test_push.process_test_requests(engine=engine, firestore=fs)["busy"] == 1
    assert push.calls == 0
    fs.logs[event_id]["leaseExpiresAt"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert test_push.process_test_requests(engine=engine, firestore=fs)["sent"] == 1
    assert push.calls == 1


def test_provider_success_db_result_failure_recovers_unknown_without_resend():
    fs = queue(["push", "telegram"])
    push, tg = FakeChannel("push"), FakeChannel("telegram")
    engine = _engine(fs, push, tg)
    original = fs.record_channel_result
    def failure(*args, **kwargs):
        raise TimeoutError("write timed out")
    fs.record_channel_result = failure
    assert test_push.process_test_requests(engine=engine, firestore=fs)["error"] == 1
    assert push.calls == 1 and tg.calls == 0
    fs.record_channel_result = original
    fs.logs["settings-test:request:stable"]["leaseExpiresAt"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    result = test_push.process_test_requests(engine=engine, firestore=fs)
    assert result["unknown"] == 1
    assert push.calls == tg.calls == 1
    assert fs.marks[-1]["results"][0]["status"] == "unknown"


def test_finish_failure_reuses_confirmed_success():
    fs = queue()
    engine = _engine(fs, FakeChannel("push"))
    original = fs.finish_test_request
    fs.finish_test_request = lambda *a, **k: (_ for _ in ()).throw(TimeoutError())
    assert test_push.process_test_requests(engine=engine, firestore=fs)["error"] == 1
    fs.finish_test_request = original
    fs.logs["settings-test:request:stable"]["leaseExpiresAt"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert test_push.process_test_requests(engine=engine, firestore=fs)["sent"] == 1
    assert engine.channels["push"].calls == 1


def test_old_test_requests_are_held_and_zero_requests_is_not_success_count():
    fs = queue(requestedAt=datetime.now(timezone.utc) - timedelta(days=30))
    push = FakeChannel("push")
    engine = _engine(fs, push)
    counts = test_push.process_test_requests(engine=engine, firestore=fs)
    assert counts["held"] == 1 and counts["sent"] == 0 and push.calls == 0
    empty = test_push.process_test_requests(engine=engine, firestore=fs)
    assert empty["processed"] == empty["sent"] == 0


def test_test_request_without_age_evidence_is_held():
    fs = queue(requestedAt=None)
    push = FakeChannel("push")
    assert test_push.process_test_requests(engine=_engine(fs, push), firestore=fs)["held"] == 1
    assert push.calls == 0


@pytest.mark.parametrize("read_result", [False, True, "error"])
def test_reservation_failure_never_authorizes_delivery_or_crossing_write(read_result):
    fs = FakeFirestore()
    def unavailable(*a): raise TimeoutError()
    fs.reserve_log = unavailable
    def exists(*a):
        if read_result == "error": raise TimeoutError()
        return read_result
    fs.log_exists = exists
    tg = FakeChannel("telegram")
    rule = make_ratio_rule(comparator="crossUp", threshold=25, channels=["telegram"])
    rule.lastValue = 20
    engine = build_engine(FakeDataSource(ratio=30), fs, {"telegram": tg})
    result = engine.process_rule(rule, now=datetime(2026, 11, 3, tzinfo=timezone.utc))
    assert result.status == STATUS_ERROR
    assert tg.calls == 0 and fs.state_updates == [] and rule.lastValue == 20


def test_months_of_backlog_hold_preserves_original_cursor_and_future_schedule():
    old = datetime(2026, 10, 1, 7, tzinfo=KST)
    now = datetime(2026, 11, 3, 12, tzinfo=KST)
    rule = _rule("monthlyFirstDay", old)
    plan = backlog_plan(rule, old, now)
    assert plan["fromInclusive"] == old.astimezone(timezone.utc).isoformat()
    assert plan["nextCursor"] == datetime(2026, 12, 1, 7, tzinfo=KST).astimezone(timezone.utc)
    assert rule.nextScheduledAt == old.astimezone(timezone.utc)
    assert backlog_plan(rule, now - timedelta(hours=2), now) is None
    assert backlog_plan(rule, now + timedelta(hours=2), now) is None


@pytest.mark.parametrize("selector", [False, True])
def test_once_daily_backlog_advances_only_for_selector_backed_calendar(selector):
    old = datetime(2026, 10, 1, 7, tzinfo=KST)
    now = datetime(2026, 11, 3, 12, tzinfo=KST)
    rule = _rule("calendar", old)
    rule.trigger.mode = "once"
    if selector:
        from alert_engine.models import Condition
        rule.condition = Condition.from_dict({"kind": "date", "selector": {"source": "calendarEvents"}})
    plan = backlog_plan(rule, old, now)
    assert (plan["nextCursor"] is not None) is selector
    assert rule.nextScheduledAt == old.astimezone(timezone.utc)


def test_backlog_boundary_is_strictly_older_than_24_hours():
    old = datetime(2026, 10, 31, 7, tzinfo=KST)
    rule = _rule("monthlyLastDay", old)
    assert backlog_plan(rule, old, old + timedelta(hours=24)) is None
    plan = backlog_plan(rule, old, old + timedelta(hours=24, microseconds=1))
    assert plan["fromInclusive"] == old.astimezone(timezone.utc).isoformat()
    assert plan["nextCursor"] == datetime(2026, 11, 30, 7, tzinfo=KST).astimezone(timezone.utc)


def test_large_stale_test_queue_is_held_across_multiple_drains_without_sends():
    class BoundedQueue(QueueFirestore):
        def list_pending_test_requests(self, uid=None, limit=50):
            return super().list_pending_test_requests(uid, limit)[:limit]
    fs = BoundedQueue([{"id": f"old-{n}", "uid": "u1", "status": "pending", "channels": ["push", "telegram"],
                        "requestedAt": datetime.now(timezone.utc) - timedelta(days=31)} for n in range(120)],
                      settings=AlertSettings(globalEnabled=True, pushTokens=["offline"], telegramChatId="offline"))
    push, tg = FakeChannel("push"), FakeChannel("telegram")
    engine = _engine(fs, push, tg)
    assert [test_push.process_test_requests(engine=engine, firestore=fs)["held"] for _ in range(3)] == [50, 50, 20]
    assert push.calls == tg.calls == 0
    assert len(fs.logs) == 120
    assert test_push.process_test_requests(engine=engine, firestore=fs)["processed"] == 0


def test_explicit_single_recovery_preserves_existing_future_cursor():
    scheduled = datetime(2026, 10, 1, 7, tzinfo=KST)
    future = datetime(2026, 12, 1, 7, tzinfo=KST)
    rule = _rule("monthlyFirstDay", scheduled)
    rule.schedulerRecovery = {"status": "requested", "duplicateRiskAcknowledged": True,
                              "occurrenceId": make_event_id(rule.id, scheduled.isoformat()),
                              "previousNextScheduledAt": future}
    fs = FakeFirestore()
    push = FakeChannel("push")
    engine = build_engine(FakeDataSource(), fs, {"push": push, "telegram": FakeChannel("telegram")})
    assert engine.process_rule(rule, now=datetime(2026, 11, 3, tzinfo=KST)).status == "delivered"
    assert fs.state_updates[0]["next_scheduled_at"] == future.astimezone(timezone.utc)


def test_backlog_hold_is_fail_closed_and_dry_run_does_not_apply():
    old = datetime(2026, 10, 1, 7, tzinfo=KST)
    rule = _rule("monthlyFirstDay", old)
    fs = FakeFirestore()
    def unavailable(*a): raise TimeoutError()
    fs.hold_scheduler_backlog = unavailable
    push = FakeChannel("push")
    engine = build_engine(FakeDataSource(), fs, {"push": push})
    now = datetime(2026, 11, 3, tzinfo=KST)
    assert engine.process_rule(rule, now=now, dry_run=True).status == STATUS_DRY_RUN
    assert engine.process_rule(rule, now=now).status == STATUS_ERROR
    assert push.calls == 0 and fs.state_updates == [] and fs.logs == {}


def test_dry_run_never_repairs_terminal_occurrence():
    fs = FakeFirestore()
    rule = _rule("monthlyFirstDay", datetime(2026, 12, 1, 7, tzinfo=KST))
    rule.scheduleStatus = "processing"
    rule.lastOccurrenceId = "previous"
    fs.logs["previous"] = {"status": "sent"}
    fs.finalize_occurrence = lambda *a, **k: (_ for _ in ()).throw(AssertionError("write"))
    engine = build_engine(FakeDataSource(), fs, {"push": FakeChannel("push")})
    result = engine.process_rule(rule, now=datetime(2026, 11, 3, tzinfo=KST), dry_run=True)
    assert result.status == "skipped_duplicate"
    assert fs.state_updates == []


@pytest.mark.parametrize("counts,expected", [
    ({"processed": 0, "sent": 0}, 0), ({"sent": 1}, 0),
    ({"failed": 1}, 1), ({"partial": 1}, 1), ({"unknown": 1}, 1),
    ({"error": 1}, 1), ({"held": 1}, 1),
])
def test_drain_cli_exit_status_matches_delivery_truth(monkeypatch, counts, expected):
    from alert_engine import main
    from alert_engine.config import EngineConfig
    monkeypatch.setattr(main, "load_config", lambda: EngineConfig(has_inline_service_account=True))
    monkeypatch.setattr(test_push, "process_test_requests", lambda *a, **k: counts)
    assert main.run(["--test-push"]) == expected


def test_heartbeat_updates_success_only_after_successful_full_run():
    from alert_engine.health import write_engine_health
    class Store:
        def __init__(self): self.writes = []
        def get_db(self): return self
        def collection(self, name): assert name == "systemHealth"; return self
        def document(self, name): assert name == "alertEngine"; return self
        def set(self, data, merge): self.writes.append(data)
    store = Store()
    now = datetime.now(timezone.utc)
    write_engine_health(store, now, {"delivered": 1}, {"push_sent": 1}, 0, 0)
    assert "lastSuccessfulRunAt" in store.writes[0] and "lastPushAcceptedAt" in store.writes[0]
    write_engine_health(store, now, {"failed": 1}, {}, 0, 1)
    assert "lastSuccessfulRunAt" not in store.writes[1]
    assert "lastEvaluationSuccessAt" in store.writes[1]
    write_engine_health(store, now, {"error": 1}, {}, 1, 0)
    assert "lastSuccessfulRunAt" not in store.writes[2] and "lastEvaluationSuccessAt" not in store.writes[2]


@pytest.mark.parametrize("args", [["--dry-run"], ["--uid", "u1"], ["--job-scope", "market"]])
def test_partial_or_dry_run_cannot_publish_healthy_heartbeat(monkeypatch, args):
    from alert_engine import main, health, firestore_client
    from alert_engine.config import EngineConfig
    monkeypatch.setattr(main, "load_config", lambda: EngineConfig(has_inline_service_account=True))
    monkeypatch.setattr(firestore_client, "list_enabled_rules", lambda *a: [])
    def forbidden(*a): raise AssertionError("heartbeat write attempted")
    monkeypatch.setattr(health, "write_engine_health", forbidden)
    assert main.run(args) == 0
