"""Shared pytest fixtures + in-memory fakes for the alert-engine test suite.

NO NETWORK, NO firebase-admin, NO yfinance. Every collaborator the engine talks
to (data source, delivery channels, Firestore) is replaced by a deterministic
in-memory fake so the full pipeline can be exercised offline.

Env knobs are set BEFORE importing ``alert_engine`` so delivery never sleeps and
each channel is attempted exactly once (retries disabled) — this keeps the
delivery-isolation property ("each other channel called exactly once") clean and
makes the whole suite fast.
"""

from __future__ import annotations

import os

# --- must run before any alert_engine import (config reads these at import) ---
os.environ.setdefault("ALERT_DELIVERY_MAX_RETRIES", "0")
os.environ.setdefault("ALERT_DELIVERY_BACKOFF_BASE", "0")
os.environ.setdefault("ALERT_DELIVERY_BACKOFF_MAX", "0")
os.environ.setdefault("ALERT_EVAL_WINDOW_MINUTES", "15")
os.environ.setdefault("DEFAULT_TZ", "Asia/Seoul")

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

from alert_engine.channels.base import ChannelSendResult
from alert_engine.engine import AlertEngine
from alert_engine.evaluators import build_default_registry
from alert_engine.models import AlertRule, AlertSettings


# --- fakes -------------------------------------------------------------------


class FakeCalendarStore:
    """Holds calendar events + counts reads/writes.

    The engine treats calendar collections as READ-ONLY, so ``writes`` must stay
    zero after any evaluation path (Property 6). ``record_write`` exists only so
    a test can prove the engine never calls it.
    """

    def __init__(self, events: Optional[List[Dict[str, Any]]] = None):
        self.events: List[Dict[str, Any]] = list(events or [])
        self.reads = 0
        self.writes = 0

    def read(self) -> List[Dict[str, Any]]:
        self.reads += 1
        return list(self.events)

    def record_write(self, *args, **kwargs) -> None:  # pragma: no cover - must never be called
        self.writes += 1


class FakeDataSource:
    """In-memory AlertDataSource stand-in (matches the methods evaluators call).

    Configure a scalar ``metric`` / ``ratio`` / ``dividend`` value and a list of
    calendar ``events``. Reading calendar events is routed through an optional
    ``calendar_store`` so a test can assert it was read but never written.
    """

    def __init__(
        self,
        metric: Optional[float] = None,
        ratio: Optional[float] = None,
        dividend: Optional[float] = None,
        events: Optional[List[Dict[str, Any]]] = None,
        calendar_store: Optional[FakeCalendarStore] = None,
        now_fn=None,
    ):
        self._metric = metric
        self._ratio = ratio
        self._dividend = dividend
        self._calendar_store = calendar_store or FakeCalendarStore(events or [])
        self._now_fn = now_fn

    def now(self) -> datetime:
        return self._now_fn() if self._now_fn else datetime.now(timezone.utc)

    def get_metric(self, metric):
        return self._metric

    def get_ratio(self, numerator, denominator):
        return self._ratio

    def get_dividend_metric(self, ticker):
        return self._dividend

    def get_calendar_events(self, uid, selector, firestore=None):
        # READ ONLY — never mutates the store.
        return self._calendar_store.read()

    @property
    def calendar_store(self) -> FakeCalendarStore:
        return self._calendar_store


class FakeChannel:
    """Configurable delivery channel with a call counter (DeliveryChannel impl).

    - status="sent"/"failed" controls the returned result.
    - raises=True makes ``send`` throw (delivery must isolate it).
    - ``calls`` counts how many times ``send`` was invoked (exactly-once checks).
    - error defaults to a "permanent" string so process_rule delivery never
      retries (kept fast + exactly-once even when retries are enabled).
    """

    def __init__(
        self,
        name: str,
        status: str = "sent",
        error: Optional[str] = "not configured",
        raises: bool = False,
        invalid_tokens: Optional[List[str]] = None,
    ):
        self.name = name
        self._status = status
        self._error = None if status == "sent" else error
        self._raises = raises
        self._invalid_tokens = list(invalid_tokens or [])
        self.calls = 0

    def send(self, message, settings, **kwargs) -> ChannelSendResult:
        self.calls += 1
        if self._raises:
            raise RuntimeError(f"{self.name} exploded")
        return ChannelSendResult(
            self.name,
            self._status,
            error=self._error,
            invalid_tokens=list(self._invalid_tokens),
        )


