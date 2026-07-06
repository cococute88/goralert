"""Self-contained functional smoke tests (stdlib only — no firebase/yfinance).

Runnable two ways:
    pytest alert_engine/tests/test_smoke.py
    python -m alert_engine.tests.test_smoke   # plain-python fallback runner

Uses in-memory fakes for Firestore / datasource / channels so the full
process_rule pipeline (evaluate -> gating -> delivery -> idempotency -> state)
is exercised without any external dependency or network.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from alert_engine.compare import compare
from alert_engine.engine import (
    AlertEngine,
    STATUS_DELIVERED,
    STATUS_DUPLICATE,
    STATUS_NOT_TRIGGERED,
    _within_quiet_hours,
)
from alert_engine.models import AlertRule, AlertSettings, MessageTemplate, QuietHours
from alert_engine.recurrence import bucket_time, due_now, next_occurrence
from alert_engine.backtest import backtest_rule


# --- fakes -------------------------------------------------------------------


class FakeDataSource:
    def __init__(self, metric=None, ratio=None, events=None):
        self._metric = metric
        self._ratio = ratio
        self._events = events or []

    def now(self):
        return datetime.now(timezone.utc)

    def get_metric(self, metric):
        return self._metric

    def get_ratio(self, num, den):
        return self._ratio

    def get_dividend_metric(self, ticker):
        return None

    def get_calendar_events(self, uid, selector, firestore=None):
        return list(self._events)


class FakeChannel:
    def __init__(self, name, status="sent"):
        self.name = name
        self._status = status
        self.calls = 0

    def send(self, message, settings, **kwargs):
        from alert_engine.channels.base import ChannelSendResult
        self.calls += 1
        return ChannelSendResult(self.name, self._status, error=None if self._status == "sent" else "boom")


class FakeFirestore:
    def __init__(self, settings=None):
        self.logs = {}
        self.state_updates = []
        self._settings = settings or AlertSettings(globalEnabled=True, telegramChatId="chat", pushTokens=["t"])

    def load_alert_settings(self, uid):
        return self._settings

    def log_exists(self, uid, event_id):
        return event_id in self.logs

    def reserve_log(self, uid, event_id):
        if event_id in self.logs:
            return False
        self.logs[event_id] = None  # reservation placeholder
        return True

    def write_notification_log(self, uid, log):
        self.logs[log.id] = log

    def update_rule_state(self, uid, rule_id, last_triggered_at=None, last_value=None, enabled=None, engine_version=None):
        self.state_updates.append({
            "rule_id": rule_id, "last_triggered_at": last_triggered_at,
            "last_value": last_value, "enabled": enabled, "engine_version": engine_version,
        })


def _ratio_rule():
    return AlertRule.from_dict({
        "id": "rule-spy-schd",
        "uid": "u1",
        "kind": "ratio",
        "name": "SPY→SCHD",
        "enabled": True,
        "condition": {"kind": "ratio", "numerator": "SPY", "denominator": "SCHD", "comparator": "gte", "threshold": 25},
        "trigger": {"mode": "recurring"},
        "delivery": {"channels": ["telegram", "push"], "message": {"title": "t", "body": "{value}"}},
    })


def _engine(ds, fs):
    from alert_engine.evaluators import build_default_registry
    channels = {"telegram": FakeChannel("telegram"), "push": FakeChannel("push")}
    return AlertEngine(
        datasource=ds,
        evaluator_registry=build_default_registry(ds),
        channel_registry=channels,
        firestore=fs,
    ), channels


# --- tests -------------------------------------------------------------------


def test_compare_basic():
    assert compare(30, "gte", 25) is True
    assert compare(20, "gte", 25) is False
    assert compare(24, "crossUp", 25, prev=20) is False
    assert compare(26, "crossUp", 25, prev=20) is True
    assert compare(24, "crossDown", 25, prev=30) is True


def test_quiet_hours_wraparound():
    qh = QuietHours("22:00", "07:00", "Asia/Seoul")
    # 23:00 KST == 14:00 UTC
    now = datetime(2024, 1, 1, 14, 0, tzinfo=timezone.utc)
    assert _within_quiet_hours(qh, now) is True
    # 12:00 KST == 03:00 UTC -> not quiet
    now2 = datetime(2024, 1, 1, 3, 0, tzinfo=timezone.utc)
    assert _within_quiet_hours(qh, now2) is False


def test_recurrence_weekly_and_bucket():
    from alert_engine.models import Recurrence, TriggerPolicy
    rec = Recurrence(kind="weekly", weekday=6, time="08:00", tz="Asia/Seoul")  # Saturday
    occ = next_occurrence(rec, datetime(2024, 5, 1, 0, 0, tzinfo=timezone.utc))
    assert occ is not None
    # 2024-05-04 is the first Saturday on/after 2024-05-01.
    assert occ.date().isoformat() == "2024-05-04"
    # due_now within a window around the occurrence.
    assert due_now(rec, occ, window_minutes=15) is True
    b1 = bucket_time(occ, TriggerPolicy(mode="recurring", recurrence=rec))
    b2 = bucket_time(occ, TriggerPolicy(mode="recurring", recurrence=rec))
    assert b1 == b2  # deterministic bucket


def test_process_rule_delivers_then_dedupes():
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    engine, channels = _engine(ds, fs)
    rule = _ratio_rule()
    now = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)

    r1 = engine.process_rule(rule, now=now)
    assert r1.status == STATUS_DELIVERED, r1.detail
    assert len(fs.logs) == 1  # exactly one NotificationLog
    log = next(iter(fs.logs.values()))
    assert log.message.body == "30.0"  # {value} rendered
    assert {c.channel for c in log.channels} == {"telegram", "push"}
    assert all(c.status == "sent" for c in log.channels)

    # Second run in the same bucket -> idempotent skip (no second log).
    r2 = engine.process_rule(rule, now=now)
    assert r2.status == STATUS_DUPLICATE
    assert len(fs.logs) == 1


def test_process_rule_not_triggered_writes_nothing():
    # REQ-027.3: a non-triggered threshold rule (gte, not a cross comparator)
    # must perform ZERO Firestore writes — no log AND no rule-state update.
    ds = FakeDataSource(ratio=10.0)
    fs = FakeFirestore()
    engine, _ = _engine(ds, fs)
    rule = _ratio_rule()
    r = engine.process_rule(rule, now=datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc))
    assert r.status == STATUS_NOT_TRIGGERED
    assert len(fs.logs) == 0
    assert fs.state_updates == []


def test_delivery_isolation_partial_failure_one_log():
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    from alert_engine.evaluators import build_default_registry
    channels = {"telegram": FakeChannel("telegram", "sent"), "push": FakeChannel("push", "failed")}
    engine = AlertEngine(datasource=ds, evaluator_registry=build_default_registry(ds),
                         channel_registry=channels, firestore=fs)
    rule = _ratio_rule()
    r = engine.process_rule(rule, now=datetime(2024, 5, 2, 12, 0, tzinfo=timezone.utc))
    assert r.status == STATUS_DELIVERED
    assert len(fs.logs) == 1  # one log despite a failed channel
    statuses = {c.channel: c.status for c in next(iter(fs.logs.values())).channels}
    assert statuses == {"telegram": "sent", "push": "failed"}


def test_once_mode_disables_rule():
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    engine, _ = _engine(ds, fs)
    rule = _ratio_rule()
    rule.trigger.mode = "once"
    engine.process_rule(rule, now=datetime(2024, 5, 3, 12, 0, tzinfo=timezone.utc))
    assert any(u["enabled"] is False for u in fs.state_updates)


def test_send_test_alert_does_not_touch_state():
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    engine, _ = _engine(ds, fs)
    rule = _ratio_rule()
    log = engine.send_test_alert("u1", rule, ["telegram"], settings=fs._settings)
    assert log.isTest is True
    assert log.id in fs.logs
    assert fs.state_updates == []  # test never mutates rule state


def test_backtest_deterministic_readonly():
    rule = _ratio_rule()
    history = {"ratios": {"SPY/SCHD": {
        "2024-05-01": 20.0, "2024-05-02": 26.0, "2024-05-03": 24.0, "2024-05-04": 30.0,
    }}}
    report = backtest_rule(rule, history, ("2024-05-01", "2024-05-04"))
    assert report.fire_count == 2  # 26 and 30 are >= 25
    # Deterministic: same inputs -> same result.
    report2 = backtest_rule(rule, history, ("2024-05-01", "2024-05-04"))
    assert [d.triggered for d in report.days] == [d.triggered for d in report2.days]


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"PASS {fn.__name__}")
    print(f"\nALL {passed} SMOKE TESTS PASSED")


if __name__ == "__main__":
    _run_all()
