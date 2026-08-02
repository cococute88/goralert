"""Offline safety tests for the dry-run-first recovery command."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alert_engine.audit import _parse_recovery_occurrence, audit_rule
from alert_engine.models import AlertRule


def _monthly_rule() -> AlertRule:
    return AlertRule.from_dict({
        "id": "rule",
        "uid": "u1",
        "kind": "date",
        "name": "월초 알림",
        "enabled": True,
        "condition": {"kind": "date"},
        "trigger": {
            "mode": "recurring",
            "recurrence": {"kind": "monthlyFirstDay", "time": "07:00", "tz": "Asia/Seoul"},
        },
        "delivery": {"channels": ["telegram"]},
    })


def test_recovery_occurrence_requires_explicit_rule_timezone_offset():
    rule = _monthly_rule()

    with pytest.raises(RuntimeError, match="with offset"):
        _parse_recovery_occurrence(rule, "2026-08-01T07:00:00")
    with pytest.raises(RuntimeError, match="rule timezone"):
        _parse_recovery_occurrence(rule, "2026-07-31T22:00:00+00:00")


def test_recovery_occurrence_must_match_current_schedule_exactly():
    rule = _monthly_rule()

    with pytest.raises(RuntimeError, match="not an occurrence"):
        _parse_recovery_occurrence(rule, "2026-08-02T07:00:00+09:00")
    with pytest.raises(RuntimeError, match="not an occurrence"):
        _parse_recovery_occurrence(rule, "2026-08-01T07:01:00+09:00")


def test_recovery_occurrence_normalizes_valid_local_time_to_utc():
    parsed = _parse_recovery_occurrence(
        _monthly_rule(), "2026-08-01T07:00:00+09:00",
    )

    assert parsed.astimezone(timezone.utc) == datetime(2026, 7, 31, 22, 0, tzinfo=timezone.utc)


def test_audit_distinguishes_uninitialized_legacy_and_proposes_future_only():
    rule = _monthly_rule()
    now = datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc)

    row = audit_rule("u1", rule, now)

    assert row and row["classification"] == "legacy_cursor_uninitialized"
    assert row["backlogAutomaticDelivery"] == "will_be_skipped"
    assert row["proposedNextFutureOccurrence"] == "2026-08-31T22:00:00+00:00"


def test_audit_distinguishes_completed_legacy_migration_and_skipped_backlog():
    rule = _monthly_rule()
    rule.durableSchedulerVersion = 1
    rule.nextScheduledAt = datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)
    rule.schedulerMigration = {
        "kind": "legacy_cursor_bootstrap",
        "migratedAt": datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc),
        "backlogPolicy": "skip_automatic_backlog",
        "backlogSkippedThrough": datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc),
        "initializedNextScheduledAt": datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc),
    }

    row = audit_rule("u1", rule, datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc))

    assert row and row["classification"] == "legacy_cursor_initialized"
    assert row["backlogAutomaticDelivery"] == "skipped"
    assert row["nextFutureOccurrence"] == "2026-08-31T22:00:00+00:00"


def test_audit_distinguishes_explicit_recovery_request():
    rule = _monthly_rule()
    rule.durableSchedulerVersion = 1
    rule.nextScheduledAt = datetime(2026, 7, 31, 22, 0, tzinfo=timezone.utc)
    rule.scheduleStatus = "recovery_requested"
    rule.schedulerRecovery = {
        "status": "requested",
        "scheduledFor": datetime(2026, 7, 31, 22, 0, tzinfo=timezone.utc),
    }

    row = audit_rule("u1", rule, datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc))

    assert row and row["classification"] == "explicit_recovery_requested"
    assert row["scheduledFor"] == "2026-07-31T22:00:00+00:00"


def test_audit_distinguishes_corrupt_versioned_rule_without_cursor():
    rule = _monthly_rule()
    rule.durableSchedulerVersion = 1

    row = audit_rule("u1", rule, datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc))

    assert row and row["classification"] == "corrupt_durable_scheduler"
