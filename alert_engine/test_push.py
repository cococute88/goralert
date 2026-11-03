"""Durable test queue: atomic request claim -> common deliver -> production channels.

The browser only enqueues testPushRequests. Each request has one deterministic
log, a lease, and a persisted pending -> sending -> sent/failed/unknown channel
boundary. The queue never creates an alternate FCM or Telegram implementation.
A reclaimed worker reuses the saved message/channel snapshot and success evidence.
Unknowable provider outcomes are not retried. Dry-run inspects requests without
attempting a claim, log write, token cleanup, request write or external send.
Synthetic rules are never persisted. Requests older than 24 hours (or without
age evidence) are held for review rather than unexpectedly sent after an outage.
"""

from __future__ import annotations

import logging
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .config import load_config
from .engine import AlertEngine
from .models import AlertRule, AlertSettings
from .models import ChannelResult, NotificationLog
from .event import render_message
from .delivery import deliver
from .recurrence import parse_datetime, as_utc

logger = logging.getLogger("alert_engine.test_push")

VALID_CHANNELS = ("push", "telegram")


def _uid_tag(uid: Optional[str]) -> str:
    if not uid:
        return "<all>"
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:12]


def build_test_rule(uid: str, settings: AlertSettings, channels: List[str]) -> AlertRule:
    """Synthetic, non-persisted rule for a settings-screen test send.

    Mirrors ``buildTestRule`` in app/settings/page.tsx: a minimal ``date`` rule
    carrying the requested channels and the default message. Built via
    ``AlertRule.from_dict`` so every nested policy is constructed the same way a
    real rule is.
    """
    title = (settings.defaultMessageTitle or "").strip() or "고라알림 테스트"
    body = (settings.defaultMessageBody or "").strip() or "설정 화면에서 보낸 테스트 알림입니다"
    return AlertRule.from_dict({
        "id": "settings-test",
        "uid": uid,
        "kind": "date",
        "name": "설정 테스트",
        "enabled": True,
        "condition": {"kind": "date"},
        "trigger": {"mode": "once"},
        "delivery": {"channels": channels, "message": {"title": title, "body": body}},
    })


def _normalize_channels(raw: Any) -> List[str]:
    """Keep only known channel names, preserving order and de-duplicating."""
    if not isinstance(raw, list):
        raw = ["push"]
    seen: List[str] = []
    for c in raw:
        if c in VALID_CHANNELS and c not in seen:
            seen.append(c)
    return seen or ["push"]


def process_test_requests(
    uid: Optional[str] = None,
    engine: Optional[AlertEngine] = None,
    firestore=None,
    dry_run: bool = False,
    limit: int = 50,
) -> Dict[str, int]:
    """Drain pending test-push requests through the production delivery path.

    Returns separate full-success, partial, failed, unknown, held, busy and preview counts.
    One request's failure never aborts the drain (delivery isolation).
    """
    if firestore is None:
        from . import firestore_client as firestore  # lazy import
    engine = engine or AlertEngine(config=load_config(), firestore=firestore)

    requests = firestore.list_pending_test_requests(uid, limit=limit)
    logger.info("[test-push] draining %d pending request(s) (uid=%s)", len(requests), _uid_tag(uid))

    counts = {"processed": 0, "sent": 0, "failed": 0, "error": 0,
              "partial": 0, "unknown": 0, "preview": 0, "held": 0, "busy": 0}
    for req in requests:
        req_uid = req.get("uid") or uid
        req_id = req.get("id")
        if not req_uid or not req_id:
            logger.warning("[test-push] skipping malformed request")
            counts["error"] += 1
            continue

        channels = _normalize_channels(req.get("channels"))
        logger.info("[test-push] request=%s uid=%s channels=%s", req_id, _uid_tag(req_uid), channels)
        counts["processed"] += 1

        try:
            settings = firestore.load_alert_settings(req_uid)
            # A caller-supplied message overrides the settings defaults.
            msg = req.get("message") if isinstance(req.get("message"), dict) else None
            if msg and (msg.get("title") or msg.get("body")):
                settings = _with_message_defaults(settings, msg)

            rule = build_test_rule(req_uid, settings, channels)
            if dry_run:
                # Inspect only. Do not claim, mark, log, clean tokens or send.
                counts["preview"] += 1
                continue
            status = _process_request(engine, firestore, req_uid, req_id, req, rule, channels, settings)
            counts[{"done": "sent", "recovery_held": "held"}.get(status, status)] += 1
        except Exception as exc:  # noqa: BLE001 - isolation: one bad request never aborts the drain
            counts["error"] += 1
            # Leave durable 'sending' intact if provider I/O may have started.
            # The next owner reconciles it as unknown; never blanket-requeue.
            logger.error("[test-push] request=%s error=%s", req_id, type(exc).__name__)

    logger.info("[test-push] drain done :: %s", counts)
    return counts


