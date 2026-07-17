"""Push delivery channel (FCM via firebase-admin messaging).

Uses the SAME firebase-admin app/credential as Firestore (no separate FCM key).
Delivers to every token in ``alertSettings.pushTokens`` with a SINGLE batched
``messaging.send_each_for_multicast`` call (the Python equivalent of the Admin
SDK's ``sendEachForMulticast``), then logs the **full** ``BatchResponse`` —
``success_count`` / ``failure_count`` and, per token, either the returned
``message_id`` or the real FCM ``error.code`` + ``error.message``. Nothing is
swallowed: ``messaging/registration-token-not-registered``,
``messaging/invalid-registration-token``, ``messaging/mismatched-credential``,
``messaging/third-party-auth-error`` etc. are surfaced verbatim so the exact
failure stage is visible in the run logs.

Tokens FCM reports as unregistered/invalid are collected in ``invalid_tokens``
so the engine can flag them for cleanup. If firebase-admin / credentials are
missing -> status:"failed" with a clear error (never crashes). Per-token
results are aggregated: "sent" when at least one token succeeds, else "failed".

NOTE: client push tokens are registered by the web app's real browser FCM client
(``lib/alerts/fcm-client.ts::registerPushToken``) and persisted to
``alertSettings.pushTokens``; this channel delivers to those tokens. The web
app's test button enqueues a request and this is the shared production path
that actually round-trips through FCM.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from ..models import AlertSettings, MessageTemplate
from .base import ChannelSendResult

logger = logging.getLogger("alert_engine.channels.push")


def _fcm_error_code(exc: BaseException) -> str:
    """Best-effort extraction of the real FCM error code from an exception.

    firebase-admin messaging errors subclass ``firebase_admin.exceptions.
    FirebaseError`` and expose a canonical ``.code`` (e.g. ``UNREGISTERED``,
    ``INVALID_ARGUMENT``, ``SENDER_ID_MISMATCH``, ``THIRD_PARTY_AUTH_ERROR``).
    The messaging-layer code (``messaging/registration-token-not-registered``,
    ``messaging/mismatched-credential`` …) lives in the HTTP error body carried
    on ``.cause``/``.http_response``. We surface whatever is available rather
    than hiding it behind the exception's Python class name.
    """
    code = getattr(exc, "code", None)
    if code:
        return str(code)
    return type(exc).__name__


def _fcm_detail(exc: BaseException) -> str:
    """Full, human-readable detail: ``ExcClass(CODE): message``.

    Includes the underlying cause (the requests/HTTP error that carries the
    ``messaging/...`` code and message) when firebase-admin attached one.
    """
    parts = [f"{type(exc).__name__}({_fcm_error_code(exc)}): {exc}"]
    cause = getattr(exc, "cause", None)
    if cause is not None and str(cause) and str(cause) not in str(exc):
        parts.append(f"cause={cause!r}")
    return " ".join(parts)


def _is_confirmed_unregistered(exc: Optional[BaseException]) -> bool:
    """True only when FCM confirms that this registration token is gone.

    INVALID_ARGUMENT can describe a malformed request, and SENDER_ID_MISMATCH
    can expose a project/configuration failure. Neither is safe evidence for
    deleting a user token. The Admin SDK's UnregisteredError (or canonical
    UNREGISTERED code) is the narrow, permanent-token signal.
    """
    if exc is None:
        return False
    return type(exc).__name__ == "UnregisteredError" or _fcm_error_code(exc).upper() == "UNREGISTERED"


class PushChannel:
    name = "push"

    def send(self, message: MessageTemplate, settings: AlertSettings, **kwargs: Any) -> ChannelSendResult:
        tokens: List[str] = list(settings.pushTokens) if settings and settings.pushTokens else []
        logger.info("[push] send start :: tokens=%d title=%r", len(tokens), message.title)
        if not tokens:
            logger.warning("[push] no pushTokens in alertSettings — nothing to deliver")
            return ChannelSendResult(
                self.name, "failed",
                error="no pushTokens in alertSettings (register via web app)",
            )

        # Ensure firebase-admin is initialized (shares the Firestore credential).
        try:
            from firebase_admin import messaging  # lazy import
            from .. import firestore_client  # ensures app init
            firestore_client._init_firebase()
            logger.info("[push] firebase-admin ready (shared Firestore credential)")
        except Exception as exc:  # noqa: BLE001
            # TODO(secret): provide FIREBASE_SERVICE_ACCOUNT to enable FCM push.
            logger.error("[push] FCM not configured: %s", _fcm_detail(exc))
            return ChannelSendResult(self.name, "failed", error=f"FCM not configured: {exc}")

        # Data-only is intentional. The generated service worker renders it via
        # onBackgroundMessage; adding a notification payload would let FCM/the
        # browser render one automatically and can duplicate the notification.
        multicast = messaging.MulticastMessage(
            data={"title": message.title, "body": message.body, "url": "/"},
            webpush=messaging.WebpushConfig(headers={"Urgency": "high"}),
            tokens=tokens,
        )

        # ONE batched call to FCM. send_each_for_multicast delivers each token in
        # its own message and NEVER raises for per-token failures — it returns a
        # BatchResponse whose per-token .exception carries the real FCM error.
        # A top-level raise here means the whole request failed (auth / network /
        # credential), which we also surface verbatim.
        try:
            batch = messaging.send_each_for_multicast(multicast)
        except Exception as exc:  # noqa: BLE001
            detail = _fcm_detail(exc)
            logger.error("[push] send_each_for_multicast raised (whole batch failed): %s", detail)
            return ChannelSendResult(
                self.name, "failed",
                error=f"FCM batch call failed: {detail}",
            )

        # --- FULL response dump (successCount / failureCount / per-token) --------
        logger.info(
            "[push] BatchResponse :: success_count=%d failure_count=%d total=%d",
            batch.success_count, batch.failure_count, len(tokens),
        )

        invalid: List[str] = []
        errors: List[str] = []
        for token, resp in zip(tokens, batch.responses):
            short = f"{token[:12]}…"
            if resp.success:
                logger.info("[push]   token=%s -> OK message_id=%s", short, resp.message_id)
                continue

            exc: Optional[BaseException] = resp.exception
            code = _fcm_error_code(exc) if exc is not None else "UNKNOWN"
            detail = _fcm_detail(exc) if exc is not None else "unknown error (no exception)"
            logger.error("[push]   token=%s -> FAILED code=%s detail=%s", short, code, detail)
            errors.append(f"{short}: {detail}")

            if _is_confirmed_unregistered(exc):
                invalid.append(token)

        if batch.success_count > 0:
            logger.info(
                "[push] send done :: sent=%d/%d invalid=%d",
                batch.success_count, len(tokens), len(invalid),
            )
            return ChannelSendResult(
                self.name, "sent",
                invalid_tokens=invalid,
                meta={
                    "sent": batch.success_count,
                    "total": len(tokens),
                    "failure_count": batch.failure_count,
                    "errors": errors,
                },
            )

        logger.error(
            "[push] send failed :: all %d token(s) failed :: %s",
            len(tokens), "; ".join(errors),
        )
        return ChannelSendResult(
            self.name, "failed",
            error=f"all {len(tokens)} token(s) failed: {'; '.join(errors)[:500]}",
            invalid_tokens=invalid,
        )
