"""Telegram delivery channel (Bot API sendMessage).

Token comes from ``TELEGRAM_BOT_TOKEN`` (env / GitHub Secret); the chat id comes
from the per-user ``alertSettings.telegramChatId`` (configured via the web app).
Missing token or chat id -> status:"failed" with a clear error (never crashes).
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import load_config
from ..models import AlertSettings, MessageTemplate
from .base import ChannelSendResult

logger = logging.getLogger("alert_engine.channels.telegram")

_API_BASE = "https://api.telegram.org"
_TIMEOUT_SECONDS = 10


class TelegramChannel:
    name = "telegram"

    def send(self, message: MessageTemplate, settings: AlertSettings, **kwargs: Any) -> ChannelSendResult:
        cfg = load_config()
        token = cfg.telegram_bot_token
        if not token:
            # TODO(secret): set TELEGRAM_BOT_TOKEN (GitHub Secret) to enable Telegram.
            return ChannelSendResult(self.name, "failed", error="TELEGRAM_BOT_TOKEN not configured")

        chat_id = settings.telegramChatId if settings else None
        if not chat_id:
            return ChannelSendResult(
                self.name, "failed",
                error="telegramChatId not set in alertSettings (configure via web app)",
            )

        # Plain text (NO parse_mode): user-authored titles/bodies may contain
        # Markdown metacharacters (_ * [ ` etc.). With parse_mode="Markdown" those
        # cause a Telegram HTTP 400 and the alert silently fails to deliver.
        # Sending as plain text guarantees delivery; we lose bold formatting only.
        text = f"{message.title}\n{message.body}" if message.title else message.body
        try:
            import requests  # lazy import
        except Exception as exc:  # noqa: BLE001
            return ChannelSendResult(self.name, "failed", error=f"requests unavailable: {exc}")

        url = f"{_API_BASE}/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001
            return ChannelSendResult(self.name, "failed", error=f"network error: {exc}")

        if resp.status_code == 200:
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                body = {}
            if body.get("ok"):
                return ChannelSendResult(self.name, "sent", meta={"message_id": body.get("result", {}).get("message_id")})
            return ChannelSendResult(self.name, "failed", error=f"telegram error: {body.get('description')}")
        # Surface a trimmed body for diagnostics.
        return ChannelSendResult(
            self.name, "failed",
            error=f"HTTP {resp.status_code}: {str(resp.text)[:200]}",
        )
