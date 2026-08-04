"""Display-only two-decimal formatting of price / RSI / ratio values.

Covers the whole contract of the change:

- the shared formatter itself (rounding, sign, zero, non-finite, non-numeric),
- the single rendering layer (``render_message``) that both Telegram and Push
  consume, so the two channels can never disagree,
- and the strict separation between what a user READS and what the engine
  COMPUTES / COMPARES / PERSISTS (full precision everywhere).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alert_engine.engine import STATUS_DELIVERED, STATUS_NOT_TRIGGERED
from alert_engine.event import render_message
from alert_engine.formatting import format_display_value
from alert_engine.models import AlertRule

from .conftest import FakeDataSource, FakeFirestore, build_engine, make_metric_rule


NOW = datetime(2024, 5, 1, 3, 0, tzinfo=timezone.utc)


class RecordingChannel:
    """Delivery channel that captures the exact MessageTemplate it received."""

    def __init__(self, name: str):
        self.name = name
        self.messages = []

    def send(self, message, settings, **kwargs):
        from alert_engine.channels.base import ChannelSendResult

        self.messages.append(message)
        return ChannelSendResult(self.name, "sent")


def _ratio_rule(threshold: float = 1.0, comparator: str = "gte") -> AlertRule:
    return AlertRule.from_dict({
        "id": "rule-ratio-display",
        "uid": "u1",
        "kind": "ratio",
        "name": "TQQQ 전환비",
        "enabled": True,
        "condition": {
            "kind": "ratio",
            "numerator": "TQQQ",
            "denominator": "QQQ",
            "comparator": comparator,
            "threshold": threshold,
        },
        "trigger": {"mode": "recurring"},
        "delivery": {
            "channels": ["telegram", "push"],
            "message": {"title": "TQQQ 전환비", "body": "TQQQ 전환비 {value}"},
        },
    })


def _deliver(rule: AlertRule, *, metric=None, ratio=None):
    """Run one delivery and return (telegram, push, firestore) recordings."""
    datasource = FakeDataSource(metric=metric, ratio=ratio)
    firestore = FakeFirestore()
    channels = {"telegram": RecordingChannel("telegram"), "push": RecordingChannel("push")}
    engine = build_engine(datasource, firestore, channels)
    result = engine.process_rule(rule, now=NOW)
    return result, channels, firestore


# --- formatter unit tests ----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # A. price
        (33.470001220703125, "33.47"),
        (33.560001373291016, "33.56"),
        # B. RSI
        (41.74344832756071, "41.74"),
        # C./D. conversion ratio (round half up on the decimal the user sees)
        (2.019557271418028, "2.02"),
        (2.0272990795576558, "2.03"),
        (2.025029687233474, "2.03"),
        (13.884672, "13.88"),
        # E. negatives keep their sign; zero stays zero
        (-2.019557271418028, "-2.02"),
        (-0.005001, "-0.01"),
        (0.0, "0.0"),
        (-0.0001, "0.00"),
        # values already within two decimals are rendered exactly as before
        (30.0, "30.0"),
        (2.5, "2.5"),
        (18.0, "18.0"),
        (0.07, "0.07"),
        (42, "42"),
        (-7, "-7"),
    ],
)
def test_format_display_value(raw, expected):
    assert format_display_value(raw) == expected


def test_format_display_value_leaves_non_numeric_untouched():
    assert format_display_value("2024-05-01") == "2024-05-01"
    assert format_display_value(None) == "None"
    assert format_display_value(True) == "True"


def test_format_display_value_keeps_non_finite_rendering():
    # Requirement 8: never crash, never coerce a bad value into "0.00".
    assert format_display_value(float("nan")) == "nan"
    assert format_display_value(float("inf")) == "inf"
    assert format_display_value(float("-inf")) == "-inf"


def test_format_display_value_never_mutates_the_input():
    raw = 33.470001220703125
    format_display_value(raw)
    assert raw == 33.470001220703125


# --- rendering layer ---------------------------------------------------------


def test_render_message_formats_value_but_not_threshold_or_text():
    rule = AlertRule.from_dict({
        "id": "r", "uid": "u1", "kind": "price", "name": "SCHD 3.5% 룰",
        "enabled": True,
        "condition": {
            "kind": "price",
            "metric": {"metric": "price", "ticker": "SCHD"},
            "comparator": "gte",
            "threshold": 18.0,
        },
        "trigger": {"mode": "recurring"},
        "delivery": {
            "channels": ["telegram"],
            "message": {
                "title": "{ticker} 종가",
                "body": "{ticker} 종가 {value} (기준 gte {threshold}, 3.5% / 2024-05-01)",
            },
        },
    })
    message = render_message(rule, {
        "ticker": "SCHD",
        "value": 33.470001220703125,
        "threshold": 18.0,
        "name": rule.name,
    })
    assert message.title == "SCHD 종가"
    # value rounded; threshold, percentage, date and ticker untouched.
    assert message.body == "SCHD 종가 33.47 (기준 gte 18.0, 3.5% / 2024-05-01)"


def test_render_message_leaves_user_authored_numbers_alone():
    rule = AlertRule.from_dict({
        "id": "r", "uid": "u1", "kind": "price", "name": "룰 2.019557271418028",
        "enabled": True,
        "condition": {
            "kind": "price",
            "metric": {"metric": "price", "ticker": "SPY500"},
            "comparator": "gte",
            "threshold": 1.23456789,
        },
        "trigger": {"mode": "recurring"},
        "delivery": {
            "channels": ["telegram"],
            "message": {
                "title": "{name}",
                "body": "SPY500 3.14159265 id=550e8400-e29b 07:30:15 thr={threshold}",
            },
        },
    })
    message = render_message(rule, {"value": 2.019557271418028, "threshold": 1.23456789})
    assert message.title == "룰 2.019557271418028"
    assert message.body == "SPY500 3.14159265 id=550e8400-e29b 07:30:15 thr=1.23456789"


def test_render_message_handles_missing_and_non_finite_values():
    rule = make_metric_rule(metric="price", ticker="SCHD")
    # value=None is dropped by render_message -> placeholder stays literal.
    assert render_message(rule, {"value": None}).body == "{value}"
    assert render_message(rule, {"value": float("nan")}).body == "nan"


# --- end-to-end: channel parity + precision preservation ---------------------


def test_price_alert_shows_two_decimals_on_both_channels():
    """A. price 33.470001220703125 -> "33.47" on Telegram AND Push."""
    rule = make_metric_rule(
        rule_id="rule-price", metric="price", ticker="SCHD",
        comparator="gte", threshold=18.0, channels=["telegram", "push"],
    )
    result, channels, firestore = _deliver(rule, metric=33.470001220703125)

    assert result.status == STATUS_DELIVERED
    assert channels["telegram"].messages[0].body == "33.47"
    # H. channel parity
    assert channels["push"].messages[0].body == channels["telegram"].messages[0].body
    assert channels["push"].messages[0].title == channels["telegram"].messages[0].title

    # G. the durable payload keeps the full provider precision.
    log = next(iter(firestore.logs.values()))
    assert log.evaluatedValue == 33.470001220703125
    assert log.message.body == "33.47"
    assert result.value == 33.470001220703125
    assert firestore.state_updates[-1]["last_value"] == 33.470001220703125


def test_rsi_alert_shows_two_decimals():
    """B. RSI 41.74344832756071 -> "41.74"."""
    rule = make_metric_rule(
        rule_id="rule-rsi", metric="rsi", ticker="KOSPI",
        comparator="gte", threshold=40.0, channels=["telegram", "push"],
    )
    result, channels, firestore = _deliver(rule, metric=41.74344832756071)

    assert result.status == STATUS_DELIVERED
    assert channels["telegram"].messages[0].body == "41.74"
    assert channels["push"].messages[0].body == "41.74"
    log = next(iter(firestore.logs.values()))
    assert log.evaluatedValue == 41.74344832756071


@pytest.mark.parametrize(
    "raw,expected",
    [
        (2.019557271418028, "TQQQ 전환비 2.02"),   # C.
        (2.0272990795576558, "TQQQ 전환비 2.03"),  # D.
        (2.025029687233474, "TQQQ 전환비 2.03"),
    ],
)
def test_ratio_alert_shows_two_decimals(raw, expected):
    result, channels, firestore = _deliver(_ratio_rule(), ratio=raw)

    assert result.status == STATUS_DELIVERED
    assert channels["telegram"].messages[0].body == expected
    assert channels["push"].messages[0].body == expected
    log = next(iter(firestore.logs.values()))
    assert log.evaluatedValue == raw


def test_negative_and_zero_ratio_values_keep_message_structure():
    """E. sign preserved, zero handled, template structure unchanged."""
    _, channels, _ = _deliver(_ratio_rule(threshold=0.0, comparator="lte"), ratio=-2.019557271418028)
    assert channels["telegram"].messages[0].body == "TQQQ 전환비 -2.02"

    _, channels, firestore = _deliver(_ratio_rule(threshold=0.0, comparator="lte"), ratio=0.0)
    assert channels["telegram"].messages[0].body == "TQQQ 전환비 0.0"
    assert next(iter(firestore.logs.values())).evaluatedValue == 0.0


# --- F. display rounding must never move a condition boundary ----------------


def test_condition_precision_is_independent_of_display_rounding():
    triggered_rule = make_metric_rule(
        rule_id="rule-above", metric="price", ticker="SCHD",
        comparator="gte", threshold=18.0, channels=["telegram", "push"],
    )
    result_true, channels, firestore = _deliver(triggered_rule, metric=18.001)
    assert result_true.status == STATUS_DELIVERED
    assert channels["telegram"].messages[0].body == "18.00"
    assert next(iter(firestore.logs.values())).evaluatedValue == 18.001

    false_rule = make_metric_rule(
        rule_id="rule-below", metric="price", ticker="SCHD",
        comparator="gte", threshold=18.0, channels=["telegram", "push"],
    )
    result_false, channels_false, firestore_false = _deliver(false_rule, metric=17.999)
    # Identical display text, opposite condition outcome, no delivery.
    assert result_false.status == STATUS_NOT_TRIGGERED
    assert result_false.value == 17.999
    assert channels_false["telegram"].messages == []
    assert firestore_false.logs == {}

    # Both sides of the threshold read "18.00" while comparison used the raw
    # values — display rounding is fully decoupled from the comparator.
    assert format_display_value(18.001) == format_display_value(17.999) == "18.00"


def test_telegram_and_push_payloads_carry_the_same_rounded_text(monkeypatch):
    """Channel-level payload assertion: Telegram `text` and the FCM data map."""
    import sys
    from types import ModuleType, SimpleNamespace

    from alert_engine.channels.push import PushChannel
    from alert_engine.channels.telegram import TelegramChannel
    from alert_engine.models import AlertSettings

    rule = _ratio_rule()
    message = render_message(rule, {"value": 2.0272990795576558, "threshold": 1.0})
    settings = AlertSettings(globalEnabled=True, telegramChatId="chat-1", pushTokens=["tok-1"])

    # --- Telegram ---
    sent_payloads = []
    fake_requests = ModuleType("requests")

    def fake_post(url, json=None, timeout=None):
        sent_payloads.append(json)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"ok": True, "result": {"message_id": 7}},
            text="",
        )

    fake_requests.post = fake_post
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    telegram_result = TelegramChannel().send(message, settings)
    assert telegram_result.status == "sent"
    assert sent_payloads[0]["text"] == "TQQQ 전환비\nTQQQ 전환비 2.03"

    # --- Push (FCM data-only payload) ---
    multicasts = []
    messaging = ModuleType("firebase_admin.messaging")
    messaging.MulticastMessage = lambda **kwargs: multicasts.append(kwargs) or SimpleNamespace(**kwargs)
    messaging.WebpushConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    messaging.send_each_for_multicast = lambda multicast: SimpleNamespace(
        success_count=1,
        failure_count=0,
        responses=[SimpleNamespace(success=True, message_id="m-1", exception=None)],
    )
    firebase_admin = ModuleType("firebase_admin")
    firebase_admin.messaging = messaging
    monkeypatch.setitem(sys.modules, "firebase_admin", firebase_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.messaging", messaging)
    monkeypatch.setattr("alert_engine.firestore_client._init_firebase", lambda: None)

    push_result = PushChannel().send(message, settings)
    assert push_result.status == "sent"
    assert multicasts[0]["data"]["title"] == "TQQQ 전환비"
    assert multicasts[0]["data"]["body"] == "TQQQ 전환비 2.03"
    # H. the human-readable number is byte-identical across channels.
    assert multicasts[0]["data"]["body"] in sent_payloads[0]["text"]


def test_threshold_and_status_are_untouched_by_display_formatting():
    rule = _ratio_rule(threshold=2.0123456789)
    result, _, firestore = _deliver(rule, ratio=2.019557271418028)

    assert result.status == STATUS_DELIVERED
    # G. threshold on the rule object is not rewritten by rendering.
    assert rule.condition.threshold == 2.0123456789
    log = next(iter(firestore.logs.values()))
    assert log.status == "sent"
    assert log.evaluationStatus == "triggered"
    assert log.evaluatedValue == 2.019557271418028
