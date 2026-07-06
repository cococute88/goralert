"""Push delivery channel (FCM via firebase-admin messaging).

Uses the SAME firebase-admin app/credential as Firestore (no separate FCM key).
Sends to each token in ``alertSettings.pushTokens``. Tokens that FCM reports as
unregistered/invalid are collected in ``invalid_tokens`` so the engine can flag
them for cleanup.

If firebase-admin / credentials are missing -> status:"failed" with a clear
error (never crashes). Per-token results are aggregated: "sent" when at least
one token succeeds, otherwise "failed".

NOTE: client push tokens are registered by the web app's real browser FCM client
(``lib/alerts/fcm-client.ts::registerPushToken``) and persisted to
``alertSettings.pushTokens``; this channel delivers to those tokens.
"""

from __future__ import annotations

import logging
from typing import Any, List

from ..models import AlertSettings, MessageTemplate
from .base import ChannelSendResult

logger = logging.getLogger("alert_engine.channels.push")


class PushChannel:
    name = "push"

    def send(self, message: MessageTemplate, settings: AlertSettings, **kwargs: Any) -> ChannelSendResult:
        tokens: List[str] = list(settings.pushTokens) if settings and settings.pushTokens else []
        if not tokens:
            return ChannelSendResult(
                self.name, "failed",
                error="no pushTokens in alertSettings (register via web app)",
            )

        # Ensure firebase-admin is initialized (shares the Firestore credential).
        try:
            from firebase_admin import messaging  # lazy import
            from .. import firestore_client  # ensures app init
            firestore_client._init_firebase()
        except Exception as exc:  # noqa: BLE001
            # TODO(secret): provide FIREBASE_SERVICE_ACCOUNT to enable FCM push.
            return ChannelSendResult(self.name, "failed", error=f"FCM not configured: {exc}")

        sent = 0
        invalid: List[str] = []
        errors: List[str] = []
        for token in tokens:
            try:
                msg = messaging.Message(
                    notification=messaging.Notification(title=message.title, body=message.body),
                    token=token,
                )
                messaging.send(msg)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                name = type(exc).__name__
                # firebase-admin raises UnregisteredError / InvalidArgumentError
                # for dead tokens; treat those as invalid for cleanup.
                if "Unregistered" in name or "InvalidArgument" in name or "NotFound" in name:
                    invalid.append(token)
                errors.append(f"{token[:12]}…: {name}")

        if sent > 0:
            return ChannelSendResult(
                self.name, "sent",
                invalid_tokens=invalid,
                meta={"sent": sent, "total": len(tokens), "errors": errors},
            )
        return ChannelSendResult(
            self.name, "failed",
            error=f"all {len(tokens)} token(s) failed: {'; '.join(errors)[:300]}",
            invalid_tokens=invalid,
        )
