"""Property 2 — 전송 격리 (delivery isolation) + Property 3 — 로그 보존 (log preservation).

Maps to:
- Property 2 / REQ-026.1 / REQ-029.5: one channel failing (or raising) must not
  block the others. Each requested channel is attempted exactly once (retries
  are disabled in conftest), even when a sibling channel throws.
- Property 3 / REQ-007.5: regardless of partial failure, the engine writes
  EXACTLY ONE NotificationLog whose ``channels`` length equals the rule's
  ``delivery.channels`` length (one result per requested channel).
"""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import given
from hypothesis import strategies as st

from alert_engine.delivery import deliver
from alert_engine.engine import STATUS_DELIVERED
from alert_engine.models import AlertSettings, MessageTemplate

from .conftest import FakeChannel, FakeDataSource, FakeFirestore, build_engine, make_ratio_rule

_MSG = MessageTemplate("t", "b")
_SETTINGS = AlertSettings(globalEnabled=True, telegramChatId="c", pushTokens=["x"])


def test_failing_channel_does_not_block_others():
    """Property 2: a failed channel still lets the others run exactly once."""
    a = FakeChannel("a", status="failed")
    b = FakeChannel("b", status="sent")
    c = FakeChannel("c", status="sent")
    registry = {"a": a, "b": b, "c": c}

    outcome = deliver(_MSG, ["a", "b", "c"], registry, _SETTINGS, max_retries=0, sleep_fn=lambda _: None)

    assert a.calls == 1 and b.calls == 1 and c.calls == 1
    statuses = {r.channel: r.status for r in outcome.results}
    assert statuses == {"a": "failed", "b": "sent", "c": "sent"}
    assert outcome.any_sent is True


def test_raising_channel_is_isolated():
    """Property 2: a channel that raises is caught; siblings still get one call."""
    boom = FakeChannel("boom", raises=True)
    ok = FakeChannel("ok", status="sent")
    registry = {"boom": boom, "ok": ok}

    outcome = deliver(_MSG, ["boom", "ok"], registry, _SETTINGS, max_retries=0, sleep_fn=lambda _: None)

    assert boom.calls == 1 and ok.calls == 1
    statuses = {r.channel: r.status for r in outcome.results}
    assert statuses["boom"] == "failed"
    assert statuses["ok"] == "sent"


@given(fail_index=st.integers(min_value=0, max_value=2), raises=st.booleans())
def test_one_bad_channel_others_called_exactly_once(fail_index, raises):
    """Property 2 (fuzzed): whichever channel is bad, the others run exactly once."""
    names = ["telegram", "push", "extra"]
    channels = {}
    for i, name in enumerate(names):
        if i == fail_index:
            channels[name] = FakeChannel(name, status="failed", raises=raises)
        else:
            channels[name] = FakeChannel(name, status="sent")

    outcome = deliver(_MSG, names, channels, _SETTINGS, max_retries=0, sleep_fn=lambda _: None)

    for i, name in enumerate(names):
        assert channels[name].calls == 1, f"{name} should be attempted exactly once"
        result = next(r for r in outcome.results if r.channel == name)
        assert result.status == ("failed" if i == fail_index else "sent")
    # One result per requested channel.
    assert len(outcome.results) == len(names)


def test_unknown_channel_yields_failed_result_not_exception():
    """Property 2: an unknown channel name fails gracefully, others still run."""
    ok = FakeChannel("telegram", status="sent")
    outcome = deliver(_MSG, ["telegram", "ghost"], {"telegram": ok}, _SETTINGS,
                      max_retries=0, sleep_fn=lambda _: None)
    assert ok.calls == 1
    statuses = {r.channel: r.status for r in outcome.results}
    assert statuses["telegram"] == "sent"
    assert statuses["ghost"] == "failed"


def test_partial_failure_writes_exactly_one_log_with_all_channels():
    """Property 3: partial failure -> one log, channels length == requested length."""
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = {"telegram": FakeChannel("telegram", status="sent"),
                "push": FakeChannel("push", status="failed")}
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule(channels=["telegram", "push"])

    r = engine.process_rule(rule, now=datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc))

    assert r.status == STATUS_DELIVERED
    assert len(fs.logs) == 1  # Property 3: exactly one NotificationLog
    log = next(iter(fs.logs.values()))
    # One ChannelResult per requested delivery channel.
    assert len(log.channels) == len(rule.delivery.channels) == 2
    statuses = {c.channel: c.status for c in log.channels}
    assert statuses == {"telegram": "sent", "push": "failed"}


def test_all_channels_fail_still_one_log():
    """Property 3: even with every channel failing, exactly one log is written."""
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = {"telegram": FakeChannel("telegram", status="failed"),
                "push": FakeChannel("push", status="failed")}
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule(channels=["telegram", "push"])

    engine.process_rule(rule, now=datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc))

    assert len(fs.logs) == 1
    log = next(iter(fs.logs.values()))
    assert len(log.channels) == 2
    assert all(c.status == "failed" for c in log.channels)
