"""Dataclasses mirroring the TS shapes in ``lib/alerts/types.ts`` EXACTLY.

All field names are camelCase so the Python engine and the Next.js web app
interoperate on the SAME Firestore documents. Parsing from Firestore dicts is
tolerant: unknown/extra keys are ignored, missing optionals default sensibly,
and malformed nested data degrades gracefully rather than raising.

Use ``AlertRule.from_dict`` / ``.to_dict`` and ``NotificationLog.to_dict`` for
round-tripping. ``to_dict`` omits ``None`` values so we never write nulls that
would clobber web-app-managed fields on merge writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

# --- literal sets (kept loose; validated by the web app) ---------------------

ALERT_KINDS = (
    "date", "ratio", "dividend", "rsi", "vix", "price", "fx", "gold",
    "bitcoin", "koreanEtf", "custom", "composite",
)
METRIC_KINDS = ("rsi", "vix", "price", "fx", "gold", "bitcoin", "koreanEtf")
COMPARATORS = ("gt", "gte", "lt", "lte", "eq", "crossUp", "crossDown")
CALENDAR_MARKS = ("star", "heart")


def _drop_none(data: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively drop keys whose value is None (merge-write friendly)."""
    cleaned: Dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, dict):
            cleaned[key] = _drop_none(value)
        else:
            cleaned[key] = value
    return cleaned


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


# --- MetricId ----------------------------------------------------------------


