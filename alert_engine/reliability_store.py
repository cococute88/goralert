"""Transactional operations; imported by firestore_client as its public surface."""
from datetime import datetime, timedelta, timezone
import random
import time


def _commit_with_contention(db, operation, reconcile):
    """Retry only exhausted contention. Reconciliation never grants ownership."""
    from google.api_core.exceptions import Aborted
    for attempt in range(3):
        try:
            return operation(db.transaction())
        except (ValueError, Aborted) as exc:
            if isinstance(exc, ValueError) and "Failed to commit transaction" not in str(exc):
                raise
            result = reconcile()
            if result is not None:
                return result
            if attempt == 2:
                raise
            time.sleep(random.uniform(0.05, 0.3) * (attempt + 1))


def hold_scheduler_backlog(rule, plan, resume_event_id, now):
    from firebase_admin import firestore
    from . import firestore_client as store
    from .recurrence import as_utc, parse_datetime

    db = store.get_db()
    user = db.collection("users").document(rule.uid)
    rule_ref = user.collection(store.ALERT_RULES).document(rule.id)
    hold_id = f"{rule.id}:backlog:{plan['fromInclusive']}"
    hold_ref = user.collection(store.NOTIFICATION_LOGS).document(hold_id)
    resume_ref = user.collection(store.NOTIFICATION_LOGS).document(resume_event_id) if resume_event_id else None

    @firestore.transactional
    def hold(tx):
        row = rule_ref.get(transaction=tx)
        previous = hold_ref.get(transaction=tx)
        resume = resume_ref.get(transaction=tx) if resume_ref else None
        data = row.to_dict() or {}
        if not row.exists or data.get("enabled") is not True:
            return "inactive"
        if previous.exists:
            return "already_held"
        if (not store._same_persisted_instant(data.get("nextScheduledAt"), rule.nextScheduledAt)
                or not store._same_persisted_instant(data.get("scheduleChangedAt"), rule.scheduleChangedAt)
                or data.get("lastOccurrenceId") != rule.lastOccurrenceId
                or data.get("scheduleStatus") != rule.scheduleStatus):
            return "schedule_changed"
        resume_data = resume.to_dict() or {} if resume else {}
        lease = parse_datetime(resume_data.get("leaseExpiresAt"), "UTC")
        if lease and as_utc(lease) > as_utc(now):
            return "in_progress"
        # Keep a permanent interval record plus the exact schedule snapshot.
        # Existing success/failure history is never replaced by a hold record.
        tx.create(hold_ref, {
            "id": hold_id, "eventId": hold_id, "ruleId": rule.id,
            "kind": rule.kind, "ruleName": rule.name, "isTest": False,
            "status": "recovery_held", "pending": False,
            "firedAt": plan["detectedAt"], "evaluatedAt": plan["detectedAt"],
            "scheduledFor": plan["fromInclusive"],
            "message": {"title": "오래된 예약 보류", "body": "운영자 검토 전에는 발송하지 않습니다."},
            "channels": [], "failureCode": "outage_backlog_held",
            "failureReason": "오래된 예약 범위를 보존했습니다. 운영자 검토 전에는 자동 발송하지 않습니다.",
            "backlog": plan, "triggerSnapshot": rule.trigger.to_dict(),
            "previousNextScheduledAt": data.get("nextScheduledAt"),
            "previousScheduleStatus": data.get("scheduleStatus"),
            "previousLastOccurrenceId": data.get("lastOccurrenceId"),
            "createdAt": firestore.SERVER_TIMESTAMP,
        })
        if resume and resume.exists and resume_data.get("status") not in store.TERMINAL_OCCURRENCE_STATUSES:
            channels = list(resume_data.get("channels") or [])
            for channel in channels:
                if channel.get("status") == "sending":
                    channel.update(status="unknown", errorCode="ambiguous_delivery",
                                   error="Worker stopped after provider boundary; automatic retry withheld.")
            tx.update(resume_ref, {
                "status": "recovery_held", "pending": False, "channels": channels,
                "backlogHoldId": hold_id,
                "leaseOwner": firestore.DELETE_FIELD, "leaseExpiresAt": firestore.DELETE_FIELD,
            })
        updates = {"scheduleStatus": "recovery_held", "updatedAt": firestore.SERVER_TIMESTAMP}
        if plan["nextCursor"] is not None:
            updates["nextScheduledAt"] = plan["nextCursor"]
        tx.update(rule_ref, updates)
        return "held"

    return _commit_with_contention(db, hold, lambda: "already_held" if hold_ref.get().exists else None)