class FakeFirestore:
    """In-memory Firestore matching the firestore_client surface the engine uses.

    Records every write so tests can assert idempotency / log preservation /
    state mutations. Calendar collections have NO write methods (read-only by
    design); ``calendar_writes`` stays 0 and proves Property 6 at this layer.
    """

    def __init__(self, settings: Optional[AlertSettings] = None, calendar_store: Optional[FakeCalendarStore] = None):
        self.logs: Dict[str, Any] = {}
        self.state_updates: List[Dict[str, Any]] = []
        self.calendar_writes = 0
        self._settings = settings or AlertSettings(
            globalEnabled=True, telegramChatId="chat-123", pushTokens=["tok-1"]
        )
        self._calendar_store = calendar_store or FakeCalendarStore()

    # settings / idempotency / writes -----------------------------------------
    def load_alert_settings(self, uid: str) -> AlertSettings:
        return self._settings

    def log_exists(self, uid: str, event_id: str) -> bool:
        return event_id in self.logs

    def reserve_log(self, uid: str, event_id: str) -> bool:
        """Atomic reserve-before-send fake: create-if-absent.

        Returns True (reserved) only the first time an eventId is seen; a
        placeholder is stored so a concurrent/repeat call returns False. The
        real log overwrites the placeholder via ``write_notification_log``.
        """
        if event_id in self.logs:
            return False
        self.logs[event_id] = None  # reservation placeholder
        return True

    def write_notification_log(self, uid: str, log) -> None:
        self.logs[log.id] = log

    def update_rule_state(
        self,
        uid: str,
        rule_id: str,
        last_triggered_at: Optional[str] = None,
        last_value: Optional[Any] = None,
        enabled: Optional[bool] = None,
        engine_version: Optional[str] = None,
    ) -> None:
        self.state_updates.append({
            "rule_id": rule_id,
            "last_triggered_at": last_triggered_at,
            "last_value": last_value,
            "enabled": enabled,
            "engine_version": engine_version,
        })

    # calendar (READ-ONLY) -----------------------------------------------------
    def read_calendar_events(self, uid: str):
        return self._calendar_store.read()

    def read_calendar_custom_events(self, uid: str):
        return self._calendar_store.read()


# --- rule factories ----------------------------------------------------------


def make_ratio_rule(
    rule_id: str = "rule-ratio",
    threshold: float = 25.0,
    comparator: str = "gte",
    channels: Optional[List[str]] = None,
    cooldown_minutes: Optional[int] = None,
    mode: str = "recurring",
    last_triggered_at: Optional[str] = None,
) -> AlertRule:
    return AlertRule.from_dict({
        "id": rule_id,
        "uid": "u1",
        "kind": "ratio",
        "name": "SPY/SCHD",
        "enabled": True,
        "condition": {
            "kind": "ratio",
            "numerator": "SPY",
            "denominator": "SCHD",
            "comparator": comparator,
            "threshold": threshold,
        },
        "trigger": {"mode": mode, "cooldownMinutes": cooldown_minutes},
        "delivery": {
            "channels": channels or ["telegram", "push"],
            "message": {"title": "ratio hit", "body": "value={value} thr={threshold}"},
        },
        "lastTriggeredAt": last_triggered_at,
    })


def make_metric_rule(
    rule_id: str = "rule-rsi",
    metric: str = "rsi",
    ticker: str = "KOSPI",
    period: int = 14,
    comparator: str = "gte",
    threshold: float = 70.0,
    channels: Optional[List[str]] = None,
) -> AlertRule:
    return AlertRule.from_dict({
        "id": rule_id,
        "uid": "u1",
        "kind": metric,
        "name": f"{metric} alert",
        "enabled": True,
        "condition": {
            "kind": metric,
            "metric": {"metric": metric, "ticker": ticker, "period": period},
            "comparator": comparator,
            "threshold": threshold,
        },
        "trigger": {"mode": "recurring"},
        "delivery": {"channels": channels or ["telegram"], "message": {"title": "m", "body": "{value}"}},
    })


def make_calendar_date_rule(rule_id: str = "rule-cal", mark_filter: Optional[List[str]] = None) -> AlertRule:
    return AlertRule.from_dict({
        "id": rule_id,
        "uid": "u1",
        "kind": "date",
        "name": "ex-div calendar",
        "enabled": True,
        "condition": {
            "kind": "date",
            "selector": {"source": "calendarEvents", "markFilter": mark_filter},
        },
        "trigger": {"mode": "recurring", "recurrence": {"kind": "calendar"}},
        "delivery": {"channels": ["telegram"], "message": {"title": "cal", "body": "{ticker}"}},
    })


def make_custom_rule(rule_id: str, expression: str) -> AlertRule:
    return AlertRule.from_dict({
        "id": rule_id,
        "uid": "u1",
        "kind": "custom",
        "name": "custom",
        "enabled": True,
        "condition": {"kind": "custom", "expression": expression},
        "trigger": {"mode": "recurring"},
        "delivery": {"channels": ["telegram"], "message": {"title": "c", "body": "c"}},
    })


def build_engine(datasource, firestore, channels: Dict[str, FakeChannel]) -> AlertEngine:
    """Wire an AlertEngine entirely from fakes (no real collaborators)."""
    return AlertEngine(
        datasource=datasource,
        evaluator_registry=build_default_registry(datasource),
        channel_registry=channels,
        firestore=firestore,
    )


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def now_utc() -> datetime:
    return datetime(2024, 5, 1, 3, 0, tzinfo=timezone.utc)  # 12:00 KST


@pytest.fixture
def fake_channels():
    return {"telegram": FakeChannel("telegram"), "push": FakeChannel("push")}


@pytest.fixture
def fake_firestore():
    return FakeFirestore()


@pytest.fixture
def make_engine():
    return build_engine
