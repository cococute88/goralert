"""Delivery channels (telegram, push). Each implements ``DeliveryChannel``.

Channels never raise on missing credentials or transport errors — they return
a ``ChannelResult`` with status "failed" and a clear error, so delivery
isolation holds (one channel's failure can't block the others or crash the run).
"""

from .base import DeliveryChannel, ChannelSendResult
from .telegram import TelegramChannel
from .push import PushChannel

__all__ = [
    "DeliveryChannel",
    "ChannelSendResult",
    "TelegramChannel",
    "PushChannel",
    "build_default_channels",
]


def build_default_channels():
    """Return the {channel-name: channel} registry used by delivery fan-out."""
    return {
        "telegram": TelegramChannel(),
        "push": PushChannel(),
    }
