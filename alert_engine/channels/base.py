"""DeliveryChannel protocol + send-result type."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol

from ..models import AlertSettings, MessageTemplate


@dataclass
class ChannelSendResult:
    """Result of a single channel send attempt.

    ``status`` is "sent" or "failed". ``invalid_tokens`` lists push tokens that
    the provider reported as unregistered/invalid (push only) so the engine can
    surface cleanup. ``meta`` holds provider-specific details for logging.
    """

    channel: str
    status: str
    error: Optional[str] = None
    invalid_tokens: List[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "sent"


class DeliveryChannel(Protocol):
    name: str

    def send(self, message: MessageTemplate, settings: AlertSettings, **kwargs: Any) -> ChannelSendResult:
        ...
