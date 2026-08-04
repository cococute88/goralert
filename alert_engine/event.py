"""Event id / event building / message rendering.

The idempotency key is ``eventId = f"{ruleId}:{bucketTime}"`` where bucketTime
is a stable ISO bucket derived from the trigger (scheduled occurrence time) or
the floored evaluation window. A NotificationLog is keyed by this eventId so the
same event is never re-sent or re-logged.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional, Union

from .formatting import format_display_value
from .models import AlertEvent, AlertRule, MessageTemplate

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# Placeholders holding an ENGINE-COMPUTED current value (price / RSI / ratio /
# fx / gold / bitcoin / koreanEtf / vix). Only these are display-rounded; the
# user-authored {threshold}, {ticker} and {name} keep their literal text.
_COMPUTED_VALUE_KEYS = frozenset({"value"})


def make_event_id(rule_id: str, bucket: str) -> str:
    """Build the idempotency key. Mirrors ``${ruleId}:${bucketTime}``."""
    return f"{rule_id}:{bucket}"


def render_message(rule: AlertRule, variables: Optional[Dict[str, Any]] = None) -> MessageTemplate:
    """Render the rule's message template, substituting ``{key}`` placeholders.

    Known keys: {ticker} {value} {threshold} {name}. Unknown placeholders are
    left untouched (mirrors ``provider.ts::renderMessage``). Falls back to the
    rule name for both title and body when no template is set.

    {value} — and only {value} — is display-rounded to at most two decimals by
    :func:`alert_engine.formatting.format_display_value`. This is the single
    shared rendering layer for every channel, so Telegram and Push always show
    the same number. The evaluated value itself is untouched: comparison,
    ``lastValue`` and the Firestore payload keep full precision.
    """
    template = rule.delivery.message if rule.delivery and rule.delivery.message else MessageTemplate(rule.name, rule.name)
    merged: Dict[str, Any] = {"name": rule.name}
    if variables:
        merged.update({k: v for k, v in variables.items() if v is not None})

    def substitute(text: str) -> str:
        def repl(match: "re.Match[str]") -> str:
            key = match.group(1)
            if key not in merged:
                return match.group(0)
            if key in _COMPUTED_VALUE_KEYS:
                return format_display_value(merged[key])
            return str(merged[key])

        return _PLACEHOLDER_RE.sub(repl, text or "")

    return MessageTemplate(title=substitute(template.title), body=substitute(template.body))


def build_event(
    rule: AlertRule,
    uid: str,
    event_id: str,
    evaluated_at: datetime,
    fired_at: datetime,
    value: Optional[Union[float, str]] = None,
    message: Optional[MessageTemplate] = None,
    priority: Optional[str] = None,
    severity: Optional[str] = None,
) -> AlertEvent:
    """Assemble an AlertEvent. ``sentAt`` is stamped later by delivery."""
    return AlertEvent(
        eventId=event_id,
        ruleId=rule.id,
        uid=uid,
        kind=rule.kind,
        message=message or render_message(rule),
        evaluatedAt=evaluated_at.isoformat(),
        firedAt=fired_at.isoformat(),
        value=value,
        priority=priority,
        severity=severity,
    )