@dataclass
class MetricId:
    """Identifies a single measurable market metric (mirrors TS MetricId union)."""

    metric: str
    ticker: Optional[str] = None
    period: Optional[int] = None
    pair: Optional[str] = None
    code: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["MetricId"]:
        if not isinstance(data, dict):
            return None
        metric = _as_str(data.get("metric"))
        if not metric:
            return None
        period = data.get("period")
        try:
            period = int(period) if period is not None else None
        except (TypeError, ValueError):
            period = None
        return cls(
            metric=metric,
            ticker=_as_str(data.get("ticker")),
            period=period,
            pair=_as_str(data.get("pair")),
            code=_as_str(data.get("code")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return _drop_none({
            "metric": self.metric,
            "ticker": self.ticker,
            "period": self.period,
            "pair": self.pair,
            "code": self.code,
        })


# --- DateEventSelector -------------------------------------------------------


@dataclass
class DateEventSelector:
    source: str = "calendarEvents"
    match: Optional[Dict[str, Any]] = None
    markFilter: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["DateEventSelector"]:
        if not isinstance(data, dict):
            return None
        match = data.get("match") if isinstance(data.get("match"), dict) else None
        mark_filter = data.get("markFilter")
        if isinstance(mark_filter, list):
            mark_filter = [m for m in mark_filter if m in CALENDAR_MARKS]
        else:
            mark_filter = None
        return cls(
            source=_as_str(data.get("source")) or "calendarEvents",
            match=match,
            markFilter=mark_filter,
        )

    def to_dict(self) -> Dict[str, Any]:
        return _drop_none({
            "source": self.source,
            "match": self.match,
            "markFilter": self.markFilter,
        })


# --- Condition ---------------------------------------------------------------


@dataclass
class Condition:
    """Tolerant representation of the TS Condition union (keyed by ``kind``).

    We keep a single flexible dataclass rather than N subclasses so tolerant
    parsing is trivial; evaluators read only the fields relevant to their kind.
    """

    kind: str
    # ratio
    numerator: Optional[str] = None
    denominator: Optional[str] = None
    # metric / ratio / dividend comparator
    comparator: Optional[str] = None
    threshold: Optional[float] = None
    # dividend
    ticker: Optional[str] = None
    # metric
    metric: Optional[MetricId] = None
    # date / dividend selector
    selector: Optional[DateEventSelector] = None
    # custom
    expression: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    # composite
    operator: Optional[str] = None
    conditions: List["Condition"] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["Condition"]:
        if not isinstance(data, dict):
            return None
        kind = _as_str(data.get("kind"))
        if not kind:
            return None
        children: List["Condition"] = []
        raw_children = data.get("conditions")
        if isinstance(raw_children, list):
            for raw in raw_children:
                child = cls.from_dict(raw)
                if child is not None:
                    children.append(child)
        return cls(
            kind=kind,
            numerator=_as_str(data.get("numerator")),
            denominator=_as_str(data.get("denominator")),
            comparator=_as_str(data.get("comparator")),
            threshold=_as_float(data.get("threshold")),
            ticker=_as_str(data.get("ticker")),
            metric=MetricId.from_dict(data.get("metric")),
            selector=DateEventSelector.from_dict(data.get("selector")),
            expression=_as_str(data.get("expression")),
            params=data.get("params") if isinstance(data.get("params"), dict) else None,
            operator=_as_str(data.get("operator")),
            conditions=children,
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": self.kind}
        if self.numerator is not None:
            out["numerator"] = self.numerator
        if self.denominator is not None:
            out["denominator"] = self.denominator
        if self.comparator is not None:
            out["comparator"] = self.comparator
        if self.threshold is not None:
            out["threshold"] = self.threshold
        if self.ticker is not None:
            out["ticker"] = self.ticker
        if self.metric is not None:
            out["metric"] = self.metric.to_dict()
        if self.selector is not None:
            out["selector"] = self.selector.to_dict()
        if self.expression is not None:
            out["expression"] = self.expression
        if self.params is not None:
            out["params"] = self.params
        if self.operator is not None:
            out["operator"] = self.operator
        if self.conditions:
            out["conditions"] = [c.to_dict() for c in self.conditions]
        return out


# --- Trigger policy ----------------------------------------------------------


@dataclass
class QuietHours:
    start: str
    end: str
    tz: str

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["QuietHours"]:
        if not isinstance(data, dict):
            return None
        start = _as_str(data.get("start"))
        end = _as_str(data.get("end"))
        if start is None or end is None:
            return None
        return cls(start=start, end=end, tz=_as_str(data.get("tz")) or "Asia/Seoul")

    def to_dict(self) -> Dict[str, Any]:
        return {"start": self.start, "end": self.end, "tz": self.tz}


@dataclass
class Recurrence:
    kind: str
    weekday: Optional[int] = None
    time: Optional[str] = None
    tz: Optional[str] = None
    anchorDate: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["Recurrence"]:
        if not isinstance(data, dict):
            return None
        kind = _as_str(data.get("kind"))
        if not kind:
            return None
        weekday = data.get("weekday")
        try:
            weekday = int(weekday) if weekday is not None else None
        except (TypeError, ValueError):
            weekday = None
        return cls(
            kind=kind,
            weekday=weekday,
            time=_as_str(data.get("time")),
            tz=_as_str(data.get("tz")),
            anchorDate=_as_str(data.get("anchorDate")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return _drop_none({
            "kind": self.kind,
            "weekday": self.weekday,
            "time": self.time,
            "tz": self.tz,
            "anchorDate": self.anchorDate,
        })


@dataclass
class TriggerPolicy:
    mode: str = "recurring"
    cooldownMinutes: Optional[int] = None
    quietHours: Optional[QuietHours] = None
    recurrence: Optional[Recurrence] = None

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "TriggerPolicy":
        if not isinstance(data, dict):
            return cls()
        cooldown = data.get("cooldownMinutes")
        try:
            cooldown = int(cooldown) if cooldown is not None else None
        except (TypeError, ValueError):
            cooldown = None
        return cls(
            mode=_as_str(data.get("mode")) or "recurring",
            cooldownMinutes=cooldown,
            quietHours=QuietHours.from_dict(data.get("quietHours")),
            recurrence=Recurrence.from_dict(data.get("recurrence")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return _drop_none({
            "mode": self.mode,
            "cooldownMinutes": self.cooldownMinutes,
            "quietHours": self.quietHours.to_dict() if self.quietHours else None,
            "recurrence": self.recurrence.to_dict() if self.recurrence else None,
        })


# --- Delivery ----------------------------------------------------------------


@dataclass
class MessageTemplate:
    title: str
    body: str

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["MessageTemplate"]:
        if not isinstance(data, dict):
            return None
        return cls(title=_as_str(data.get("title")) or "", body=_as_str(data.get("body")) or "")

    def to_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "body": self.body}


@dataclass
class DeliveryConfig:
    channels: List[str] = field(default_factory=list)
    message: Optional[MessageTemplate] = None

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DeliveryConfig":
        if not isinstance(data, dict):
            return cls()
        channels = data.get("channels")
        if isinstance(channels, list):
            channels = [c for c in channels if c in ("telegram", "push")]
        else:
            channels = []
        return cls(channels=channels, message=MessageTemplate.from_dict(data.get("message")))

    def to_dict(self) -> Dict[str, Any]:
        return _drop_none({
            "channels": self.channels,
            "message": self.message.to_dict() if self.message else None,
        })


# --- AlertRule ---------------------------------------------------------------


@dataclass
class AlertRule:
    id: str
    uid: str
    kind: str
    name: str
    enabled: bool
    condition: Optional[Condition]
    trigger: TriggerPolicy
    delivery: DeliveryConfig
    lastTriggeredAt: Optional[str] = None
    lastValue: Optional[Union[float, str]] = None
    ruleVersion: Optional[int] = None
    engineVersion: Optional[str] = None
    createdAt: Any = None
    updatedAt: Any = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AlertRule":
        rule_version = data.get("ruleVersion")
        try:
            rule_version = int(rule_version) if rule_version is not None else None
        except (TypeError, ValueError):
            rule_version = None
        last_value = data.get("lastValue")
        if last_value is not None and not isinstance(last_value, str):
            coerced = _as_float(last_value)
            last_value = coerced if coerced is not None else _as_str(last_value)
        return cls(
            id=_as_str(data.get("id")) or "",
            uid=_as_str(data.get("uid")) or "",
            kind=_as_str(data.get("kind")) or "",
            name=_as_str(data.get("name")) or "",
            enabled=bool(data.get("enabled", False)),
            condition=Condition.from_dict(data.get("condition")),
            trigger=TriggerPolicy.from_dict(data.get("trigger")),
            delivery=DeliveryConfig.from_dict(data.get("delivery")),
            lastTriggeredAt=_as_str(data.get("lastTriggeredAt")),
            lastValue=last_value,
            ruleVersion=rule_version,
            engineVersion=_as_str(data.get("engineVersion")),
            createdAt=data.get("createdAt"),
            updatedAt=data.get("updatedAt"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return _drop_none({
            "id": self.id,
            "uid": self.uid,
            "kind": self.kind,
            "name": self.name,
            "enabled": self.enabled,
            "condition": self.condition.to_dict() if self.condition else None,
            "trigger": self.trigger.to_dict(),
            "delivery": self.delivery.to_dict(),
            "lastTriggeredAt": self.lastTriggeredAt,
            "lastValue": self.lastValue,
            "ruleVersion": self.ruleVersion,
            "engineVersion": self.engineVersion,
        })


# --- AlertSettings -----------------------------------------------------------


@dataclass
class AlertSettings:
    globalEnabled: bool = True
    telegramChatId: Optional[str] = None
    pushTokens: List[str] = field(default_factory=list)
    defaultQuietHours: Optional[QuietHours] = None
    defaultAlertTime: Optional[str] = None
    defaultMessageTitle: Optional[str] = None
    defaultMessageBody: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "AlertSettings":
        if not isinstance(data, dict):
            return cls()
        tokens = data.get("pushTokens")
        if isinstance(tokens, list):
            tokens = [t for t in tokens if isinstance(t, str) and t.strip()]
        else:
            tokens = []
        return cls(
            globalEnabled=bool(data.get("globalEnabled", True)),
            telegramChatId=_as_str(data.get("telegramChatId")),
            pushTokens=tokens,
            defaultQuietHours=QuietHours.from_dict(data.get("defaultQuietHours")),
            defaultAlertTime=_as_str(data.get("defaultAlertTime")),
            defaultMessageTitle=_as_str(data.get("defaultMessageTitle")),
            defaultMessageBody=_as_str(data.get("defaultMessageBody")),
        )


# --- AlertEvent --------------------------------------------------------------


@dataclass
class AlertEvent:
    eventId: str
    ruleId: str
    uid: str
    kind: str
    message: MessageTemplate
    evaluatedAt: str
    firedAt: str
    value: Optional[Union[float, str]] = None
    sentAt: Optional[str] = None
    priority: Optional[str] = None
    severity: Optional[str] = None


# --- NotificationLog ---------------------------------------------------------


@dataclass
class ChannelResult:
    channel: str
    status: str  # "sent" | "failed"
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _drop_none({"channel": self.channel, "status": self.status, "error": self.error})


@dataclass
class NotificationLog:
    id: str
    eventId: str
    ruleId: str
    kind: str
    firedAt: str
    evaluatedAt: str
    message: MessageTemplate
    channels: List[ChannelResult]
    isTest: bool
    sentAt: Optional[str] = None
    evaluatedValue: Optional[Union[float, str]] = None
    priority: Optional[str] = None
    severity: Optional[str] = None
    ruleName: Optional[str] = None
    tickers: Optional[List[str]] = None
    createdAt: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return _drop_none({
            "id": self.id,
            "eventId": self.eventId,
            "ruleId": self.ruleId,
            "kind": self.kind,
            "firedAt": self.firedAt,
            "evaluatedAt": self.evaluatedAt,
            "sentAt": self.sentAt,
            "evaluatedValue": self.evaluatedValue,
            "message": self.message.to_dict(),
            "channels": [c.to_dict() for c in self.channels],
            "isTest": self.isTest,
            "priority": self.priority,
            "severity": self.severity,
            "ruleName": self.ruleName,
            "tickers": self.tickers,
        })
