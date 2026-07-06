"""REQ-027.3 — non-triggered rules perform NO Firestore writes.

The engine must not write to Firestore when a rule is evaluated but not
triggered (saves free-tier write quota on every poll). The ONLY allowed
exception is crossUp/crossDown comparators, which must persist ``lastValue`` so
a future crossing can be detected.

Maps to: REQ-027.3 (no writes for non-triggered rules) + REQ-006.5/REQ-034.3
(cross comparators use prev).
"""

from __future__ import annotations

from datetime import datetime, timezone

from alert_engine.engine import STATUS_NOT_TRIGGERED

from .conftest import FakeChannel, FakeDataSource, FakeFirestore, build_engine, make_metric_rule


def _channels():
    return {"telegram": FakeChannel("telegram"), "push": FakeChannel("push")}


def _now():
    return datetime(2024, 5, 1, 3, 0, tzinfo=timezone.utc)


def test_threshold_rule_not_triggered_writes_nothing():
    """gte/lt/etc. comparators: not triggered -> ZERO Firestore writes."""
    ds = FakeDataSource(metric=40.0)  # 40 >= 70 is False -> not triggered
    fs = FakeFirestore()
    channels = _channels()
    engine = build_engine(ds, fs, channels)
    rule = make_metric_rule(comparator="gte", threshold=70.0, channels=["telegram"])

    res = engine.process_rule(rule, now=_now())

    assert res.status == STATUS_NOT_TRIGGERED
    # REQ-027.3: no state writes, no logs, no sends.
    assert fs.state_updates == []
    assert fs.logs == {}
    assert channels["telegram"].calls == 0


def test_cross_comparator_not_triggered_persists_last_value():
    """crossUp/crossDown: not triggered still persists lastValue (and ONLY that)."""
    ds = FakeDataSource(metric=55.0)  # no prev -> crossUp not triggered, value=55
    fs = FakeFirestore()
    channels = _channels()
    engine = build_engine(ds, fs, channels)
    rule = make_metric_rule(comparator="crossUp", threshold=70.0, channels=["telegram"])

    res = engine.process_rule(rule, now=_now())

    assert res.status == STATUS_NOT_TRIGGERED
    # Exactly one state write carrying lastValue; no engineVersion/lastTriggeredAt noise.
    assert len(fs.state_updates) == 1
    update = fs.state_updates[0]
    assert update["last_value"] == 55.0
    assert update["last_triggered_at"] is None
    assert update["enabled"] is None
    # Still no log / no send.
    assert fs.logs == {}
    assert channels["telegram"].calls == 0
