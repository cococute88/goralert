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

from .models import AlertEvent, AlertRule, MessageTemplate

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def make_event_id(rule_id: str, bucket: str) -> str:
    """Build the idempotency key. Mirrors ``${ruleId}:${bucketTime}``."""
    return f"{rule_id}:{bucket}"


def render_message(rule: AlertRule, variables: Optional[Dict[str, Any]] = None) -> MessageTemplate:
    """Render the rule's message template, substituting ``{key}`` placeholders.

    Known keys: {ticker} {value} {threshold} {name}. Unknown placeholders are
    left untouched (mirrors ``provider.ts::renderMessage``). Falls back to the
    rule name for both title and body when no template is set.
    """
    template = rule.delivery.message if rule.delivery and rule.delivery.message else MessageTemplate(rule.name, rule.name)
    merged: Dict[str, Any] = {"name": rule.name}
    if variables:
        merged.update({k: v for k, v in variables.items() if v is not None})

    def substitute(text: str) -> str:
        def repl(match: "re.Match[str]") -> str:
            key = match.group(1)
            return str(merged[key]) if key in merged else match.group(0)

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
