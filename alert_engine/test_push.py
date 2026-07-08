"""Test-push drain — the browser test button's server-side fulfilment.

Single-source-of-truth bridge. The web app's "테스트 Push / Telegram" buttons
do NOT deliver anything in the browser; they enqueue a request document at
``users/{uid}/testPushRequests/{id}``. This module drains those requests and
delivers each one through the EXACT production path:

    process_test_requests
        -> AlertEngine.send_test_alert          (engine.py)
            -> deliver()                         (delivery.py — retry/backoff/isolation)
                -> channel_registry["push"]      == PushChannel  (production instance)
                    -> messaging.send_each_for_multicast -> FCM

There is no separate push implementation for tests: production alerts
(``process_rule``) and test alerts (``send_test_alert``) both fan out via the
same ``deliver`` + ``build_default_channels`` registry, so the FCM logic,
token validation, failure handling, retries and logging live ONLY inside
PushChannel/TelegramChannel. Whatever the channel returns is the sole source
of truth for success — this module never fabricates a "sent".

A synthetic AlertRule is built from the user's settings defaults (mirroring the
web ``buildTestRule`` in app/settings/page.tsx) so ``send_test_alert`` can render
the message; the rule is never persisted and rule state is never touched.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .config import load_config
from .engine import AlertEngine
from .models import AlertRule, AlertSettings

logger = logging.getLogger("alert_engine.test_push")

VALID_CHANNELS = ("push", "telegram")


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

    Returns a small counter summary ``{"processed", "sent", "failed", "error"}``.
    One request's failure never aborts the drain (delivery isolation).
    """
    if firestore is None:
        from . import firestore_client as firestore  # lazy import
    engine = engine or AlertEngine(config=load_config(), firestore=firestore)

    requests = firestore.list_pending_test_requests(uid, limit=limit)
    logger.info("[test-push] draining %d pending request(s) (uid=%s)", len(requests), uid or "<all>")

    counts = {"processed": 0, "sent": 0, "failed": 0, "error": 0}
    for req in requests:
        req_uid = req.get("uid") or uid
        req_id = req.get("id")
        if not req_uid or not req_id:
            logger.warning("[test-push] skipping malformed request: %r", req)
            continue

        channels = _normalize_channels(req.get("channels"))
        logger.info("[test-push] request=%s uid=%s channels=%s", req_id, req_uid, channels)
        counts["processed"] += 1

        try:
            settings = firestore.load_alert_settings(req_uid)
            # A caller-supplied message overrides the settings defaults.
            msg = req.get("message") if isinstance(req.get("message"), dict) else None
            if msg and (msg.get("title") or msg.get("body")):
                settings = _with_message_defaults(settings, msg)

            rule = build_test_rule(req_uid, settings, channels)
            log = engine.send_test_alert(req_uid, rule, channels, settings=settings, dry_run=dry_run)

            if log is None:
                # REQ-022.4: no credential for any requested channel.
                firestore.mark_test_request(
                    req_uid, req_id, "failed",
                    error="no credential for requested channel(s) (register push / set Telegram chat id)",
                )
                counts["failed"] += 1
                logger.warning("[test-push] request=%s -> no credentials, nothing delivered", req_id)
                continue

            results = [
                {"channel": c.channel, "status": c.status, **({"error": c.error} if c.error else {})}
                for c in log.channels
            ]
            any_sent = any(c.status == "sent" for c in log.channels)
            firestore.mark_test_request(
                req_uid, req_id, "done" if any_sent else "failed",
                results=results, log_id=log.id,
                error=None if any_sent else "; ".join(c.error for c in log.channels if c.error) or "delivery failed",
            )
            counts["sent" if any_sent else "failed"] += 1
            logger.info(
                "[test-push] request=%s -> %s results=%s log=%s",
                req_id, "SENT" if any_sent else "FAILED", results, log.id,
            )
        except Exception as exc:  # noqa: BLE001 - isolation: one bad request never aborts the drain
            counts["error"] += 1
            logger.exception("[test-push] request=%s crashed: %s", req_id, exc)
            try:
                firestore.mark_test_request(req_uid, req_id, "error", error=str(exc))
            except Exception:  # noqa: BLE001
                logger.warning("[test-push] failed to mark request=%s as error", req_id)

    logger.info("[test-push] drain done :: %s", counts)
    return counts


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
