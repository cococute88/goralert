"""Property 8 — 테스트 발송 무영향 (test send has no side effects on rule state).

Maps to: REQ-022.2 / REQ-022.3. ``send_test_alert`` writes an isTest
NotificationLog but must NOT mutate the rule's ``lastTriggeredAt`` or
``enabled`` — a test is a no-op on durable rule state (mirrors the web
MockAlertProvider.sendTest behavior).
"""

from __future__ import annotations

from alert_engine.models import AlertSettings

from .conftest import FakeChannel, FakeDataSource, FakeFirestore, build_engine, make_ratio_rule


def _channels():
    return {"telegram": FakeChannel("telegram"), "push": FakeChannel("push")}


def test_send_test_writes_is_test_log():
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = _channels()
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule()

    log = engine.send_test_alert("u1", rule, ["telegram"], settings=fs._settings)

    assert log.isTest is True
    assert log.id in fs.logs
    assert fs.logs[log.id].isTest is True


def test_send_test_does_not_mutate_rule_state():
    """Property 8: no lastTriggeredAt / enabled mutation from a test send."""
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = _channels()
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule()
    original_enabled = rule.enabled
    original_last = rule.lastTriggeredAt

    engine.send_test_alert("u1", rule, ["telegram", "push"], settings=fs._settings)

    # No rule-state writes whatsoever.
    assert fs.state_updates == []
    # In-memory rule object is untouched too.
    assert rule.enabled == original_enabled
    assert rule.lastTriggeredAt == original_last


def test_send_test_is_isolated_from_process_state():
    """A test send before a real evaluation must not perturb the real fire."""
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = _channels()
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule(cooldown_minutes=60)

    engine.send_test_alert("u1", rule, ["telegram"], settings=fs._settings)
    # Test did not set lastTriggeredAt, so a real evaluation is free to fire.
    assert rule.lastTriggeredAt is None
    assert fs.state_updates == []


def test_send_test_failed_channels_still_no_state_change():
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = {"telegram": FakeChannel("telegram", status="failed")}
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule(channels=["telegram"])
    # Credentials ARE present (chatId) so the test is performed; the channel
    # itself reports failed. State must still be untouched.
    settings = AlertSettings(globalEnabled=True, telegramChatId="chat-123")

    log = engine.send_test_alert("u1", rule, ["telegram"], settings=settings)

    assert log is not None
    assert log.isTest is True
    assert fs.state_updates == []


def test_send_test_not_performed_without_credentials():
    """REQ-022.4: no chatId / no push tokens -> test send is NOT performed."""
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = {"telegram": FakeChannel("telegram"), "push": FakeChannel("push")}
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule(channels=["telegram", "push"])
    settings = AlertSettings()  # no telegramChatId, no pushTokens

    log = engine.send_test_alert("u1", rule, ["telegram", "push"], settings=settings)

    assert log is None  # not performed
    assert fs.logs == {}  # no log written
    assert fs.state_updates == []
    assert channels["telegram"].calls == 0
    assert channels["push"].calls == 0
