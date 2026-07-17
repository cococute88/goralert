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
- users/{uid}/calendarCustomEvents/{id}  (READ-ONLY)
- users/{uid}/calendarAlertMarks/{id}    (🔔 bell, Goralert-owned)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from .config import load_config, load_service_account_dict
from .models import AlertRule, AlertSettings, NotificationLog

logger = logging.getLogger("alert_engine.firestore")

ALERT_RULES = "alertRules"
NOTIFICATION_LOGS = "notificationLogs"
ALERT_SETTINGS = "alertSettings"
ALERT_SETTINGS_DOC_ID = "default"
CALENDAR_EVENTS = "calendarEvents"
CALENDAR_CUSTOM_EVENTS = "calendarCustomEvents"
CALENDAR_ALERT_MARKS = "calendarAlertMarks"
TEST_PUSH_REQUESTS = "testPushRequests"

_db = None  # cached Firestore client


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

    The collection_group path is most efficient with a COLLECTION_GROUP index on
    (enabled) for the ``alertRules`` group (see ``firestore.indexes.json``). When
    that index has not been deployed to the project, Firestore rejects the
    filtered query with a "requires an index" error; rather than failing the
    whole run we fall back to an unfiltered collection_group scan and filter
    ``enabled == True`` in memory, so the engine keeps working until the index is
    deployed (``firebase deploy --only firestore:indexes``).
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

    # All users via collection_group. Prefer the indexed (filtered) query; on a
    # missing-index error, fall back to an unfiltered scan + in-memory filter.
    try:
        group = db.collection_group(ALERT_RULES).where(filter=_enabled_filter())
        snaps = list(group.stream())
    except Exception as exc:  # noqa: BLE001
        if not _is_missing_index_error(exc):
            raise
        logger.warning(
            "collection-group index for alertRules.enabled is missing (%s); "
            "falling back to an unfiltered scan + in-memory filter. Deploy "
            "firestore.indexes.json (firebase deploy --only firestore:indexes) "
            "to restore the efficient path.",
            exc,
        )
        snaps = list(db.collection_group(ALERT_RULES).stream())

    for snap in snaps:
        data = snap.to_dict() or {}
        # Guard: the fallback path returns disabled rules too, so filter here.
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


def read_calendar_events(uid: str) -> List[Dict[str, Any]]:
    """Read users/{uid}/calendarEvents (meta incl. star/heart/ticker/type)."""
    db = get_db()
    out: List[Dict[str, Any]] = []
    for snap in db.collection("users").document(uid).collection(CALENDAR_EVENTS).stream():
        out.append({"id": snap.id, **(snap.to_dict() or {})})
    return out


def read_calendar_custom_events(uid: str) -> List[Dict[str, Any]]:
    """Read users/{uid}/calendarCustomEvents (id,title,date,type,ticker?)."""
    db = get_db()
    out: List[Dict[str, Any]] = []
    for snap in db.collection("users").document(uid).collection(CALENDAR_CUSTOM_EVENTS).stream():
        out.append({"id": snap.id, **(snap.to_dict() or {})})
    return out


def read_calendar_alert_marks(uid: str) -> List[Dict[str, Any]]:
    """Read users/{uid}/calendarAlertMarks (🔔 bell marks, Goralert-owned)."""
    db = get_db()
    out: List[Dict[str, Any]] = []
    for snap in db.collection("users").document(uid).collection(CALENDAR_ALERT_MARKS).stream():
        out.append({"id": snap.id, **(snap.to_dict() or {})})
    return out


# --- NotificationLog writes + idempotency ------------------------------------


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
