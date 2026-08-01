"""Firestore access layer (firebase-admin).

This is the ONLY module that talks to Firestore. firebase-admin is imported
lazily so the package can be imported (and unit-tested / py_compiled) without
the dependency installed or credentials configured.

Path boundary: every read/write is scoped under ``users/{uid}/...``. The
existing calendar collections are READ-ONLY; the engine only WRITES to the new
alert collections (notificationLogs, alertRules state, calendarAlertMarks).

Collections (mirroring ``lib/alerts/collections.ts`` + calendar repos):
- users/{uid}/alertRules/{id}
- users/{uid}/notificationLogs/{id}
- users/{uid}/alertSettings/default
- users/{uid}/calendarEvents/{id}        (READ-ONLY, meta incl. star/heart)
- users/{uid}/calendarCache/{ticker}     (READ-ONLY, generated event bodies)
- users/{uid}/calendarCustomEvents/{id}  (READ-ONLY)
- users/{uid}/calendarPortfolios/{id}/... (READ-ONLY, active named portfolio)
- users/{uid}/calendarAlertMarks/{id}    (🔔 bell, Goralert-owned)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .calendar_contract import (
    DEFAULT_CALENDAR_PORTFOLIO_ID,
    join_calendar_metadata,
    normalize_authoritative_event,
    select_authoritative_events,
)
from .config import load_config, load_service_account_dict
from .models import AlertRule, AlertSettings, NotificationLog

logger = logging.getLogger("alert_engine.firestore")

ALERT_RULES = "alertRules"
NOTIFICATION_LOGS = "notificationLogs"
ALERT_SETTINGS = "alertSettings"
ALERT_SETTINGS_DOC_ID = "default"
CALENDAR_EVENTS = "calendarEvents"
CALENDAR_CUSTOM_EVENTS = "calendarCustomEvents"
CALENDAR_CACHE = "calendarCache"
CALENDAR_SETTINGS = "calendarSettings"
CALENDAR_EVENT_METAS = "calendarEventMetas"
CALENDAR_PORTFOLIOS = "calendarPortfolios"
CALENDAR_ALERT_MARKS = "calendarAlertMarks"
TEST_PUSH_REQUESTS = "testPushRequests"

_db = None  # cached Firestore client
_UNSET = object()


def _init_firebase():
    """Initialize the firebase-admin app exactly once, from config.

    Credential resolution order:
      1. inline service-account JSON (FIREBASE_SERVICE_ACCOUNT[_KEY])
      2. GOOGLE_APPLICATION_CREDENTIALS path (application-default)
    Raises RuntimeError with a clear message when neither is available.
    """
    import firebase_admin  # lazy import
    from firebase_admin import credentials

    if firebase_admin._apps:
        return firebase_admin.get_app()

    sa_dict = load_service_account_dict()
    if sa_dict is not None:
        expected_project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
        credential_project_id = str(sa_dict.get("project_id") or "").strip()
        if expected_project_id and credential_project_id and expected_project_id != credential_project_id:
            raise RuntimeError(
                "Firebase service account project does not match FIREBASE_PROJECT_ID. "
                "Use credentials from the same Firebase project as the web app."
            )
        cred = credentials.Certificate(sa_dict)
        app = firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin initialized for project=%s", credential_project_id or "<unknown>")
        return app

    cfg = load_config()
    if cfg.google_application_credentials:
        # firebase-admin picks up GOOGLE_APPLICATION_CREDENTIALS automatically
        # via ApplicationDefault.
        cred = credentials.ApplicationDefault()
        return firebase_admin.initialize_app(cred)

    raise RuntimeError(
        "No Firebase credentials configured. Set FIREBASE_SERVICE_ACCOUNT "
        "(service-account JSON) or GOOGLE_APPLICATION_CREDENTIALS (path)."
    )


def get_db():
    """Return a cached Firestore client, initializing firebase-admin on demand."""
    global _db
    if _db is not None:
        return _db
    if os.getenv("FIRESTORE_EMULATOR_HOST", "").strip():
        from google.auth.credentials import AnonymousCredentials
        from google.cloud import firestore as google_firestore

        project_id = os.getenv("FIREBASE_PROJECT_ID", "demo-goralert").strip() or "demo-goralert"
        _db = google_firestore.Client(project=project_id, credentials=AnonymousCredentials())
        logger.info("Firestore Emulator client initialized project=%s", project_id)
        return _db
    from firebase_admin import firestore  # lazy import

    _init_firebase()
    _db = firestore.client()
    return _db


def reset_client() -> None:
    """Drop the cached client (used by tests)."""
    global _db
    _db = None


# --- AlertRule reads ---------------------------------------------------------


def _enabled_filter():
    """Build an ``enabled == True`` filter using the keyword ``FieldFilter`` API.

    The positional ``where("enabled", "==", True)`` form is deprecated and emits a
    UserWarning on every run. ``FieldFilter`` is the supported form. Imported
    lazily so the module still imports without ``google-cloud-firestore``.
    """
    from google.cloud.firestore_v1 import FieldFilter  # lazy import

    return FieldFilter("enabled", "==", True)


def _is_missing_index_error(exc: Exception) -> bool:
    """True when ``exc`` is Firestore's "this query requires an index" error.

    Collection-group queries with a field filter need a COLLECTION_GROUP-scoped
    single-field index (``firestore.indexes.json`` → ``fieldOverrides``). When
    that index has not been deployed, Firestore raises ``FailedPrecondition`` /
    HTTP 400 whose message asks to create the index.
    """
    name = type(exc).__name__
    msg = str(exc).lower()
    return name == "FailedPrecondition" or ("requires" in msg and "index" in msg)


def list_enabled_rules(uid: Optional[str] = None) -> List[AlertRule]:
    """List enabled rules.

    - When ``uid`` is given: query ``users/{uid}/alertRules`` where enabled==True.
    - Otherwise: use a collection_group query across all users' ``alertRules``.

    The collection_group path requires the COLLECTION_GROUP index on ``enabled``
    declared in ``firestore.indexes.json``. A missing index is a visible
    infrastructure failure; we never hide it behind a whole-table scan. Durable
    scheduled cursors catch up after the index is deployed.
    """
    db = get_db()
    rules: List[AlertRule] = []

    if uid:
        col = db.collection("users").document(uid).collection(ALERT_RULES)
        query = col.where(filter=_enabled_filter())
        for snap in query.stream():
            data = snap.to_dict() or {}
            if data.get("enabled") is not True:
                continue
            rule = AlertRule.from_dict({"id": snap.id, **data})
            if not rule.uid:
                rule.uid = uid
            rules.append(rule)
        return rules

    # All users via the indexed collection_group query. Integrity is preserved
    # on failure because no cursor is changed until an occurrence is claimed.
    group = db.collection_group(ALERT_RULES).where(filter=_enabled_filter())
    snaps = list(group.stream())

    for snap in snaps:
        data = snap.to_dict() or {}
        # Defensive guard in case malformed data reaches the result set.
        if data.get("enabled") is not True:
            continue
        rule = AlertRule.from_dict({"id": snap.id, **data})
        if not rule.uid:
            rule.uid = _extract_uid_from_path(snap)
        if rule.uid:
            rules.append(rule)
    return rules


def _extract_uid_from_path(snap) -> str:
    """Best-effort uid extraction from a collection_group doc reference path.

    Path shape: users/{uid}/alertRules/{id}.
    """
    try:
        parts = snap.reference.path.split("/")
        if len(parts) >= 2 and parts[0] == "users":
            return parts[1]
    except Exception:
        pass
    return ""


def get_rule(uid: str, rule_id: str) -> Optional[AlertRule]:
    db = get_db()
    snap = db.collection("users").document(uid).collection(ALERT_RULES).document(rule_id).get()
    if not snap.exists:
        return None
    return AlertRule.from_dict({"id": snap.id, **(snap.to_dict() or {})})


# --- AlertSettings -----------------------------------------------------------


def load_alert_settings(uid: str) -> AlertSettings:
    """Load per-user settings, defaulting to globalEnabled=True when absent."""
    db = get_db()
    snap = (
        db.collection("users").document(uid)
        .collection(ALERT_SETTINGS).document(ALERT_SETTINGS_DOC_ID).get()
    )
    if not snap.exists:
        return AlertSettings()
    return AlertSettings.from_dict(snap.to_dict() or {})


# --- Calendar (READ-ONLY) ----------------------------------------------------


def _read_collection(collection_ref) -> List[Dict[str, Any]]:
    """Read a collection while preserving each Firestore document ID."""
    out: List[Dict[str, Any]] = []
    for snap in collection_ref.stream():
        data = snap.to_dict() or {}
        out.append({
            "id": data.get("id") or snap.id,
            "firestoreDocumentId": snap.id,
            **data,
        })
    return out


def _active_calendar_portfolio_id(user_ref) -> str:
    settings = user_ref.collection(CALENDAR_SETTINGS).document("default").get()
    if not settings.exists:
        return DEFAULT_CALENDAR_PORTFOLIO_ID
    value = (settings.to_dict() or {}).get("activePortfolioId")
    return value.strip() if isinstance(value, str) and value.strip() else DEFAULT_CALENDAR_PORTFOLIO_ID


def _calendar_scope(uid: str, portfolio_id: Optional[str] = None):
    """Return selected or active portfolio refs matching Gorani repository paths."""
    db = get_db()
    user_ref = db.collection("users").document(uid)
    portfolio_id = (
        portfolio_id.strip()
        if isinstance(portfolio_id, str) and portfolio_id.strip()
        else _active_calendar_portfolio_id(user_ref)
    )
    if portfolio_id == DEFAULT_CALENDAR_PORTFOLIO_ID:
        return {
            "portfolio_id": portfolio_id,
            "metadata": user_ref.collection(CALENDAR_EVENTS),
            "cache": user_ref.collection(CALENDAR_CACHE),
            "custom": user_ref.collection(CALENDAR_CUSTOM_EVENTS),
            "legacy": user_ref.collection(CALENDAR_EVENTS),
        }
    portfolio_ref = user_ref.collection(CALENDAR_PORTFOLIOS).document(portfolio_id)
    return {
        "portfolio_id": portfolio_id,
        "metadata": portfolio_ref.collection(CALENDAR_EVENT_METAS),
        "cache": portfolio_ref.collection(CALENDAR_CACHE),
        "custom": portfolio_ref.collection(CALENDAR_CUSTOM_EVENTS),
        "legacy": None,
    }


def read_calendar_events(uid: str, portfolio_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Resolve Gorani generated/legacy bodies and join star/heart metadata."""
    scope = _calendar_scope(uid, portfolio_id)
    metadata = _read_collection(scope["metadata"])
    cache_docs = _read_collection(scope["cache"])

    cache_events: List[Dict[str, Any]] = []
    cache_tickers = set()
    for cache_doc in cache_docs:
        fallback_ticker = str(
            cache_doc.get("ticker")
            or cache_doc.get("firestoreDocumentId")
            or cache_doc.get("id")
            or ""
        ).strip().upper()
        if fallback_ticker:
            cache_tickers.add(fallback_ticker)
        raw_events = cache_doc.get("events")
        if not isinstance(raw_events, list):
            continue
        for raw_event in raw_events:
            if not isinstance(raw_event, dict):
                continue
            normalized = normalize_authoritative_event(raw_event, fallback_ticker=fallback_ticker)
            if normalized is not None:
                cache_events.append(normalized)

    legacy_docs = _read_collection(scope["legacy"]) if scope["legacy"] is not None else []
    legacy_events = [
        event
        for event in (
            normalize_authoritative_event(doc, fallback_id=str(doc.get("id") or ""))
            for doc in legacy_docs
        )
        if event is not None
    ]
    authoritative = select_authoritative_events(cache_events, legacy_events, cache_tickers)
    joined = join_calendar_metadata(authoritative, metadata)
    logger.info(
        "calendar read success source=calendarEvents portfolio=%s metadata=%d "
        "cache_documents=%d cache_tickers=%d authoritative=%d join=%d",
        scope["portfolio_id"],
        len(metadata),
        len(cache_docs),
        len(cache_tickers),
        len(authoritative),
        len(joined),
    )
    return joined


