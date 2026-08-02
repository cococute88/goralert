"""Dry-run-first audit and explicit recovery preparation for scheduled alerts.

This command never sends a notification and never changes history. The only
write mode moves one named rule's scheduler cursor back to one explicitly named
occurrence after checking that no occurrence/log document already exists.
Normal ``alert_engine.main`` then performs the durable claim and delivery.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from .event import make_event_id
from .firestore_client import (
    ALERT_RULES,
    DURABLE_SCHEDULER_VERSION,
    LEGACY_BACKLOG_POLICY,
    NOTIFICATION_LOGS,
    get_db,
)
from .models import AlertRule
from .recurrence import (
    as_utc,
    first_future_scheduled_occurrence,
    get_tz,
    parse_datetime,
    scheduled_occurrence,
)


def _parse_recovery_occurrence(rule: AlertRule, scheduled_for: str) -> datetime:
    """Validate an explicit local occurrence against the rule's exact cadence."""
    recurrence = rule.trigger.recurrence
    if recurrence is None:
        raise RuntimeError("rule has no recurrence")
    try:
        raw = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("--scheduled-for must be an ISO datetime with offset") from exc
    if raw.tzinfo is None or raw.utcoffset() is None:
        raise RuntimeError("--scheduled-for must be an ISO datetime with offset")
    local = raw.astimezone(get_tz(recurrence.tz))
    if raw.replace(tzinfo=None) != local.replace(tzinfo=None) or raw.utcoffset() != local.utcoffset():
        raise RuntimeError("--scheduled-for must use the rule timezone and its valid UTC offset")
    expected = scheduled_occurrence(recurrence, local)
    if expected is None or as_utc(expected) != as_utc(local):
        raise RuntimeError("--scheduled-for is not an occurrence of the current rule schedule")
    return local


def _rule_rows(uid: Optional[str]) -> Iterable[tuple[str, AlertRule]]:
    db = get_db()
    if uid:
        snaps = db.collection("users").document(uid).collection(ALERT_RULES).stream()
    else:
        snaps = db.collection_group(ALERT_RULES).stream()
    for snap in snaps:
        data = snap.to_dict() or {}
        row_uid = uid
        if not row_uid:
            parts = snap.reference.path.split("/")
            row_uid = parts[1] if len(parts) >= 2 and parts[0] == "users" else ""
        rule = AlertRule.from_dict({"id": snap.id, "uid": row_uid, **data})
        if row_uid:
            yield row_uid, rule


