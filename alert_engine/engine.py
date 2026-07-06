"""AlertEngine: orchestrates evaluation -> gating -> delivery -> persistence.

process_rule pipeline (in order):
  1. enabled check (rule.enabled + settings.globalEnabled)
  2. recurrence "due now" gate (scheduled cadences; calendar cadence is gated by
     the date evaluator against calendar data instead)
  3. evaluate condition via the evaluator registry
  4. not triggered -> NO Firestore write (REQ-027.3), except crossUp/crossDown
     comparators which must persist lastValue to detect a future crossing
  5. quiet-hours gate
  6. cooldown gate (lastTriggeredAt + cooldownMinutes)
  7. eventId idempotency via reserve-before-send: atomically create
     notificationLogs/{eventId} (create() fails if it already exists) so two
     overlapping runs in the same bucket can never BOTH deliver
  8. render message, fan-out delivery (retry/backoff, isolation), stamp
     evaluatedAt/sentAt + engineVersion
  9. finalize the reserved NotificationLog (EXACTLY ONE)
 10. update rule state (lastTriggeredAt/lastValue/engineVersion);
     mode=="once" -> disable the rule

The engine is stateless: all durable state lives in Firestore. Inject
collaborators (datasource/registries/firestore) for tests; defaults wire the
real implementations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from .config import EngineConfig, load_config
from .datasource import AlertDataSource
from .delivery import deliver
from .event import build_event, make_event_id, render_message
from .evaluators import build_default_registry
from .evaluators.base import EvalContext
from .channels import build_default_channels
from .models import (
    AlertRule,
    AlertSettings,
    ChannelResult,
    NotificationLog,
)
from .recurrence import bucket_time, due_now, get_tz, parse_hh_mm

logger = logging.getLogger("alert_engine.engine")


@dataclass
class ProcessResult:
    """Outcome of processing one rule (for structured run logging)."""

    rule_id: str
    uid: str
    status: str  # see STATUS_* below
    detail: Optional[str] = None
    value: Optional[Any] = None
    event_id: Optional[str] = None
    log: Optional[NotificationLog] = None


STATUS_DISABLED = "skipped_disabled"
STATUS_NOT_DUE = "not_due"
STATUS_NOT_TRIGGERED = "not_triggered"
STATUS_QUIET_HOURS = "skipped_quiet_hours"
STATUS_COOLDOWN = "skipped_cooldown"
STATUS_DUPLICATE = "skipped_duplicate"
STATUS_DELIVERED = "delivered"
STATUS_DRY_RUN = "dry_run"
STATUS_ERROR = "error"


def _within_quiet_hours(quiet, now: datetime) -> bool:
    """True when ``now`` (in the quiet-hours tz) falls inside [start, end).

    Handles wrap-around windows (e.g. 22:00-07:00). When start == end the
    window is treated as empty (never quiet).
    """
    if quiet is None:
        return False
    tz = get_tz(quiet.tz)
    local = now.astimezone(tz)
    sh, sm = parse_hh_mm(quiet.start)
    eh, em = parse_hh_mm(quiet.end)
    start_minutes = sh * 60 + sm
    end_minutes = eh * 60 + em
    cur = local.hour * 60 + local.minute
    if start_minutes == end_minutes:
        return False
    if start_minutes < end_minutes:
        return start_minutes <= cur < end_minutes
    # Wrap-around (overnight) window.
    return cur >= start_minutes or cur < end_minutes


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


class AlertEngine:
    def __init__(
        self,
        datasource: Optional[AlertDataSource] = None,
        evaluator_registry: Optional[dict] = None,
        channel_registry: Optional[dict] = None,
        firestore=None,
        config: Optional[EngineConfig] = None,
    ):
        self.datasource = datasource or AlertDataSource()
        self.evaluators = evaluator_registry or build_default_registry(self.datasource)
        self.channels = channel_registry or build_default_channels()
        self.config = config or load_config()
        if firestore is None:
            from . import firestore_client as firestore  # lazy import
        self.firestore = firestore

    # --- main pipeline -------------------------------------------------------

    def process_rule(
        self,
        rule: AlertRule,
        now: Optional[datetime] = None,
        settings: Optional[AlertSettings] = None,
        dry_run: bool = False,
    ) -> ProcessResult:
        now = now or datetime.now(timezone.utc)

        # 1. enabled + globalEnabled
        if not rule.enabled:
            return ProcessResult(rule.id, rule.uid, STATUS_DISABLED, "rule disabled")
        if settings is None:
            try:
                settings = self.firestore.load_alert_settings(rule.uid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("settings load failed for uid=%s (%s); defaulting", rule.uid, exc)
                settings = AlertSettings()
        if not settings.globalEnabled:
            return ProcessResult(rule.id, rule.uid, STATUS_DISABLED, "globalEnabled=false")

        trigger = rule.trigger
        recurrence = trigger.recurrence if trigger else None

        # 2. recurrence "due now" gate (skip for calendar-driven / no recurrence)
        if recurrence is not None and recurrence.kind not in ("calendar", None):
            if not due_now(recurrence, now, self.config.eval_window_minutes):
                return ProcessResult(rule.id, rule.uid, STATUS_NOT_DUE, "recurrence not due")

        # 3. evaluate
        if rule.condition is None:
            return ProcessResult(rule.id, rule.uid, STATUS_ERROR, "rule has no condition")
        evaluator = self.evaluators.get(rule.condition.kind)
        if evaluator is None:
            return ProcessResult(rule.id, rule.uid, STATUS_ERROR, f"no evaluator for kind={rule.condition.kind}")

        prev_value = rule.lastValue if isinstance(rule.lastValue, (int, float)) else None
        ctx = EvalContext(uid=rule.uid, now=now, prev_value=prev_value, settings=settings)
        eval_result = evaluator.evaluate(rule, rule.condition, ctx)
        logger.info("rule=%s eval: %s", rule.id, eval_result.detail)

        # 4. not triggered.
        #    REQ-027.3: do NOT write Firestore for non-triggered rules. The ONLY
        #    exception is crossUp/crossDown comparators, which need the previous
        #    observed value persisted to detect a future crossing — without it
        #    cross detection is impossible. Every other comparator performs ZERO
        #    writes here, saving Firestore write quota on every poll cycle.
        if not eval_result.triggered:
            if (
                not dry_run
                and eval_result.value is not None
                and self._needs_prev_value(rule.condition)
            ):
                try:
                    self.firestore.update_rule_state(
                        rule.uid, rule.id, last_value=eval_result.value,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("lastValue update failed rule=%s (%s)", rule.id, exc)
            return ProcessResult(rule.id, rule.uid, STATUS_NOT_TRIGGERED, eval_result.detail, eval_result.value)

        # 5. quiet hours
        if _within_quiet_hours(trigger.quietHours if trigger else None, now):
            return ProcessResult(rule.id, rule.uid, STATUS_QUIET_HOURS, "within quiet hours", eval_result.value)

        # 6. cooldown
        if trigger and trigger.cooldownMinutes:
            last = _parse_iso(rule.lastTriggeredAt)
            if last is not None and now - last < timedelta(minutes=trigger.cooldownMinutes):
                return ProcessResult(rule.id, rule.uid, STATUS_COOLDOWN, "within cooldown", eval_result.value)

        # 7. compute idempotency key (eventId = ruleId:bucketTime)
        bucket = bucket_time(now, trigger, self.config.eval_window_minutes)
        event_id = make_event_id(rule.id, bucket)

        # 8. render + build event
        variables = {
            "ticker": ctx.extra.get("ticker") or self._primary_ticker(rule),
            "value": eval_result.value,
            "threshold": rule.condition.threshold,
            "name": rule.name,
        }
        message = render_message(rule, variables)
        evaluated_at = now
        event = build_event(rule, rule.uid, event_id, evaluated_at, now, value=eval_result.value, message=message)

        if dry_run:
            # dry-run performs NO writes, hence NO reservation — report only.
            return ProcessResult(rule.id, rule.uid, STATUS_DRY_RUN, eval_result.detail, eval_result.value, event_id)

        # 8a. reserve-before-send: atomically create notificationLogs/{eventId}.
        #     create() fails if the doc already exists, so two overlapping runs
        #     that reach the same bucket cannot BOTH proceed to deliver — exactly
        #     one wins the reservation and the loser short-circuits as a
        #     duplicate. This closes the check-then-act race that a plain
        #     log_exists() read left open (REQ-008.3/REQ-034.1).
        try:
            reserved = self.firestore.reserve_log(rule.uid, event_id)
        except Exception as exc:  # noqa: BLE001
            # Reservation unavailable -> fall back to a best-effort (non-atomic)
            # existence check so a single-runner deployment still dedupes.
            logger.warning("reserve_log failed rule=%s (%s); falling back to log_exists", rule.id, exc)
            try:
                reserved = not self.firestore.log_exists(rule.uid, event_id)
            except Exception:  # noqa: BLE001
                reserved = True
        if not reserved:
            return ProcessResult(rule.id, rule.uid, STATUS_DUPLICATE, "event already reserved", eval_result.value, event_id)

        # 8b. fan-out delivery
        outcome = deliver(
            message=message,
            channels=rule.delivery.channels,
            channel_registry=self.channels,
            settings=settings,
        )
        sent_at = datetime.now(timezone.utc).isoformat() if outcome.any_sent else None
        event.sentAt = sent_at

        # 9. finalize the reserved NotificationLog (overwrites the reservation)
        log = NotificationLog(
            id=event_id,
            eventId=event_id,
            ruleId=rule.id,
            kind=rule.kind,
            firedAt=event.firedAt,
            evaluatedAt=event.evaluatedAt,
            sentAt=sent_at,
            evaluatedValue=eval_result.value,
            message=message,
            channels=outcome.results,
            isTest=False,
            ruleName=rule.name,
            tickers=self._tickers(rule) or None,
        )
        try:
            self.firestore.write_notification_log(rule.uid, log)
        except Exception as exc:  # noqa: BLE001
            logger.error("write_notification_log failed rule=%s (%s)", rule.id, exc)
            return ProcessResult(rule.id, rule.uid, STATUS_ERROR, f"log write failed: {exc}", eval_result.value, event_id)

        # 10. update rule state; once -> disable
        try:
            disable = (trigger.mode == "once") if trigger else False
            self.firestore.update_rule_state(
                rule.uid, rule.id,
                last_triggered_at=event.firedAt,
                last_value=eval_result.value,
                enabled=False if disable else None,
                engine_version=self.config.engine_version,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("update_rule_state failed rule=%s (%s)", rule.id, exc)

        if outcome.invalid_push_tokens:
            logger.warning("rule=%s invalid push tokens: %s", rule.id, outcome.invalid_push_tokens)

        return ProcessResult(rule.id, rule.uid, STATUS_DELIVERED, eval_result.detail, eval_result.value, event_id, log)

    # --- test send -----------------------------------------------------------

    def send_test_alert(
        self,
        uid: str,
        rule: AlertRule,
        channels: List[str],
        settings: Optional[AlertSettings] = None,
        dry_run: bool = False,
    ) -> Optional[NotificationLog]:
        """Send a test alert and write an isTest NotificationLog.

        Does NOT touch rule state (lastTriggeredAt/enabled) — a test is a no-op
        on the rule, mirroring ``MockAlertProvider.sendTest``.

        REQ-022.4: if NONE of the requested channels has a credential configured
        (telegram chatId or FCM push token), the test is NOT performed — returns
        ``None`` and writes no log. (dry-run bypasses this, being a no-delivery
        preview.)
        """
        now = datetime.now(timezone.utc)
        if settings is None:
            try:
                settings = self.firestore.load_alert_settings(uid)
            except Exception:  # noqa: BLE001
                settings = AlertSettings()

        if not dry_run and not self._test_has_credentials(channels, settings):
            logger.info(
                "test send skipped rule=%s: no credentials for channels=%s (REQ-022.4)",
                rule.id, channels,
            )
            return None

        event_id = f"{rule.id}:test:{int(now.timestamp() * 1000)}"
        variables = {
            "ticker": self._primary_ticker(rule),
            "value": rule.lastValue,
            "threshold": rule.condition.threshold if rule.condition else None,
            "name": rule.name,
        }
        message = render_message(rule, variables)

        if dry_run:
            results = [ChannelResult(c, "sent") for c in channels]
        else:
            outcome = deliver(
                message=message,
                channels=channels,
                channel_registry=self.channels,
                settings=settings,
            )
            results = outcome.results

        any_sent = any(r.status == "sent" for r in results)
        log = NotificationLog(
            id=event_id,
            eventId=event_id,
            ruleId=rule.id,
            kind=rule.kind,
            firedAt=now.isoformat(),
            evaluatedAt=now.isoformat(),
            sentAt=now.isoformat() if any_sent else None,
            evaluatedValue=rule.lastValue if isinstance(rule.lastValue, (int, float, str)) else None,
            message=message,
            channels=results,
            isTest=True,
            ruleName=rule.name,
            tickers=self._tickers(rule) or None,
        )
        if not dry_run:
            try:
                self.firestore.write_notification_log(uid, log)
            except Exception as exc:  # noqa: BLE001
                logger.error("test log write failed rule=%s (%s)", rule.id, exc)
        return log

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _needs_prev_value(condition) -> bool:
        """True when evaluating ``condition`` requires the previously observed
        value (i.e. it uses a crossUp/crossDown comparator anywhere).

        Used to decide whether a NON-triggered rule may persist ``lastValue``:
        cross comparators need it to detect a future crossing; all other
        comparators must write nothing on a non-trigger (REQ-027.3).
        """
        if condition is None:
            return False
        if condition.comparator in ("crossUp", "crossDown"):
            return True
        for child in (condition.conditions or []):
            if AlertEngine._needs_prev_value(child):
                return True
        return False

    @staticmethod
    def _test_has_credentials(channels: List[str], settings: Optional[AlertSettings]) -> bool:
        """REQ-022.4: at least one requested channel must have a credential
        (telegram chatId or FCM push token) for a test send to be performed."""
        if settings is None:
            return False
        for channel in channels:
            if channel == "telegram" and settings.telegramChatId:
                return True
            if channel == "push" and settings.pushTokens:
                return True
        return False

    @staticmethod
    def _tickers(rule: AlertRule) -> List[str]:
        """Denormalized tickers for history search (mirrors provider.ts)."""
        c = rule.condition
        if c is None:
            return []
        out: List[str] = []
        if c.kind == "ratio":
            if c.numerator:
                out.append(c.numerator)
            if c.denominator:
                out.append(c.denominator)
        elif c.kind == "dividend" and c.ticker:
            out.append(c.ticker)
        elif c.metric is not None:
            if c.metric.ticker:
                out.append(c.metric.ticker)
            if c.metric.code:
                out.append(c.metric.code)
            if c.metric.pair:
                out.append(c.metric.pair)
        # Uppercased to match the `tickers` array-contains search index (REQ-024)
        # regardless of how the ticker was typed when the rule was authored.
        return [t.strip().upper() for t in out if t and t.strip()]

    @classmethod
    def _primary_ticker(cls, rule: AlertRule) -> Optional[str]:
        tickers = cls._tickers(rule)
        return tickers[0] if tickers else None