def read_calendar_custom_events(uid: str, portfolio_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read custom event bodies from the selected or active Gorani portfolio."""
    scope = _calendar_scope(uid, portfolio_id)
    raw_events = _read_collection(scope["custom"])
    events = [
        event
        for event in (
            normalize_authoritative_event(doc, fallback_id=str(doc.get("id") or ""))
            for doc in raw_events
        )
        if event is not None
    ]
    logger.info(
        "calendar read success source=calendarCustomEvents portfolio=%s "
        "metadata=0 authoritative=%d join=%d",
        scope["portfolio_id"],
        len(raw_events),
        len(events),
    )
    return events


def read_calendar_alert_marks(uid: str) -> List[Dict[str, Any]]:
    """Read users/{uid}/calendarAlertMarks (🔔 bell marks, Goralert-owned)."""
    db = get_db()
    out: List[Dict[str, Any]] = []
    for snap in db.collection("users").document(uid).collection(CALENDAR_ALERT_MARKS).stream():
        out.append({"id": snap.id, **(snap.to_dict() or {})})
    return out


# --- NotificationLog writes + idempotency ------------------------------------

TERMINAL_OCCURRENCE_STATUSES = {
    "sent", "partial_failure", "failed", "skipped", "cancelled",
    "disabled", "delivery_unknown",
}


def get_occurrence(uid: str, event_id: str) -> Optional[Dict[str, Any]]:
    """Load one durable occurrence by its idempotency key."""
    db = get_db()
    snap = db.collection("users").document(uid).collection(NOTIFICATION_LOGS).document(event_id).get()
    return (snap.to_dict() or {}) if snap.exists else None


def claim_occurrence(
    uid: str,
    rule_id: str,
    event_id: str,
    payload: Dict[str, Any],
    next_scheduled_at: Optional[datetime],
    worker_id: str,
    now: datetime,
    lease_seconds: int = 600,
    expected_next_scheduled_at: Any = _UNSET,
    expected_schedule_changed_at: Any = _UNSET,
) -> Dict[str, Any]:
    """Create or reclaim one occurrence and advance its rule atomically.

    The transaction is the scheduler's core invariant: ``nextScheduledAt`` can
    move only in the same commit that creates the permanent occurrence record.
    Existing terminal records are duplicates; an expired processing lease can
    be reclaimed after a crash. Channel states marked ``sending`` are retained
    so a recovery worker can classify them as ambiguous without re-sending.
    """
    from firebase_admin import firestore

    db = get_db()
    log_ref = db.collection("users").document(uid).collection(NOTIFICATION_LOGS).document(event_id)
    rule_ref = db.collection("users").document(uid).collection(ALERT_RULES).document(rule_id)
    aware_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    lease_expires = aware_now.astimezone(timezone.utc) + timedelta(seconds=max(1, lease_seconds))
    scheduled_value: Any = payload.get("scheduledFor")
    if isinstance(scheduled_value, str):
        try:
            scheduled_value = datetime.fromisoformat(scheduled_value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass

    @firestore.transactional
    def claim(transaction):
        snap = log_ref.get(transaction=transaction)
        rule_snap = rule_ref.get(transaction=transaction)
        rule_data = rule_snap.to_dict() or {} if rule_snap.exists else {}
        if snap.exists:
            data = snap.to_dict() or {}
            status = str(data.get("status") or "processing")
            if status in TERMINAL_OCCURRENCE_STATUSES:
                return {"claim": "terminal", "record": data}
            if not rule_snap.exists or rule_data.get("enabled") is not True:
                reason = "rule_deleted" if not rule_snap.exists else "rule_disabled"
                transaction.set(log_ref, {
                    "status": "cancelled" if not rule_snap.exists else "disabled",
                    "pending": False,
                    "failureCode": reason,
                    "failureReason": "rule was deleted or disabled after occurrence claim",
                    "leaseOwner": firestore.DELETE_FIELD,
                    "leaseExpiresAt": firestore.DELETE_FIELD,
                    "completedAt": aware_now.astimezone(timezone.utc).isoformat(),
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                }, merge=True)
                return {"claim": "inactive", "record": data, "reason": reason}
            current_lease = data.get("leaseExpiresAt")
            if isinstance(current_lease, datetime):
                if current_lease.tzinfo is None:
                    current_lease = current_lease.replace(tzinfo=timezone.utc)
                if current_lease > aware_now.astimezone(timezone.utc) and data.get("leaseOwner") != worker_id:
                    return {"claim": "in_progress", "record": data}
            attempts = int(data.get("attemptCount") or 0) + 1
            occurrence_updates = {
                "status": "processing",
                "leaseOwner": worker_id,
                "leaseExpiresAt": lease_expires,
                "attemptCount": attempts,
                "processingStartedAt": aware_now.astimezone(timezone.utc).isoformat(),
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }
            if not data.get("scheduledFor"):
                # Upgrade an old reserve_log placeholder without deleting its
                # reservedAt evidence. Channels are intentionally not copied as
                # pending: the old worker may have crossed the provider boundary.
                occurrence_updates.update({
                    key: value for key, value in payload.items()
                    if key not in {"channels", "status", "attemptCount"}
                })
            transaction.set(log_ref, occurrence_updates, merge=True)
            rule_updates: Dict[str, Any] = {
                "lastOccurrenceId": event_id,
                "lastProcessedScheduledAt": scheduled_value,
                "scheduleStatus": "processing",
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }
            if next_scheduled_at is not None:
                rule_updates["nextScheduledAt"] = next_scheduled_at.astimezone(timezone.utc)
            transaction.set(rule_ref, rule_updates, merge=True)
            return {"claim": "claimed", "record": {**data, "attemptCount": attempts}}

        if not rule_snap.exists or rule_data.get("enabled") is not True:
            return {
                "claim": "inactive",
                "record": None,
                "reason": "rule_deleted" if not rule_snap.exists else "rule_disabled",
            }

        # The browser can edit a schedule after the worker queried the rule but
        # before this transaction begins. Never let that stale worker create an
        # occurrence from the old definition or overwrite the edited cursor.
        if (
            (expected_next_scheduled_at is not _UNSET and not _same_persisted_instant(
                rule_data.get("nextScheduledAt"), expected_next_scheduled_at,
            ))
            or (expected_schedule_changed_at is not _UNSET and not _same_persisted_instant(
                rule_data.get("scheduleChangedAt"), expected_schedule_changed_at,
            ))
        ):
            return {
                "claim": "schedule_changed",
                "record": None,
                "reason": "schedule changed before occurrence claim",
            }

        initial = {
            **payload,
            "status": "processing",
            "pending": True,
            "attemptCount": 1,
            "leaseOwner": worker_id,
            "leaseExpiresAt": lease_expires,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }
        transaction.create(log_ref, initial)
        rule_updates: Dict[str, Any] = {
            "lastOccurrenceId": event_id,
            "lastProcessedScheduledAt": scheduled_value,
            "scheduleStatus": "processing",
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }
        if next_scheduled_at is not None:
            rule_updates["nextScheduledAt"] = next_scheduled_at.astimezone(timezone.utc)
        transaction.set(rule_ref, rule_updates, merge=True)
        return {"claim": "claimed", "record": initial}

    for contention_attempt in range(3):
        transaction = db.transaction()
        try:
            return claim(transaction)
        except ValueError as exc:
            # The Firestore client raises ValueError after exhausting automatic
            # ABORTED retries. Under heavy contention the winning transaction may
            # already have created the occurrence. A read-only reconciliation must
            # never authorize delivery, but can safely report the durable owner so
            # the losing worker exits without turning a normal collision into a
            # noisy job failure.
            if "Failed to commit transaction" not in str(exc):
                raise
            reconciled = log_ref.get()
            if reconciled.exists:
                data = reconciled.to_dict() or {}
                if str(data.get("status") or "processing") in TERMINAL_OCCURRENCE_STATUSES:
                    return {"claim": "terminal", "record": data, "reconciledAfterContention": True}
                return {"claim": "in_progress", "record": data, "reconciledAfterContention": True}
            if contention_attempt == 2:
                raise
            # Both contenders can be aborted by the emulator's pessimistic lock.
            # A small deterministic worker-specific stagger lets one fresh
            # transaction commit; production contention also benefits without
            # weakening the atomic claim invariant.
            stagger = (sum(ord(char) for char in worker_id) % 13) / 100
            time.sleep(0.05 * (contention_attempt + 1) + stagger)

    raise RuntimeError("unreachable occurrence claim state")


def _same_persisted_instant(left: Any, right: Any) -> bool:
    """Compare Firestore Timestamp/datetime/ISO values without host timezone use."""
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, datetime) and isinstance(right, datetime):
        left_aware = left if left.tzinfo is not None else left.replace(tzinfo=timezone.utc)
        right_aware = right if right.tzinfo is not None else right.replace(tzinfo=timezone.utc)
        return left_aware.astimezone(timezone.utc) == right_aware.astimezone(timezone.utc)
    return left == right


def begin_channel_attempt(
    uid: str,
    rule_id: str,
    event_id: str,
    channel: str,
    worker_id: str,
    attempted_at: datetime,
) -> str:
    """Atomically verify rule/lease state and mark one channel as sending.

    The returned value is ``began`` or a reason that forbids provider I/O:
    ``rule_disabled``, ``rule_deleted``, ``lease_lost``, or ``not_pending``.
    """
    from firebase_admin import firestore

    db = get_db()
    ref = db.collection("users").document(uid).collection(NOTIFICATION_LOGS).document(event_id)
    rule_ref = db.collection("users").document(uid).collection(ALERT_RULES).document(rule_id)
    transaction = db.transaction()

    @firestore.transactional
    def begin(transaction):
        snap = ref.get(transaction=transaction)
        rule_snap = rule_ref.get(transaction=transaction)
        if not snap.exists:
            return "lease_lost"
        data = snap.to_dict() or {}
        if data.get("leaseOwner") != worker_id or data.get("status") in TERMINAL_OCCURRENCE_STATUSES:
            return "lease_lost"
        results = list(data.get("channels") or [])
        if not rule_snap.exists or (rule_snap.to_dict() or {}).get("enabled") is not True:
            reason = "rule_deleted" if not rule_snap.exists else "rule_disabled"
            completed_at = attempted_at.astimezone(timezone.utc).isoformat()
            for result in results:
                if result.get("status") == "pending":
                    result.update({
                        "status": "failed",
                        "error": "delivery cancelled because the rule became inactive before provider I/O",
                        "errorCode": reason,
                        "completedAt": completed_at,
                    })
            statuses = {str(result.get("status")) for result in results}
            if "unknown" in statuses:
                occurrence_status = "delivery_unknown"
            elif "sent" in statuses:
                occurrence_status = "partial_failure"
            else:
                occurrence_status = "cancelled" if reason == "rule_deleted" else "disabled"
            transaction.set(ref, {
                "channels": results,
                "status": occurrence_status,
                "pending": False,
                "failureCode": reason,
                "failureReason": "rule became inactive before the next provider call",
                "completedAt": completed_at,
                "leaseOwner": firestore.DELETE_FIELD,
                "leaseExpiresAt": firestore.DELETE_FIELD,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }, merge=True)
            if rule_snap.exists:
                transaction.set(rule_ref, {
                    "scheduleStatus": occurrence_status,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                }, merge=True)
            return reason
        changed = False
        for result in results:
            if result.get("channel") == channel and result.get("status") == "pending":
                result.update({
                    "status": "sending",
                    "attemptCount": int(result.get("attemptCount") or 0) + 1,
                    "attemptedAt": attempted_at.astimezone(timezone.utc).isoformat(),
                })
                changed = True
                break
        if changed:
            transaction.set(ref, {"channels": results, "updatedAt": firestore.SERVER_TIMESTAMP}, merge=True)
            return "began"
        return "not_pending"

    return str(begin(transaction))


def record_channel_result(uid: str, event_id: str, result: Dict[str, Any], worker_id: str) -> None:
    """Persist one channel result without replacing sibling channel results."""
    from firebase_admin import firestore

    db = get_db()
    ref = db.collection("users").document(uid).collection(NOTIFICATION_LOGS).document(event_id)
    transaction = db.transaction()

    @firestore.transactional
    def record(transaction):
        snap = ref.get(transaction=transaction)
        if not snap.exists:
            raise RuntimeError(f"occurrence disappeared: {event_id}")
        data = snap.to_dict() or {}
        if data.get("leaseOwner") != worker_id:
            raise RuntimeError(f"occurrence lease lost: {event_id}")
        results = list(data.get("channels") or [])
        for index, current in enumerate(results):
            if current.get("channel") == result.get("channel"):
                results[index] = {**current, **result}
                break
        else:
            results.append(dict(result))
        transaction.set(ref, {"channels": results, "updatedAt": firestore.SERVER_TIMESTAMP}, merge=True)

    record(transaction)


def finalize_occurrence(
    uid: str,
    rule_id: str,
    event_id: str,
    status: str,
    updates: Dict[str, Any],
    rule_updates: Optional[Dict[str, Any]] = None,
    worker_id: Optional[str] = None,
) -> None:
    """Finalize an occurrence and its denormalized rule status atomically."""
    from firebase_admin import firestore

    db = get_db()
    log_ref = db.collection("users").document(uid).collection(NOTIFICATION_LOGS).document(event_id)
    rule_ref = db.collection("users").document(uid).collection(ALERT_RULES).document(rule_id)
    transaction = db.transaction()
    clean_updates = {key: value for key, value in updates.items() if value is not None}
    clean_rule_updates = {
        key: value for key, value in (rule_updates or {}).items() if value is not None
    }

    @firestore.transactional
    def finalize(transaction):
        snap = log_ref.get(transaction=transaction)
        rule_snap = rule_ref.get(transaction=transaction)
        if not snap.exists:
            raise RuntimeError(f"occurrence disappeared: {event_id}")
        data = snap.to_dict() or {}
        if worker_id is not None and data.get("leaseOwner") != worker_id:
            raise RuntimeError(f"occurrence lease lost before finalization: {event_id}")
        transaction.set(log_ref, {
            **clean_updates,
            "status": status,
            "pending": False,
            "leaseOwner": firestore.DELETE_FIELD,
            "leaseExpiresAt": firestore.DELETE_FIELD,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        merged_rule_updates = {
            "scheduleStatus": status,
            "updatedAt": firestore.SERVER_TIMESTAMP,
            **clean_rule_updates,
        }
        # A user may delete the rule after the occurrence is claimed. Finalize
        # the permanent occurrence but never recreate a partial rule document.
        if rule_snap.exists:
            transaction.set(rule_ref, merged_rule_updates, merge=True)

    finalize(transaction)


def reserve_log(uid: str, event_id: str) -> bool:
    """Atomically reserve a NotificationLog slot for ``event_id`` (reserve-before-send).

    Uses Firestore ``DocumentReference.create()`` which FAILS if the document
    already exists — a server-side atomic check-and-set. Returns:
      - True  : we created (own) the reservation -> proceed to deliver + finalize
      - False : it already existed -> another run won this bucket; short-circuit

    This is the idempotency primitive that closes the check-then-act race a plain
    ``log_exists`` read leaves open. The reservation is later overwritten with the
    full record by ``write_notification_log`` (``set(..., merge=False)``).
    """
    from firebase_admin import firestore  # lazy import for SERVER_TIMESTAMP

    db = get_db()
    ref = (
        db.collection("users").document(uid)
        .collection(NOTIFICATION_LOGS).document(event_id)
    )
    try:
        ref.create({
            "eventId": event_id,
            "reservedAt": firestore.SERVER_TIMESTAMP,
            "pending": True,
        })
        return True
    except Exception as exc:  # noqa: BLE001
        # AlreadyExists (HTTP 409 / google.api_core.exceptions.AlreadyExists or
        # google.cloud.exceptions.Conflict) => someone already reserved this
        # bucket. Anything else: re-raise so the engine can fall back.
        name = type(exc).__name__
        if name in ("AlreadyExists", "Conflict") or "already exists" in str(exc).lower():
            return False
        raise


def log_exists(uid: str, event_id: str) -> bool:
    """True when a NotificationLog with this eventId already exists.

    Idempotency check: we store logs keyed by id == eventId, so a direct doc
    get is enough and avoids needing a composite index.
    """
    db = get_db()
    snap = (
        db.collection("users").document(uid)
        .collection(NOTIFICATION_LOGS).document(event_id).get()
    )
    return bool(snap.exists)


def write_notification_log(uid: str, log: NotificationLog) -> None:
    """Write exactly one NotificationLog (永久 보존). Keyed by eventId.

    Uses ``merge=False`` set so a re-run with the same eventId overwrites an
    identical record rather than duplicating; combined with ``log_exists`` the
    engine never re-sends. ``createdAt`` is stamped with the server timestamp.
    """
    from firebase_admin import firestore  # lazy import for SERVER_TIMESTAMP

    db = get_db()
    payload = log.to_dict()
    payload["createdAt"] = firestore.SERVER_TIMESTAMP
    (
        db.collection("users").document(uid)
        .collection(NOTIFICATION_LOGS).document(log.id)
        .set(payload)
    )


def _remove_invalid_push_data(data: Dict[str, Any], invalid: set) -> Tuple[List[str], List[Dict[str, Any]], int]:
    """Pure normalization used by the transaction and offline regression tests."""
    current_tokens = data.get("pushTokens") if isinstance(data.get("pushTokens"), list) else []
    current_tokens = [token for token in current_tokens if isinstance(token, str) and token]
    current_devices = data.get("pushDevices") if isinstance(data.get("pushDevices"), list) else []
    current_devices = [device for device in current_devices if isinstance(device, dict)]
    before = {
        *current_tokens,
        *(device.get("token") for device in current_devices if isinstance(device.get("token"), str)),
    }
    next_tokens = [token for token in current_tokens if token not in invalid]
    next_devices = [device for device in current_devices if device.get("token") not in invalid]
    after = {
        *next_tokens,
        *(device.get("token") for device in next_devices if isinstance(device.get("token"), str)),
    }
    return next_tokens, next_devices, len(before - after)


def remove_invalid_push_tokens(uid: str, invalid_tokens: List[str]) -> int:
    """Atomically remove only FCM-confirmed invalid tokens for one user.

    A failed token lookup on a browser is never enough to remove a token. This
    function is called only after PushChannel received an UNREGISTERED/invalid
    result from FCM, and a transaction preserves tokens registered concurrently
    by another browser.
    """
    invalid = {token for token in invalid_tokens if isinstance(token, str) and token}
    if not invalid:
        return 0
    from firebase_admin import firestore  # lazy import for SERVER_TIMESTAMP

    db = get_db()
    ref = db.collection("users").document(uid).collection(ALERT_SETTINGS).document(ALERT_SETTINGS_DOC_ID)
    transaction = db.transaction()

    @firestore.transactional
    def remove_in_transaction(transaction):
        snap = ref.get(transaction=transaction)
        data = snap.to_dict() or {}
        next_tokens, next_devices, removed = _remove_invalid_push_data(data, invalid)
        if removed:
            transaction.update(ref, {
                "pushTokens": next_tokens,
                "pushDevices": next_devices,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            })
        return removed

    return int(remove_in_transaction(transaction))


# --- Test-push request queue (browser -> engine bridge) ----------------------
#
# The web "테스트 Push/Telegram" buttons do NOT deliver in the browser. They
# enqueue a request doc here; the Python engine drains it through the SAME
# production path (send_test_alert -> deliver -> PushChannel/TelegramChannel),
# so test and production share one delivery implementation. Client writes are
# already permitted by firestore.rules (users/{uid}/**).


def list_pending_test_requests(uid: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """List pending test-push requests, oldest first.

    Each returned dict includes ``id`` and ``uid`` alongside the stored fields
    (``channels``, ``message``, ``status`` …). When ``uid`` is given the query
    is scoped to that user; otherwise a collection_group scan spans all users
    (falling back to an unfiltered scan if the status index is not deployed).
    """
    from google.cloud.firestore_v1 import FieldFilter  # lazy import

    db = get_db()
    out: List[Dict[str, Any]] = []

    if uid:
        col = db.collection("users").document(uid).collection(TEST_PUSH_REQUESTS)
        snaps = list(col.where(filter=FieldFilter("status", "==", "pending")).stream())
        for snap in snaps:
            out.append({"id": snap.id, "uid": uid, **(snap.to_dict() or {})})
    else:
        try:
            group = db.collection_group(TEST_PUSH_REQUESTS).where(
                filter=FieldFilter("status", "==", "pending")
            )
            snaps = list(group.stream())
        except Exception as exc:  # noqa: BLE001
            if not _is_missing_index_error(exc):
                raise
            logger.warning(
                "collection-group index for testPushRequests.status is missing (%s); "
                "falling back to an unfiltered scan + in-memory filter.",
                exc,
            )
            snaps = list(db.collection_group(TEST_PUSH_REQUESTS).stream())
        for snap in snaps:
            data = snap.to_dict() or {}
            if data.get("status") != "pending":
                continue
            out.append({"id": snap.id, "uid": _extract_uid_from_path(snap), **data})

    # Oldest first so requests are handled roughly in order.
    out.sort(key=lambda d: str(d.get("requestedAt") or ""))
    return out[:limit] if limit else out


def mark_test_request(
    uid: str,
    req_id: str,
    status: str,
    results: Optional[List[Dict[str, Any]]] = None,
    log_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Finalize a test-push request with the engine's outcome (merge write)."""
    from firebase_admin import firestore  # lazy import for SERVER_TIMESTAMP

    db = get_db()
    updates: Dict[str, Any] = {
        "status": status,
        "processedAt": firestore.SERVER_TIMESTAMP,
    }
    if results is not None:
        updates["results"] = results
    if log_id is not None:
        updates["logId"] = log_id
    if error is not None:
        updates["error"] = error
    (
        db.collection("users").document(uid)
        .collection(TEST_PUSH_REQUESTS).document(req_id)
        .set(updates, merge=True)
    )


# --- AlertRule state writes --------------------------------------------------


def update_rule_state(
    uid: str,
    rule_id: str,
    last_triggered_at: Optional[str] = None,
    last_value: Optional[Any] = None,
    enabled: Optional[bool] = None,
    engine_version: Optional[str] = None,
) -> None:
    """Update only the engine-owned state fields on a rule (merge write).

    We never rewrite the whole rule doc (the web app owns name/condition/etc.).
    Only lastTriggeredAt / lastValue / enabled / engineVersion are touched.
    """
    from firebase_admin import firestore  # lazy import

    db = get_db()
    updates: Dict[str, Any] = {"updatedAt": firestore.SERVER_TIMESTAMP}
    if last_triggered_at is not None:
        updates["lastTriggeredAt"] = last_triggered_at
    if last_value is not None:
        updates["lastValue"] = last_value
    if enabled is not None:
        updates["enabled"] = enabled
    if engine_version is not None:
        updates["engineVersion"] = engine_version
    (
        db.collection("users").document(uid)
        .collection(ALERT_RULES).document(rule_id)
        .set(updates, merge=True)
    )