def audit_rule(uid: str, rule: AlertRule, now: datetime) -> Optional[Dict[str, Any]]:
    recurrence = rule.trigger.recurrence
    if recurrence is None:
        return None
    timezone_name = recurrence.tz or "Asia/Seoul"
    cursor = parse_datetime(rule.nextScheduledAt, timezone_name)
    migration = rule.schedulerMigration if isinstance(rule.schedulerMigration, dict) else None
    recovery = rule.schedulerRecovery if isinstance(rule.schedulerRecovery, dict) else None
    base = {
        "uid": uid,
        "ruleId": rule.id,
        "ruleName": rule.name,
        "enabled": rule.enabled,
        "timezone": timezone_name,
        "durableSchedulerVersion": rule.durableSchedulerVersion,
    }

    if rule.nextScheduledAt is None:
        is_legacy = (
            rule.durableSchedulerVersion is None
            and migration is None
            and recovery is None
            and rule.scheduleStatus != "recovery_requested"
        )
        if is_legacy:
            future = first_future_scheduled_occurrence(recurrence, now)
            return {
                **base,
                "classification": "legacy_cursor_uninitialized",
                "backlogPolicy": LEGACY_BACKLOG_POLICY,
                "backlogAutomaticDelivery": "will_be_skipped",
                "proposedNextFutureOccurrence": as_utc(future).isoformat() if future else None,
            }
        return {
            **base,
            "classification": "corrupt_durable_scheduler",
            "scheduleStatus": rule.scheduleStatus,
            "error": "durable scheduler version or metadata exists but nextScheduledAt is missing",
        }

    if cursor is None:
        return {
            **base,
            "classification": "corrupt_durable_scheduler",
            "scheduleStatus": rule.scheduleStatus,
            "error": "nextScheduledAt is present but is not a valid timestamp",
        }

    cursor_utc = as_utc(cursor).isoformat()
    migration_fields: Dict[str, Any] = {}
    if migration is not None:
        migration_fields = {
            "migrationKind": migration.get("kind"),
            "migrationPerformedAt": (
                as_utc(parsed).isoformat()
                if (parsed := parse_datetime(migration.get("migratedAt"), "UTC"))
                else None
            ),
            "backlogPolicy": migration.get("backlogPolicy"),
            "backlogAutomaticDelivery": (
                "skipped" if migration.get("backlogPolicy") == LEGACY_BACKLOG_POLICY else "unknown"
            ),
            "backlogSkippedThrough": (
                as_utc(parsed).isoformat()
                if (parsed := parse_datetime(migration.get("backlogSkippedThrough"), "UTC"))
                else None
            ),
            "initializedNextScheduledAt": (
                as_utc(parsed).isoformat()
                if (parsed := parse_datetime(migration.get("initializedNextScheduledAt"), "UTC"))
                else None
            ),
        }

    if recovery is not None and recovery.get("status") in {"requested", "processing"}:
        requested = parse_datetime(recovery.get("scheduledFor"), timezone_name)
        return {
            **base,
            **migration_fields,
            "classification": "explicit_recovery_requested",
            "scheduleStatus": rule.scheduleStatus,
            "recoveryStatus": recovery.get("status"),
            "recoveryOccurrenceId": recovery.get("occurrenceId"),
            "scheduledFor": as_utc(requested).isoformat() if requested else None,
            "nextScheduledAt": cursor_utc,
        }

    if cursor > now.astimezone(cursor.tzinfo):
        return {
            **base,
            **migration_fields,
            "classification": (
                "legacy_cursor_initialized" if migration is not None else "durable_rule_healthy"
            ),
            "scheduleStatus": rule.scheduleStatus,
            "nextFutureOccurrence": cursor_utc,
        }

    candidate = cursor

    event_id = make_event_id(rule.id, candidate.isoformat())
    db = get_db()
    snap = db.collection("users").document(uid).collection(NOTIFICATION_LOGS).document(event_id).get()
    if snap.exists:
        data = snap.to_dict() or {}
        return {
            **base,
            **migration_fields,
            "occurrenceId": event_id,
            "scheduledFor": as_utc(candidate).isoformat(),
            "classification": "recorded_occurrence",
            "status": data.get("status") or ("processing" if data.get("pending") else "legacy"),
            "channelStatuses": [
                {"channel": item.get("channel"), "status": item.get("status")}
                for item in (data.get("channels") or [])
            ],
        }
    return {
        **base,
        **migration_fields,
        "occurrenceId": event_id,
        "scheduledFor": as_utc(candidate).isoformat(),
        "classification": "overdue_cursor",
        "nextScheduledAt": cursor_utc,
        "risk": "delivery may have occurred outside Firestore; verify provider logs before recovery",
    }


