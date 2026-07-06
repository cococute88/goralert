"""Delivery fan-out with per-channel retry + exponential backoff.

Guarantees:
- Delivery isolation: each channel is attempted independently; one channel's
  failure (or exception) never blocks the others.
- Bounded retries: each channel is retried up to ``max_retries`` times with
  exponential backoff (base * 2**attempt, capped), only for transient failures.
- Exactly one NotificationLog: this module returns the aggregated per-channel
  results + invalid push tokens; the engine writes a single log regardless of
  partial failure.

Backoff is real ``time.sleep`` in production but injectable (``sleep_fn``) so
tests run instantly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .channels.base import ChannelSendResult, DeliveryChannel
from .config import load_config
from .models import AlertSettings, ChannelResult, MessageTemplate

logger = logging.getLogger("alert_engine.delivery")


@dataclass
class DeliveryOutcome:
    """Aggregated result of a fan-out delivery."""

    results: List[ChannelResult] = field(default_factory=list)
    invalid_push_tokens: List[str] = field(default_factory=list)
    any_sent: bool = False


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def deliver(
    message: MessageTemplate,
    channels: List[str],
    channel_registry: Dict[str, DeliveryChannel],
    settings: AlertSettings,
    max_retries: Optional[int] = None,
    backoff_base: Optional[float] = None,
    backoff_max: Optional[float] = None,
    sleep_fn: Callable[[float], None] = _sleep,
) -> DeliveryOutcome:
    """Fan out ``message`` to each requested channel with retry/backoff.

    Returns a DeliveryOutcome with one ChannelResult per requested channel.
    Unknown channel names yield a failed result rather than raising.
    """
    cfg = load_config()
    retries = cfg.delivery_max_retries if max_retries is None else max_retries
    base = cfg.delivery_backoff_base_seconds if backoff_base is None else backoff_base
    cap = cfg.delivery_backoff_max_seconds if backoff_max is None else backoff_max

    outcome = DeliveryOutcome()

    for channel_name in channels:
        channel = channel_registry.get(channel_name)
        if channel is None:
            outcome.results.append(ChannelResult(channel_name, "failed", error="unknown channel"))
            continue

        result = _deliver_one(channel, message, settings, retries, base, cap, sleep_fn)
        outcome.results.append(ChannelResult(result.channel, result.status, error=result.error))
        if result.invalid_tokens:
            outcome.invalid_push_tokens.extend(result.invalid_tokens)
        if result.ok:
            outcome.any_sent = True

    return outcome


def _deliver_one(
    channel: DeliveryChannel,
    message: MessageTemplate,
    settings: AlertSettings,
    max_retries: int,
    backoff_base: float,
    backoff_max: float,
    sleep_fn: Callable[[float], None],
) -> ChannelSendResult:
    """Attempt a single channel with up to ``max_retries`` retries.

    A channel that raises is caught and converted to a failed result; we keep
    retrying transient failures until attempts are exhausted.
    """
    attempt = 0
    last: Optional[ChannelSendResult] = None
    while attempt <= max_retries:
        try:
            last = channel.send(message, settings)
        except Exception as exc:  # noqa: BLE001 - isolation: never propagate
            last = ChannelSendResult(getattr(channel, "name", "?"), "failed", error=f"exception: {exc}")

        if last.ok:
            return last

        # A credential/config failure won't recover via retry; bail early.
        if last.error and _is_permanent(last.error):
            return last

        if attempt < max_retries:
            delay = min(backoff_max, backoff_base * (2 ** attempt))
            logger.info("channel %s retry %d/%d after %.1fs", last.channel, attempt + 1, max_retries, delay)
            sleep_fn(delay)
        attempt += 1

    return last if last is not None else ChannelSendResult("?", "failed", error="no attempt made")


def _is_permanent(error: str) -> bool:
    """Heuristic: config/credential problems are not worth retrying."""
    needles = ("not configured", "not set in alertSettings", "no pushTokens", "unknown channel", "requests unavailable")
    low = error.lower()
    return any(n.lower() in low for n in needles)