def _test_refs(uid, request_id):
    from . import firestore_client as store
    user = store.get_db().collection("users").document(uid)
    event_id = f"settings-test:request:{request_id}"
    return (user.collection(store.TEST_PUSH_REQUESTS).document(request_id),
            user.collection(store.NOTIFICATION_LOGS).document(event_id))


def claim_test_request(uid, request_id, payload, worker_id, now, lease_seconds=600):
    from firebase_admin import firestore
    from . import firestore_client as store
    from .recurrence import parse_datetime, as_utc
    request_ref, log_ref = _test_refs(uid, request_id)

    @firestore.transactional
    def claim(tx):
        request = request_ref.get(transaction=tx)
        log = log_ref.get(transaction=tx)
        req = request.to_dict() or {}
        if not request.exists or req.get("status") not in {"pending", "processing"}:
            return {"claim": "terminal"}
        data = log.to_dict() or {}
        if log.exists and data.get("status") != "processing":
            # Reconcile a previously committed result without another send.
            tx.update(request_ref, {
                "status": data.get("requestStatus", "error"),
                "results": data.get("channels", []), "logId": log_ref.id,
                "processedAt": firestore.SERVER_TIMESTAMP,
            })
            return {"claim": "terminal"}
        lease = parse_datetime(data.get("leaseExpiresAt"), "UTC")
        if lease and as_utc(lease) > as_utc(now) and data.get("leaseOwner") != worker_id:
            return {"claim": "in_progress"}
        record = data if log.exists else payload
        ownership = {"leaseOwner": worker_id, "leaseExpiresAt": now + timedelta(seconds=lease_seconds)}
        if log.exists:
            tx.update(log_ref, ownership)
        else:
            tx.create(log_ref, {**record, **ownership})
        tx.update(request_ref, {"status": "processing", "startedAt": firestore.SERVER_TIMESTAMP,
                                "logId": log_ref.id})
        return {"claim": "claimed", "record": {**record, **ownership}}

    return _commit_with_contention(store.get_db(), claim,
                                   lambda: {"claim": "in_progress"} if log_ref.get().exists else None)


def begin_test_channel_attempt(uid, request_id, channel, worker_id, now):
    from firebase_admin import firestore
    from . import firestore_client as store
    request_ref, log_ref = _test_refs(uid, request_id)

    @firestore.transactional
    def begin(tx):
        snap = log_ref.get(transaction=tx)
        data = snap.to_dict() or {}
        if data.get("leaseOwner") != worker_id or data.get("status") != "processing":
            return False
        # Expiry alone does not transfer ownership. A competing claim fences us.
        channels = list(data.get("channels") or [])
        for row in channels:
            if row.get("channel") == channel and row.get("status") == "pending":
                row.update(status="sending", attemptedAt=now.isoformat(), attemptCount=1)
                tx.update(log_ref, {"channels": channels})
                tx.update(request_ref, {"results": channels})
                return True
        return False

    return begin(store.get_db().transaction())


def finish_test_request(uid, request_id, worker_id, status, results, error=None):
    from firebase_admin import firestore
    from . import firestore_client as store
    request_ref, log_ref = _test_refs(uid, request_id)

    @firestore.transactional
    def finish(tx):
        snap = log_ref.get(transaction=tx)
        request = request_ref.get(transaction=tx)
        data = snap.to_dict() or {}
        if data.get("leaseOwner") != worker_id or data.get("status") != "processing":
            raise RuntimeError("test request lease lost")
        log_status = {"done": "sent", "partial": "partial_failure", "unknown": "delivery_unknown"}.get(status, status)
        updates = {"channels": results, "status": log_status, "requestStatus": status,
                   "completedAt": datetime.now(timezone.utc).isoformat(),
                   "leaseOwner": firestore.DELETE_FIELD, "leaseExpiresAt": firestore.DELETE_FIELD}
        if any(row.get("status") == "sent" for row in results):
            accepted_times = [row["completedAt"] for row in results if row.get("status") == "sent" and row.get("completedAt")]
            updates["sentAt"] = data.get("sentAt") or (min(accepted_times) if accepted_times else datetime.now(timezone.utc).isoformat())
        tx.update(log_ref, updates)
        if request.exists:
            tx.update(request_ref, {"status": status, "results": results,
                                    "error": error, "logId": log_ref.id,
                                    "processedAt": firestore.SERVER_TIMESTAMP})

    finish(store.get_db().transaction())
