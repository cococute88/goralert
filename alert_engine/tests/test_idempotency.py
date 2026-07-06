"""Property 1 — 멱등성 (cooldown idempotency) + Property 9 — AlertEvent idempotency.

Maps to:
- Property 1 / REQ-007.4 / REQ-034.1: a rule already triggered within its
  cooldown window must NOT send again. Re-processing with the same
  ``lastTriggeredAt`` is a no-op.
- Property 9 / REQ-008.3 / REQ-034.2: the eventId (ruleId:bucket) is an
  idempotency key. If a NotificationLog for that eventId already exists
  (log_exists == True) the engine must not send or log a second time.
"""

from __future__ import annotations

from datetime import datetime, timezone

from alert_engine.engine import STATUS_COOLDOWN, STATUS_DELIVERED, STATUS_DUPLICATE

from .conftest import FakeChannel, FakeDataSource, FakeFirestore, build_engine, make_ratio_rule


def _channels():
    return {"telegram": FakeChannel("telegram"), "push": FakeChannel("push")}


def test_cooldown_blocks_resend_within_window():
    """Property 1: triggered once, re-processing within cooldown sends nothing."""
    ds = FakeDataSource(ratio=30.0)  # 30 >= 25 -> triggered
    fs = FakeFirestore()
    channels = _channels()
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule(cooldown_minutes=60)
    now = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)

    # First fire delivers and records lastTriggeredAt.
    r1 = engine.process_rule(rule, now=now)
    assert r1.status == STATUS_DELIVERED
    sends_after_first = channels["telegram"].calls
    assert sends_after_first == 1

    # Engine updated state in the fake; mirror it onto the rule (the engine is
    # stateless — durable state lives in Firestore).
    rule.lastTriggeredAt = r1.log.firedAt

    # Re-process 5 minutes later, still inside the 60-minute cooldown.
    later = now.replace(minute=5)
    r2 = engine.process_rule(rule, now=later)
    assert r2.status == STATUS_COOLDOWN
    # No additional send, no additional log.
    assert channels["telegram"].calls == sends_after_first
    assert len(fs.logs) == 1


def test_cooldown_idempotent_across_repeated_calls():
    """Property 1: repeated processing with the same lastTriggeredAt stays a no-op."""
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = _channels()
    engine = build_engine(ds, fs, channels)
    base = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)
    rule = make_ratio_rule(cooldown_minutes=120, last_triggered_at=base.isoformat())

    for offset in (1, 2, 3):
        res = engine.process_rule(rule, now=base.replace(minute=offset))
        assert res.status == STATUS_COOLDOWN
    assert channels["telegram"].calls == 0
    assert channels["push"].calls == 0
    assert fs.logs == {}


def test_event_id_idempotency_via_log_exists():
    """Property 9: same eventId (log already exists) -> no extra send/log."""
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = _channels()
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule(cooldown_minutes=None)  # no cooldown so we reach the log_exists gate
    now = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)

    r1 = engine.process_rule(rule, now=now)
    assert r1.status == STATUS_DELIVERED
    assert len(fs.logs) == 1
    first_event_id = r1.event_id
    sends = channels["telegram"].calls

    # Same now -> same bucket -> same eventId -> log_exists True -> duplicate.
    r2 = engine.process_rule(rule, now=now)
    assert r2.status == STATUS_DUPLICATE
    assert r2.event_id == first_event_id
    assert len(fs.logs) == 1  # no second log
    assert channels["telegram"].calls == sends  # no second send
    assert channels["push"].calls == sends


def test_event_id_stable_within_eval_window():
    """Property 9 corollary: eventId is stable across the eval window bucket."""
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = _channels()
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule(cooldown_minutes=None)

    # Two instants in the same 15-minute window -> same bucket/eventId -> dedup.
    t0 = datetime(2024, 5, 1, 12, 1, tzinfo=timezone.utc)
    t1 = datetime(2024, 5, 1, 12, 14, tzinfo=timezone.utc)
    r1 = engine.process_rule(rule, now=t0)
    r2 = engine.process_rule(rule, now=t1)
    assert r1.status == STATUS_DELIVERED
    assert r2.status == STATUS_DUPLICATE
    assert r1.event_id == r2.event_id
    assert len(fs.logs) == 1
