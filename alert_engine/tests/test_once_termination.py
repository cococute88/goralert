"""Property 7 — once 종료 (one-shot termination).

Maps to: REQ-034.4 / REQ-015.3. When ``trigger.mode == "once"`` a successful
fire must disable the rule (enabled -> false) so it never triggers again.
"""

from __future__ import annotations

from datetime import datetime, timezone

from alert_engine.engine import STATUS_DELIVERED, STATUS_DISABLED

from .conftest import FakeChannel, FakeDataSource, FakeFirestore, build_engine, make_ratio_rule


def _channels():
    return {"telegram": FakeChannel("telegram"), "push": FakeChannel("push")}


def test_once_disables_rule_on_success():
    """Property 7: once-mode success requests enabled=False on rule state."""
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = _channels()
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule(mode="once")

    r = engine.process_rule(rule, now=datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc))

    assert r.status == STATUS_DELIVERED
    # The engine asked Firestore to disable the rule.
    assert any(u["enabled"] is False for u in fs.state_updates)


def test_once_never_retriggers_after_disable():
    """Property 7: after the disable lands, the rule is inert (no re-trigger)."""
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = _channels()
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule(mode="once")
    now = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)

    engine.process_rule(rule, now=now)
    sends_after_first = channels["telegram"].calls
    assert sends_after_first == 1

    # Apply the engine-requested state change (stateless engine; state in store).
    disable_update = next(u for u in fs.state_updates if u["enabled"] is False)
    assert disable_update is not None
    rule.enabled = False

    # Next run (even in a fresh bucket) short-circuits as disabled, never sends.
    later = datetime(2024, 5, 1, 13, 0, tzinfo=timezone.utc)
    r2 = engine.process_rule(rule, now=later)
    assert r2.status == STATUS_DISABLED
    assert channels["telegram"].calls == sends_after_first
    assert len(fs.logs) == 1


def test_recurring_mode_does_not_disable():
    """Contrast: recurring mode must NOT disable the rule after firing."""
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = _channels()
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule(mode="recurring")

    engine.process_rule(rule, now=datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc))

    assert all(u["enabled"] is not False for u in fs.state_updates)