def _process_request(engine, firestore, uid, request_id, request, rule, channels, settings):
    now = datetime.now(timezone.utc)
    event_id = f"settings-test:request:{request_id}"
    worker = uuid.uuid4().hex
    message = render_message(rule, {"name": rule.name})
    payload = NotificationLog(
        id=event_id, eventId=event_id, ruleId=rule.id, kind=rule.kind,
        firedAt=now.isoformat(), evaluatedAt=now.isoformat(), message=message,
        channels=[ChannelResult(c, "pending") for c in channels],
        isTest=True, ruleName=rule.name, status="processing",
    ).to_dict()
    claim = firestore.claim_test_request(uid, request_id, payload, worker, now)
    if claim.get("claim") != "claimed":
        return "busy"
    record = claim["record"]
    # A reclaimed worker uses the immutable channel/message snapshot.
    from .models import MessageTemplate
    message = MessageTemplate(**record["message"])
    requested = parse_datetime(request.get("requestedAt"), "UTC")
    # Missing or malformed age evidence cannot authorize a surprise test send.
    stale = requested is None or as_utc(now) - as_utc(requested) > timedelta(hours=24)
    results = []
    for row in record.get("channels", []):
        row = dict(row)
        name = row["channel"]
        if row["status"] == "sending":
            row.update(status="unknown", error="발송 시작 후 작업 중단: 중복 방지를 위해 자동 재발송하지 않습니다.",
                       errorCode="ambiguous_delivery", completedAt=datetime.now(timezone.utc).isoformat())
            firestore.record_channel_result(uid, event_id, row, worker)
        elif row["status"] == "pending" and not stale:
            if not settings.globalEnabled:
                row.update(status="failed", error="global alerts are disabled")
            elif not engine._test_has_credentials([name], settings):
                row.update(status="failed", error="no credential for requested channel")
            else:
                attempted_at = datetime.now(timezone.utc)
                if not firestore.begin_test_channel_attempt(uid, request_id, name, worker, attempted_at):
                    raise RuntimeError("test channel ownership lost")
                # Use the common production fan-out, without ambiguous retries.
                outcome = deliver(message, [name], engine.channels, settings, max_retries=0)
                row = {**row, **outcome.results[0].to_dict(), "attemptedAt": attempted_at.isoformat(),
                       "completedAt": datetime.now(timezone.utc).isoformat(), "attemptCount": 1}
                code = engine._delivery_error_code(name, row.get("error"))
                if code:
                    row["errorCode"] = code
                engine._cleanup_invalid_push_tokens(uid, rule.id, outcome.invalid_push_tokens)
            firestore.record_channel_result(uid, event_id, row, worker)
        results.append(row)
    statuses = [row["status"] for row in results]
    status = ("unknown" if "unknown" in statuses else
              "recovery_held" if stale and "pending" in statuses else
              "done" if statuses and all(s == "sent" for s in statuses) else
              "partial" if "sent" in statuses else "failed")
    error = None if status == "done" else "; ".join(row.get("error", "") for row in results) or status
    firestore.finish_test_request(uid, request_id, worker, status, results, error)
    return status


def _with_message_defaults(settings: AlertSettings, msg: Dict[str, Any]) -> AlertSettings:
    """Return a shallow copy of settings with message defaults overridden.

    Keeps the credential fields (pushTokens/telegramChatId) intact so the
    production delivery path is unaffected — only the rendered message changes.
    """
    import dataclasses

    return dataclasses.replace(
        settings,
        defaultMessageTitle=str(msg.get("title") or settings.defaultMessageTitle or "").strip() or None,
        defaultMessageBody=str(msg.get("body") or settings.defaultMessageBody or "").strip() or None,
    )