def _prepare_recovery(uid: str, rule_id: str, scheduled_for: str, acknowledged: bool) -> Dict[str, Any]:
    if not acknowledged:
        raise SystemExit("--apply-reset-cursor requires --acknowledge-duplicate-risk")
    from firebase_admin import firestore

    db = get_db()
    requested_at = datetime.now(timezone.utc)
    rule_ref = db.collection("users").document(uid).collection(ALERT_RULES).document(rule_id)
    transaction = db.transaction()

    @firestore.transactional
    def prepare(transaction):
        snap = rule_ref.get(transaction=transaction)
        if not snap.exists:
            raise RuntimeError(f"rule not found: {uid}/{rule_id}")
        rule = AlertRule.from_dict({"id": rule_id, "uid": uid, **(snap.to_dict() or {})})
        recurrence = rule.trigger.recurrence
        if recurrence is None:
            raise RuntimeError("rule has no recurrence")
        if not rule.enabled:
            raise RuntimeError("refusing recovery: rule is disabled")
        parsed = _parse_recovery_occurrence(rule, scheduled_for)
        if as_utc(parsed) >= requested_at:
            raise RuntimeError("refusing recovery: scheduled occurrence is not in the past")
        occurrence_id = make_event_id(rule_id, parsed.isoformat())
        log_col = db.collection("users").document(uid).collection(NOTIFICATION_LOGS)
        log_ref = log_col.document(occurrence_id)
        log_snap = log_ref.get(transaction=transaction)
        if log_snap.exists:
            raise RuntimeError(f"refusing recovery: occurrence already exists: {occurrence_id}")
        # Legacy history could use a random document ID while keeping eventId
        # in the payload. A direct document read alone would miss it and could
        # authorize a duplicate delivery.
        from google.cloud.firestore_v1 import FieldFilter

        legacy_query = log_col.where(filter=FieldFilter("eventId", "==", occurrence_id)).limit(1)
        if list(legacy_query.stream(transaction=transaction)):
            raise RuntimeError(f"refusing recovery: legacy occurrence log already exists: {occurrence_id}")
        current = parse_datetime(rule.nextScheduledAt, recurrence.tz)
        if current is not None and parsed >= current:
            raise RuntimeError("refusing recovery: scheduled occurrence is not earlier than current cursor")
        if rule.scheduleStatus == "processing":
            raise RuntimeError("refusing recovery: rule has an occurrence in processing")
        transaction.set(rule_ref, {
            "nextScheduledAt": as_utc(parsed),
            "durableSchedulerVersion": DURABLE_SCHEDULER_VERSION,
            "scheduleStatus": "recovery_requested",
            "schedulerRecovery": {
                "status": "requested",
                "requestedAt": firestore.SERVER_TIMESTAMP,
                "scheduledFor": as_utc(parsed),
                "occurrenceId": occurrence_id,
                "duplicateRiskAcknowledged": True,
                "requestedBy": "operator_audit",
                "previousNextScheduledAt": as_utc(current) if current else None,
            },
            "schedulerError": firestore.DELETE_FIELD,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        return parsed, occurrence_id

    try:
        parsed, occurrence_id = prepare(transaction)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    return {
        "changed": True,
        "uid": uid,
        "ruleId": rule_id,
        "occurrenceId": occurrence_id,
        "nextScheduledAt": as_utc(parsed).isoformat(),
        "sent": False,
        "nextStep": f"python -m alert_engine.main --uid {uid} --job-scope all",
    }


def _duplicate_history_rows(uid: Optional[str]) -> list[Dict[str, Any]]:
    """Detect duplicate eventIds even when legacy documents used random IDs."""
    db = get_db()
    if uid:
        snaps = db.collection("users").document(uid).collection(NOTIFICATION_LOGS).stream()
    else:
        snaps = db.collection_group(NOTIFICATION_LOGS).stream()
    grouped: Dict[tuple[str, str], list[str]] = {}
    for snap in snaps:
        data = snap.to_dict() or {}
        event_id = data.get("eventId")
        if not isinstance(event_id, str) or not event_id:
            continue
        row_uid = uid
        if not row_uid:
            parts = snap.reference.path.split("/")
            row_uid = parts[1] if len(parts) >= 2 and parts[0] == "users" else ""
        grouped.setdefault((row_uid or "", event_id), []).append(snap.id)
    return [
        {
            "uid": row_uid,
            "occurrenceId": event_id,
            "classification": "duplicate_occurrence_record",
            "documentIds": document_ids,
            "count": len(document_ids),
        }
        for (row_uid, event_id), document_ids in grouped.items()
        if len(document_ids) > 1
    ]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Audit missing/duplicate scheduled occurrences (dry-run by default)")
    parser.add_argument("--uid")
    parser.add_argument("--rule-id")
    parser.add_argument("--name")
    parser.add_argument("--now", help="fixed ISO instant for deterministic audit")
    parser.add_argument("--apply-reset-cursor", action="store_true")
    parser.add_argument("--scheduled-for", help="exact local ISO occurrence, e.g. 2026-07-31T12:15:00+09:00")
    parser.add_argument("--acknowledge-duplicate-risk", action="store_true")
    return parser.parse_args(argv)


def run(argv=None) -> int:
    args = parse_args(argv)
    if args.apply_reset_cursor:
        if not args.uid or not args.rule_id or not args.scheduled_for:
            raise SystemExit("recovery requires --uid, --rule-id, and --scheduled-for")
        print(json.dumps(_prepare_recovery(
            args.uid, args.rule_id, args.scheduled_for, args.acknowledge_duplicate_risk,
        ), ensure_ascii=False, indent=2))
        return 0

    now = parse_datetime(args.now, "UTC") if args.now else datetime.now(timezone.utc)
    assert now is not None
    rows = []
    for uid, rule in _rule_rows(args.uid):
        if args.rule_id and rule.id != args.rule_id:
            continue
        if args.name and args.name not in rule.name:
            continue
        try:
            result = audit_rule(uid, rule, now)
        except Exception as exc:  # noqa: BLE001
            result = {
                "uid": uid,
                "ruleId": rule.id,
                "ruleName": rule.name,
                "classification": "audit_error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        if result:
            rows.append(result)
    rows.extend(_duplicate_history_rows(args.uid))
    counts = Counter(row["classification"] for row in rows)
    print(json.dumps({"dryRun": True, "now": as_utc(now).isoformat(), "counts": counts, "rows": rows}, ensure_ascii=False, indent=2))
    return 1 if counts.get("audit_error") else 0


if __name__ == "__main__":
    raise SystemExit(run())
