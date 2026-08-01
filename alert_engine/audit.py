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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

from .event import make_event_id
from .firestore_client import ALERT_RULES, NOTIFICATION_LOGS, get_db
from .models import AlertRule
from .recurrence import (
    as_utc,
    due_occurrence,
    get_tz,
    next_scheduled_occurrence,
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


def _legacy_anchor(rule: AlertRule, timezone_name: str) -> Any:
    last_processed = parse_datetime(rule.lastProcessedScheduledAt, timezone_name)
    if last_processed:
        return last_processed + timedelta(microseconds=1)
    last_triggered = parse_datetime(rule.lastTriggeredAt, timezone_name)
    if last_triggered:
        return last_triggered + timedelta(microseconds=1)
    return rule.createdAt


def _previous_occurrence(rule: AlertRule, cursor: datetime) -> Optional[datetime]:
    recurrence = rule.trigger.recurrence
    if recurrence is None:
        return None
    lookback_days = {
        "calendar": 2,
        "weekly": 8,
        "biweekly": 15,
        "monthlyFirstDay": 40,
        "monthlyLastDay": 40,
    }.get(recurrence.kind, 400)
    candidate = scheduled_occurrence(recurrence, cursor - timedelta(days=lookback_days))
    previous = None
    for _ in range(400):
        if candidate is None or candidate >= cursor:
            return previous
        previous = candidate
        candidate = next_scheduled_occurrence(recurrence, candidate)
    return previous


def audit_rule(uid: str, rule: AlertRule, now: datetime) -> Optional[Dict[str, Any]]:
    recurrence = rule.trigger.recurrence
    if recurrence is None:
        return None
    timezone_name = recurrence.tz or "Asia/Seoul"
    cursor = parse_datetime(rule.nextScheduledAt, timezone_name)
    if cursor is None:
        candidate = due_occurrence(
            recurrence,
            now,
            anchor=_legacy_anchor(rule, timezone_name),
        )
    else:
        candidate = cursor if cursor <= now.astimezone(cursor.tzinfo) else _previous_occurrence(rule, cursor)
    if candidate is None or candidate > now.astimezone(candidate.tzinfo):
        return None

    event_id = make_event_id(rule.id, candidate.isoformat())
    db = get_db()
    snap = db.collection("users").document(uid).collection(NOTIFICATION_LOGS).document(event_id).get()
    if snap.exists:
        data = snap.to_dict() or {}
        return {
            "uid": uid,
            "ruleId": rule.id,
            "ruleName": rule.name,
            "occurrenceId": event_id,
            "scheduledFor": as_utc(candidate).isoformat(),
            "timezone": timezone_name,
            "classification": "recorded",
            "status": data.get("status") or ("processing" if data.get("pending") else "legacy"),
            "channelStatuses": [
                {"channel": item.get("channel"), "status": item.get("status")}
                for item in (data.get("channels") or [])
            ],
        }
    return {
        "uid": uid,
        "ruleId": rule.id,
        "ruleName": rule.name,
        "occurrenceId": event_id,
        "scheduledFor": as_utc(candidate).isoformat(),
        "timezone": timezone_name,
        "classification": "missing_occurrence_record",
        "enabled": rule.enabled,
        "nextScheduledAt": as_utc(cursor).isoformat() if cursor else None,
        "risk": "delivery may have occurred outside Firestore; verify provider logs before recovery",
    }


def _prepare_recovery(uid: str, rule_id: str, scheduled_for: str, acknowledged: bool) -> Dict[str, Any]:
    if not acknowledged:
        raise SystemExit("--apply-reset-cursor requires --acknowledge-duplicate-risk")
    from firebase_admin import firestore

    db = get_db()
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
            "scheduleStatus": "recovery_requested",
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
