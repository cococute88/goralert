"""Regression group (Task 33, Python side).

Maps to: REQ-035 (recurrence stability), REQ-034 (idempotency/global gate), and
REQ-048-adjacent (engine import surface stability). Guards against regressions in:
- recurrence determinism (biweekly / monthlyLastDay / monthlyFirstDay stable
  across repeated runs);
- eventId idempotency (ruleId:bucket stable for the same occurrence);
- the public engine import surface (key symbols remain importable);
- the global kill-switch (settings.globalEnabled=false suppresses ALL sends).
"""

from __future__ import annotations

import importlib
from datetime import datetime

from alert_engine.event import make_event_id
from alert_engine.models import AlertSettings, Recurrence, TriggerPolicy
from alert_engine.recurrence import bucket_time, get_tz, next_occurrence

from .conftest import FakeChannel, FakeDataSource, FakeFirestore, build_engine, make_ratio_rule

KST = get_tz("Asia/Seoul")


def _kst(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


# --- recurrence stability ----------------------------------------------------


def test_recurrence_stable_across_runs():
    cases = [
        Recurrence(kind="biweekly", weekday=6, time="08:00", tz="Asia/Seoul"),
        Recurrence(kind="monthlyLastDay", time="09:00", tz="Asia/Seoul"),
        Recurrence(kind="monthlyFirstDay", time="09:00", tz="Asia/Seoul"),
    ]
    frm = _kst(2024, 5, 15)
    for rec in cases:
        first = next_occurrence(rec, frm)
        for _ in range(5):
            assert next_occurrence(rec, frm) == first, f"{rec.kind} not stable"


def test_event_id_idempotency_preserved():
    rec = Recurrence(kind="weekly", weekday=6, time="08:00", tz="Asia/Seoul")
    trigger = TriggerPolicy(mode="recurring", recurrence=rec)
    occ = next_occurrence(rec, _kst(2024, 5, 1))
    eid1 = make_event_id("rule-1", bucket_time(occ, trigger))
    eid2 = make_event_id("rule-1", bucket_time(occ, trigger))
    assert eid1 == eid2
    assert eid1 == f"rule-1:{occ.isoformat()}"


# --- import surface ----------------------------------------------------------


def test_engine_import_surface_stable():
    engine = importlib.import_module("alert_engine.engine")
    for name in (
        "AlertEngine", "ProcessResult",
        "STATUS_DISABLED", "STATUS_NOT_DUE", "STATUS_NOT_TRIGGERED",
        "STATUS_QUIET_HOURS", "STATUS_COOLDOWN", "STATUS_DUPLICATE",
        "STATUS_DELIVERED", "STATUS_DRY_RUN", "STATUS_ERROR",
    ):
        assert hasattr(engine, name), f"engine.{name} missing"

    # Cross-module public surface relied upon by callers/tests.
    assert hasattr(importlib.import_module("alert_engine.compare"), "compare")
    assert hasattr(importlib.import_module("alert_engine.delivery"), "deliver")
    assert hasattr(importlib.import_module("alert_engine.backtest"), "backtest_rule")
    evaluators = importlib.import_module("alert_engine.evaluators")
    assert hasattr(evaluators, "build_default_registry")
    channels = importlib.import_module("alert_engine.channels")
    assert hasattr(channels, "build_default_channels")


def test_engine_methods_present():
    from alert_engine.engine import AlertEngine
    assert callable(getattr(AlertEngine, "process_rule", None))
    assert callable(getattr(AlertEngine, "send_test_alert", None))


# --- global kill-switch ------------------------------------------------------


def test_global_disabled_suppresses_all_sends():
    ds = FakeDataSource(ratio=30.0)  # would trigger if enabled
    fs = FakeFirestore()
    channels = {"telegram": FakeChannel("telegram"), "push": FakeChannel("push")}
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule()
    settings = AlertSettings(globalEnabled=False, telegramChatId="c", pushTokens=["t"])

    from alert_engine.engine import STATUS_DISABLED
    r = engine.process_rule(rule, now=_kst(2024, 5, 1, 12, 0), settings=settings)

    assert r.status == STATUS_DISABLED
    assert channels["telegram"].calls == 0
    assert channels["push"].calls == 0
    assert fs.logs == {}
    assert fs.state_updates == []


def test_global_enabled_allows_send():
    ds = FakeDataSource(ratio=30.0)
    fs = FakeFirestore()
    channels = {"telegram": FakeChannel("telegram"), "push": FakeChannel("push")}
    engine = build_engine(ds, fs, channels)
    rule = make_ratio_rule()
    settings = AlertSettings(globalEnabled=True, telegramChatId="c", pushTokens=["t"])

    from alert_engine.engine import STATUS_DELIVERED
    r = engine.process_rule(rule, now=_kst(2024, 5, 1, 12, 0), settings=settings)
    assert r.status == STATUS_DELIVERED
    assert len(fs.logs) == 1
