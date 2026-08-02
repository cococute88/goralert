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
import logging
from datetime import datetime
from types import SimpleNamespace

from alert_engine.config import EngineConfig
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
        "STATUS_DELIVERED", "STATUS_PARTIAL_FAILURE", "STATUS_DELIVERY_FAILED",
        "STATUS_DELIVERY_UNKNOWN", "STATUS_DRY_RUN", "STATUS_ERROR",
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


def test_global_enabled_false_survives_firestore_model_parsing():
    assert AlertSettings.from_dict({"globalEnabled": False}).globalEnabled is False
    assert AlertSettings.from_dict({"globalEnabled": True}).globalEnabled is True
    assert AlertSettings.from_dict({}).globalEnabled is True


def test_runner_skips_all_user_rules_when_settings_read_fails(monkeypatch):
    from alert_engine import firestore_client, main

    rule = make_ratio_rule()
    processed = []

    class NeverProcessEngine:
        def __init__(self, **kwargs):
            pass

        def process_rule(self, *args, **kwargs):
            processed.append((args, kwargs))
            raise AssertionError("provider/evaluation boundary must not be reached")

    monkeypatch.setattr(main, "load_config", lambda: EngineConfig(has_inline_service_account=True))
    monkeypatch.setattr(main, "AlertEngine", NeverProcessEngine)
    monkeypatch.setattr(firestore_client, "list_enabled_rules", lambda uid=None: [rule])
    monkeypatch.setattr(
        firestore_client,
        "load_alert_settings",
        lambda uid: (_ for _ in ()).throw(RuntimeError("settings unavailable")),
    )

    assert main.run([]) == 1
    assert processed == []


def test_runner_isolates_settings_failure_and_masks_full_uid(monkeypatch, caplog):
    from alert_engine import firestore_client, main
    from alert_engine.engine import STATUS_DISABLED

    failed_uid = "firebase-uid-that-must-not-appear-in-logs"
    healthy_uid = "healthy-user"
    failed_rule = make_ratio_rule()
    failed_rule.uid = failed_uid
    healthy_rule = make_ratio_rule()
    healthy_rule.uid = healthy_uid
    processed = []

    class RecordingEngine:
        def __init__(self, **kwargs):
            pass

        def process_rule(self, rule, **kwargs):
            processed.append(rule.uid)
            return SimpleNamespace(
                status=STATUS_DISABLED,
                event_id=None,
                detail="test",
            )

    def load_settings(uid):
        if uid == failed_uid:
            raise RuntimeError("settings unavailable")
        return AlertSettings(globalEnabled=True)

    monkeypatch.setattr(main, "load_config", lambda: EngineConfig(has_inline_service_account=True))
    monkeypatch.setattr(main, "AlertEngine", RecordingEngine)
    monkeypatch.setattr(firestore_client, "list_enabled_rules", lambda uid=None: [failed_rule, healthy_rule])
    monkeypatch.setattr(firestore_client, "load_alert_settings", load_settings)

    with caplog.at_level(logging.INFO):
        assert main.run([]) == 1

    assert processed == [healthy_uid]
    assert failed_uid not in caplog.text


def test_engine_direct_settings_failure_is_fail_closed():
    class BrokenSettingsFirestore(FakeFirestore):
        def load_alert_settings(self, uid):
            raise RuntimeError("settings unavailable")

    class NeverDataSource(FakeDataSource):
        def get_ratio(self, numerator, denominator):
            raise AssertionError("provider boundary must not be reached")

    datasource = NeverDataSource(ratio=30.0)
    firestore = BrokenSettingsFirestore()
    channels = {"telegram": FakeChannel("telegram"), "push": FakeChannel("push")}
    result = build_engine(datasource, firestore, channels).process_rule(
        make_ratio_rule(), now=_kst(2024, 5, 1, 12, 0),
    )

    assert result.status == "error"
    assert result.detail == "settings_unavailable"
    assert channels["telegram"].calls == 0
    assert channels["push"].calls == 0
    assert firestore.logs == {}
    assert firestore.state_updates == []
