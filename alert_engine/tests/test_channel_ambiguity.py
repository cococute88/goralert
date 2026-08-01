"""Provider-boundary ambiguity must be preserved instead of reported failed."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from alert_engine.channels.push import _is_ambiguous_batch_failure
from alert_engine.channels.telegram import TelegramChannel
from alert_engine.models import AlertSettings, MessageTemplate


def test_telegram_timeout_is_delivery_unknown(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    def timeout(*args, **kwargs):
        raise TimeoutError("response timed out")

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=timeout))
    result = TelegramChannel().send(
        MessageTemplate("title", "body"),
        AlertSettings(globalEnabled=True, telegramChatId="chat"),
    )

    assert result.status == "unknown"
    assert "network error" in (result.error or "")


def test_fcm_timeout_is_ambiguous_but_invalid_argument_is_definitive():
    class DeadlineExceeded(Exception):
        code = "DEADLINE_EXCEEDED"

    class InvalidArgument(Exception):
        code = "INVALID_ARGUMENT"

    assert _is_ambiguous_batch_failure(DeadlineExceeded("timeout")) is True
    assert _is_ambiguous_batch_failure(InvalidArgument("bad request")) is False
