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
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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
    Condition,
    MessageTemplate,
    NotificationLog,
)
from .recurrence import (
    as_utc,
    bucket_time,
    due_occurrence,
    first_future_scheduled_occurrence,
    get_tz,
    next_scheduled_occurrence,
    parse_datetime,
    parse_hh_mm,
)

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
STATUS_NOT_TRIGGERED = "condition_false"
STATUS_NO_DATA = "no_data"
STATUS_STALE_DATA = "stale_data"
STATUS_PROVIDER_ERROR = "provider_error"
STATUS_EVALUATION_ERROR = "evaluation_error"
STATUS_QUIET_HOURS = "skipped_quiet_hours"
STATUS_COOLDOWN = "skipped_cooldown"
STATUS_DUPLICATE = "skipped_duplicate"
STATUS_DELIVERED = "delivered"
STATUS_PARTIAL_FAILURE = "partial_failure"
STATUS_DELIVERY_FAILED = "failed"
STATUS_DELIVERY_UNKNOWN = "delivery_unknown"
STATUS_DRY_RUN = "dry_run"
STATUS_ERROR = "error"
STATUS_LEGACY_CURSOR_INITIALIZED = "legacy_cursor_initialized"


def _classify_delivery(results: List[ChannelResult]) -> tuple[str, str]:
    """Return the durable occurrence status and public process status."""
    statuses = [result.status for result in results]
    if statuses and all(status == "sent" for status in statuses):
        return "sent", STATUS_DELIVERED
    if "unknown" in statuses:
        return "delivery_unknown", STATUS_DELIVERY_UNKNOWN
    if "sent" in statuses:
        return "partial_failure", STATUS_PARTIAL_FAILURE
    return "failed", STATUS_DELIVERY_FAILED


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


def _has_calendar_selector(condition: Condition) -> bool:
    if condition.kind in {"date", "dividend"} and condition.selector is not None:
        return True
    return any(_has_calendar_selector(child) for child in condition.conditions)


class AlertEngine:
    def __init__(
        self,
        datasource: Optional[AlertDataSource] = None,
        evaluator_registry: Optional[dict] = None,
        channel_registry: Optional[dict] = None,
        firestore=None,
        config: Optional[EngineConfig] = None,
    ):
        if firestore is None:
            from . import firestore_client as firestore  # lazy import
        self.firestore = firestore
        self.datasource = datasource or AlertDataSource(firestore=self.firestore)
        self.evaluators = evaluator_registry or build_default_registry(self.datasource)
        self.channels = channel_registry or build_default_channels()
        self.config = config or load_config()

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

        # Scheduled rules use a durable occurrence cursor. They must never use
        # a sliding evaluation window: a past cursor remains due until a
        # permanent occurrence record is atomically created.
        if recurrence is not None:
            return self._process_scheduled_rule(rule, now, settings, dry_run)

        # Unscheduled threshold rules retain their existing evaluation path.
        calendar_occurrence = None

        # 3. evaluate
        if rule.condition is None:
            return ProcessResult(rule.id, rule.uid, STATUS_ERROR, "rule has no condition")
        evaluator = self.evaluators.get(rule.condition.kind)
        if evaluator is None:
            return ProcessResult(rule.id, rule.uid, STATUS_ERROR, f"no evaluator for kind={rule.condition.kind}")

        prev_value = rule.lastValue if isinstance(rule.lastValue, (int, float)) else None
        evaluation_now = (
            calendar_occurrence
            if calendar_occurrence is not None
            and _has_calendar_selector(rule.condition)
            else now
        )
        ctx = EvalContext(uid=rule.uid, now=evaluation_now, prev_value=prev_value, settings=settings)
        eval_result = evaluator.evaluate(rule, rule.condition, ctx)
        logger.info("rule=%s eval: %s", rule.id, eval_result.detail)

        # 4. not triggered.
        #    REQ-027.3: do NOT write Firestore for non-triggered rules. The ONLY
        #    exception is crossUp/crossDown comparators, which need the previous
        #    observed value persisted to detect a future crossing — without it
        #    cross detection is impossible. Every other comparator performs ZERO
        #    writes here, saving Firestore write quota on every poll cycle.
        if not eval_result.triggered:
            if eval_result.status not in {None, "condition_false"}:
                status = {
                    "no_data": STATUS_NO_DATA,
                    "stale_data": STATUS_STALE_DATA,
                    "provider_error": STATUS_PROVIDER_ERROR,
                    "evaluation_error": STATUS_EVALUATION_ERROR,
                }.get(eval_result.status, STATUS_EVALUATION_ERROR)
                logger.warning(
                    "evaluation unavailable alertId=%s ruleId=%s status=%s code=%s observedAt=%s detail=%s",
                    rule.id,
                    rule.id,
                    status,
                    eval_result.failure_code,
                    eval_result.observed_at.isoformat() if eval_result.observed_at else None,
                    eval_result.detail,
                )
                return ProcessResult(rule.id, rule.uid, status, eval_result.detail, eval_result.value)
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
        sent_at = as_utc(now).isoformat() if outcome.any_sent else None
        event.sentAt = sent_at
        occurrence_status, process_status = _classify_delivery(outcome.results)
        completed_at = as_utc(now).isoformat()
        failure_code = (
            "delivery_unknown"
            if occurrence_status == "delivery_unknown"
            else "all_channels_failed"
            if occurrence_status == "failed"
            else None
        )
        failure_reason = (
            "at least one channel returned an ambiguous delivery result"
            if occurrence_status == "delivery_unknown"
            else "no requested channel confirmed delivery"
            if occurrence_status == "failed"
            else None
        )

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
            evaluationStatus=eval_result.status,
            dataObservedAt=(
                eval_result.observed_at.isoformat()
                if eval_result.observed_at
                else None
            ),
            message=message,
            channels=outcome.results,
            isTest=False,
            ruleName=rule.name,
            tickers=self._tickers(rule) or None,
            status=occurrence_status,
            completedAt=completed_at,
            failureCode=failure_code,
            failureReason=failure_reason,
        )
        try:
            self.firestore.write_notification_log(rule.uid, log)
        except Exception as exc:  # noqa: BLE001
            logger.error("write_notification_log failed rule=%s (%s)", rule.id, exc)
            return ProcessResult(rule.id, rule.uid, STATUS_ERROR, f"log write failed: {exc}", eval_result.value, event_id)

        # 10. A failed/ambiguous attempt is not a confirmed trigger. Preserve
        # lastTriggeredAt and once-mode enabled state so a definitive failure
        # cannot silently consume the alert.
        if outcome.any_sent:
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

        self._cleanup_invalid_push_tokens(rule.uid, rule.id, outcome.invalid_push_tokens)

        return ProcessResult(rule.id, rule.uid, process_status, eval_result.detail, eval_result.value, event_id, log)

    def _process_scheduled_rule(
        self,
        rule: AlertRule,
        now: datetime,
        settings: AlertSettings,
        dry_run: bool,
    ) -> ProcessResult:
        """Process one durable recurring occurrence.

        The occurrence is identified by ``ruleId + scheduledFor``. Its
        NotificationLog placeholder and the rule's next cursor are committed in
        one Firestore transaction before any delivery call. A missed occurrence
        therefore remains due indefinitely, and a crash can leave only an
        explicit processing/unknown record—not a silent hole.
        """
        trigger = rule.trigger
        recurrence = trigger.recurrence
        assert recurrence is not None
        timezone_name = recurrence.tz or self.config.default_tz

        # A pre-version rule with no cursor is a legacy row. Its first durable
        # worker run is migration-only: atomically persist the first occurrence
        # strictly after the cutover instant, then return before evaluation,
        # history creation, occurrence claim, or any provider boundary.
        if rule.nextScheduledAt is None:
            is_legacy = (
                rule.durableSchedulerVersion is None
                and rule.schedulerMigration is None
                and rule.schedulerRecovery is None
                and rule.scheduleStatus != "recovery_requested"
            )
            if not is_legacy:
                detail = (
                    "durable scheduler data is corrupt: version or scheduler metadata "
                    "exists but nextScheduledAt is missing"
                )
                logger.error(
                    "scheduler corruption alertId=%s ruleId=%s code=missing_cursor_for_versioned_rule "
                    "schedulerVersion=%s workerAt=%s",
                    rule.id, rule.id, rule.durableSchedulerVersion, as_utc(now).isoformat(),
                )
                if not dry_run:
                    try:
                        self.firestore.record_scheduler_error(
                            rule.uid, rule.id, "missing_cursor_for_versioned_rule",
                            detail, as_utc(now),
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("scheduler error persistence failed ruleId=%s", rule.id)
                return ProcessResult(rule.id, rule.uid, STATUS_ERROR, detail)

            try:
                future_cursor = first_future_scheduled_occurrence(recurrence, now)
                if future_cursor is None:
                    raise ValueError("recurrence has no future scheduled occurrence")
            except Exception as exc:  # invalid timezone/schedule
                detail = f"legacy cursor initialization failed: {exc}"
                logger.exception(
                    "legacy cursor resolution failed alertId=%s ruleId=%s timezone=%s workerAt=%s",
                    rule.id, rule.id, timezone_name, as_utc(now).isoformat(),
                )
                if not dry_run:
                    try:
                        self.firestore.record_scheduler_error(
                            rule.uid, rule.id, "invalid_schedule_or_timezone",
                            detail, as_utc(now),
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("scheduler error persistence failed ruleId=%s", rule.id)
                return ProcessResult(rule.id, rule.uid, STATUS_ERROR, detail)

            future_utc = as_utc(future_cursor)
            if dry_run:
                return ProcessResult(
                    rule.id, rule.uid, STATUS_DRY_RUN,
                    f"legacy cursor would initialize at {future_utc.isoformat()}; backlog would not be delivered",
                )
            try:
                initialized = self.firestore.initialize_legacy_scheduler_cursor(
                    rule.uid,
                    rule.id,
                    trigger.to_dict(),
                    rule.scheduleChangedAt,
                    future_utc,
                    as_utc(now),
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("legacy cursor transaction failed ruleId=%s", rule.id)
                return ProcessResult(
                    rule.id, rule.uid, STATUS_ERROR,
                    f"legacy cursor initialization transaction failed: {exc}",
                )
            initialization = initialized.get("initialization")
            if initialization == "inactive":
                return ProcessResult(
                    rule.id, rule.uid, STATUS_DISABLED,
                    initialized.get("reason") or "rule inactive during legacy cursor initialization",
                )
            if initialization == "schedule_changed":
                return ProcessResult(
                    rule.id, rule.uid, STATUS_NOT_DUE,
                    "schedule changed during legacy cursor initialization",
                )
            if initialization == "corrupt_durable_scheduler":
                try:
                    self.firestore.record_scheduler_error(
                        rule.uid, rule.id, "missing_cursor_for_versioned_rule",
                        "durable scheduler metadata appeared without a cursor during initialization",
                        as_utc(now),
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("scheduler error persistence failed ruleId=%s", rule.id)
                return ProcessResult(
                    rule.id, rule.uid, STATUS_ERROR,
                    "durable scheduler metadata appeared without a cursor during initialization",
                )
            if initialization not in {"initialized", "already_initialized"}:
                return ProcessResult(
                    rule.id, rule.uid, STATUS_ERROR,
                    f"unexpected legacy cursor initialization result: {initialization}",
                )
            stored_cursor = initialized.get("nextScheduledAt") or future_utc
            logger.info(
                "legacy cursor initialization alertId=%s ruleId=%s result=%s "
                "backlogPolicy=skip_automatic_backlog cutoff=%s nextScheduledAt=%s",
                rule.id, rule.id, initialization, as_utc(now).isoformat(), stored_cursor,
            )
            return ProcessResult(
                rule.id, rule.uid, STATUS_LEGACY_CURSOR_INITIALIZED,
                f"backlog not delivered; nextScheduledAt={stored_cursor}",
            )

        resume_record = None
        resume_event_id = None
        if rule.scheduleStatus == "processing" and rule.lastOccurrenceId:
            try:
                resume_record = self.firestore.get_occurrence(rule.uid, rule.lastOccurrenceId)
            except Exception as exc:  # noqa: BLE001
                logger.exception("pending occurrence load failed occurrenceId=%s", rule.lastOccurrenceId)
                return ProcessResult(rule.id, rule.uid, STATUS_ERROR, f"pending occurrence load failed: {exc}")
            if resume_record and resume_record.get("status") not in {
                "sent", "partial_failure", "failed", "skipped", "cancelled",
                "disabled", "delivery_unknown", "condition_false", "no_data",
                "stale_data", "provider_error", "evaluation_error",
                "skipped_quiet_hours", "skipped_cooldown",
            }:
                resume_event_id = rule.lastOccurrenceId
            elif resume_record and resume_record.get("status"):
                # The log commit succeeded but the denormalized rule status was
                # stale (for example, a client cached an older snapshot). Repair
                # it idempotently without touching delivery.
                try:
                    self.firestore.finalize_occurrence(
                        rule.uid, rule.id, rule.lastOccurrenceId,
                        str(resume_record["status"]), {}, {},
                    )
                except Exception as exc:  # noqa: BLE001
                    return ProcessResult(rule.id, rule.uid, STATUS_ERROR, f"schedule status repair failed: {exc}")
                return ProcessResult(
                    rule.id, rule.uid, STATUS_DUPLICATE,
                    "terminal occurrence already recorded", event_id=rule.lastOccurrenceId,
                )

        try:
            scheduled_for = (
                parse_datetime(resume_record.get("scheduledFor"), timezone_name)
                if resume_event_id and resume_record
                else due_occurrence(
                    recurrence,
                    now,
                    next_scheduled_at=rule.nextScheduledAt,
                )
            )
        except Exception as exc:  # invalid timezone/schedule
            logger.exception(
                "schedule resolution failed alertId=%s ruleId=%s timezone=%s workerAt=%s",
                rule.id, rule.id, timezone_name, as_utc(now).isoformat(),
            )
            event_id = f"{rule.id}:schedule-error:{as_utc(now).date().isoformat()}"
            if not dry_run:
                failure_log = NotificationLog(
                    id=event_id,
                    eventId=event_id,
                    ruleId=rule.id,
                    kind=rule.kind,
                    firedAt=as_utc(now).isoformat(),
                    evaluatedAt=as_utc(now).isoformat(),
                    message=render_message(rule, {"name": rule.name}),
                    channels=[],
                    isTest=False,
                    ruleName=rule.name,
                    status="failed",
                    timezone=timezone_name,
                    processingStartedAt=as_utc(now).isoformat(),
                    completedAt=as_utc(now).isoformat(),
                    attemptCount=1,
                    failureCode="invalid_schedule_or_timezone",
                    failureReason=str(exc),
                )
                try:
                    self.firestore.write_notification_log(rule.uid, failure_log)
                except Exception:  # noqa: BLE001
                    logger.exception("schedule failure record write failed ruleId=%s", rule.id)
            return ProcessResult(rule.id, rule.uid, STATUS_ERROR, f"schedule resolution failed: {exc}", event_id=event_id)
        if scheduled_for is None:
            logger.info(
                "due decision alertId=%s ruleId=%s due=false nextScheduledAt=%s timezone=%s workerAt=%s",
                rule.id, rule.id, rule.nextScheduledAt, timezone_name, as_utc(now).isoformat(),
            )
            return ProcessResult(rule.id, rule.uid, STATUS_NOT_DUE, "durable occurrence cursor is in the future")

        # A selector-backed one-shot calendar alert evaluates daily until its
        # target event date, then disables only after delivery. Advancing its
        # daily cursor on non-matching days is therefore required.
        should_advance = trigger.mode == "recurring" or (
            recurrence.kind == "calendar"
            and rule.condition is not None
            and _has_calendar_selector(rule.condition)
        )
        next_scheduled = next_scheduled_occurrence(recurrence, scheduled_for) if should_advance else None
        scheduled_utc = as_utc(scheduled_for)
        next_utc = as_utc(next_scheduled) if next_scheduled is not None else None
        occurrence_bucket = scheduled_for.isoformat()
        event_id = resume_event_id or make_event_id(rule.id, occurrence_bucket)
        worker_id = uuid.uuid4().hex
        processing_started = as_utc(now).isoformat()
        delay_seconds = max(0, int((as_utc(now) - scheduled_utc).total_seconds()))

        logger.info(
            "due decision alertId=%s ruleId=%s occurrenceId=%s due=true scheduledFor=%s "
            "timezone=%s workerAt=%s delaySeconds=%d nextBefore=%s nextAfter=%s",
            rule.id, rule.id, event_id, scheduled_utc.isoformat(), timezone_name,
            processing_started, delay_seconds, rule.nextScheduledAt,
            next_utc.isoformat() if next_utc else None,
        )

        skip_status = None
        skip_code = None
        skip_reason = None
        if resume_record:
            # Evaluation and message rendering completed before the original
            # reservation. Recovery must use that durable snapshot instead of
            # querying mutable market/calendar data again.
            eval_result = SimpleNamespace(
                triggered=True,
                value=resume_record.get("evaluatedValue"),
                detail="resumed durable occurrence",
                status="triggered",
                failure_code=None,
                observed_at=None,
            )
            stored_message = resume_record.get("message") or {}
            message = MessageTemplate(
                title=str(stored_message.get("title") or rule.name),
                body=str(stored_message.get("body") or rule.name),
            )
            skip_code = resume_record.get("failureCode")
            skip_reason = resume_record.get("failureReason")
            if skip_code:
                skip_status = "failed" if skip_code in {
                    "evaluation_error", "invalid_schedule_or_timezone",
                } else "skipped"
        else:
            # Evaluate at the original scheduled wall-clock instant. This is
            # essential for a 07:00 Asia/Seoul occurrence recovered at 09:19
            # and for calendar selectors crossing a UTC date boundary.
            eval_result = None
            eval_error = None
            ctx = None
            if rule.condition is None:
                eval_error = "rule has no condition"
            else:
                evaluator = self.evaluators.get(rule.condition.kind)
                if evaluator is None:
                    eval_error = f"no evaluator for kind={rule.condition.kind}"
                else:
                    prev_value = rule.lastValue if isinstance(rule.lastValue, (int, float)) else None
                    ctx = EvalContext(uid=rule.uid, now=scheduled_for, prev_value=prev_value, settings=settings)
                    try:
                        eval_result = evaluator.evaluate(rule, rule.condition, ctx)
                    except Exception as exc:  # noqa: BLE001
                        eval_error = f"evaluator exception: {type(exc).__name__}: {exc}"

            variables = {
                "ticker": (ctx.extra.get("ticker") if ctx else None) or self._primary_ticker(rule),
                "value": eval_result.value if eval_result else None,
                "threshold": rule.condition.threshold if rule.condition else None,
                "name": rule.name,
            }
            message = render_message(rule, variables)

            if eval_error:
                skip_status, skip_code, skip_reason = "evaluation_error", "evaluation_error", eval_error
            elif eval_result.status not in {None, "triggered", "condition_false"}:
                skip_status = eval_result.status
                skip_code = eval_result.failure_code or eval_result.status
                skip_reason = eval_result.detail
            elif not eval_result.triggered:
                skip_status, skip_code, skip_reason = "condition_false", "condition_not_met", eval_result.detail
            elif _within_quiet_hours(trigger.quietHours, now):
                skip_status, skip_code, skip_reason = "skipped_quiet_hours", "quiet_hours", "within quiet hours"
            elif trigger.cooldownMinutes:
                last = _parse_iso(rule.lastTriggeredAt)
                if last is not None and now - last < timedelta(minutes=trigger.cooldownMinutes):
                    skip_status, skip_code, skip_reason = "skipped_cooldown", "cooldown", "within cooldown"

        pending_channels = [] if skip_status else [
            ChannelResult(channel, "pending", attemptCount=0) for channel in rule.delivery.channels
        ]
        placeholder = NotificationLog(
            id=event_id,
            eventId=event_id,
            ruleId=rule.id,
            kind=rule.kind,
            firedAt=processing_started,
            evaluatedAt=processing_started,
            evaluatedValue=eval_result.value if eval_result else None,
            evaluationStatus=eval_result.status if eval_result else "evaluation_error",
            dataObservedAt=(
                eval_result.observed_at.isoformat()
                if eval_result and eval_result.observed_at
                else None
            ),
            message=message,
            channels=pending_channels,
            isTest=False,
            ruleName=rule.name,
            tickers=self._tickers(rule) or None,
            status="processing",
            scheduledFor=scheduled_utc.isoformat(),
            timezone=timezone_name,
            processingStartedAt=processing_started,
            attemptCount=1,
            nextScheduledAt=next_utc.isoformat() if next_utc else None,
            nextScheduleUpdated=True,
            failureCode=skip_code,
            failureReason=skip_reason,
        )

        if dry_run:
            return ProcessResult(
                rule.id, rule.uid, STATUS_DRY_RUN,
                f"scheduledFor={scheduled_utc.isoformat()} delaySeconds={delay_seconds}",
                eval_result.value if eval_result else None, event_id,
            )

        # No best-effort fallback is allowed here. Delivery without an atomic
        # reservation would re-open the duplicate-send race.
        try:
            claim = self.firestore.claim_occurrence(
                rule.uid, rule.id, event_id, placeholder.to_dict(), next_utc,
                worker_id, as_utc(now),
                expected_next_scheduled_at=rule.nextScheduledAt,
                expected_schedule_changed_at=rule.scheduleChangedAt,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "occurrence claim failed alertId=%s ruleId=%s occurrenceId=%s scheduledFor=%s",
                rule.id, rule.id, event_id, scheduled_utc.isoformat(),
            )
            return ProcessResult(rule.id, rule.uid, STATUS_ERROR, f"occurrence claim failed: {exc}", event_id=event_id)

        claim_status = claim.get("claim")
        logger.info(
            "claim result alertId=%s ruleId=%s occurrenceId=%s claim=%s",
            rule.id, rule.id, event_id, claim_status,
        )
        if claim_status != "claimed":
            if claim_status == "inactive":
                return ProcessResult(
                    rule.id, rule.uid, STATUS_DISABLED,
                    claim.get("reason") or "rule disabled or deleted before claim",
                    eval_result.value if eval_result else None, event_id,
                )
            if claim_status == "schedule_changed":
                return ProcessResult(
                    rule.id, rule.uid, STATUS_NOT_DUE,
                    claim.get("reason") or "schedule changed before claim",
                    eval_result.value if eval_result else None, event_id,
                )
            return ProcessResult(
                rule.id, rule.uid, STATUS_DUPLICATE,
                "occurrence terminal" if claim_status == "terminal" else "occurrence owned by another worker",
                eval_result.value if eval_result else None, event_id,
            )

        completed_at = as_utc(now).isoformat()
        if skip_status:
            rule_updates = {"engineVersion": self.config.engine_version}
            if (
                eval_result
                and eval_result.status == "condition_false"
                and eval_result.value is not None
                and self._needs_prev_value(rule.condition)
            ):
                rule_updates["lastValue"] = eval_result.value
            try:
                self.firestore.finalize_occurrence(
                    rule.uid, rule.id, event_id, skip_status,
                    {
                        "completedAt": completed_at,
                        "failureCode": skip_code,
                        "failureReason": skip_reason,
                    },
                    rule_updates,
                    worker_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("occurrence finalization failed occurrenceId=%s", event_id)
                return ProcessResult(rule.id, rule.uid, STATUS_ERROR, f"finalization failed: {exc}", event_id=event_id)
            result_status = {
                "condition_false": STATUS_NOT_TRIGGERED,
                "no_data": STATUS_NO_DATA,
                "stale_data": STATUS_STALE_DATA,
                "provider_error": STATUS_PROVIDER_ERROR,
                "evaluation_error": STATUS_EVALUATION_ERROR,
                "skipped_quiet_hours": STATUS_QUIET_HOURS,
                "skipped_cooldown": STATUS_COOLDOWN,
                "failed": STATUS_ERROR,
            }.get(skip_status, STATUS_NOT_TRIGGERED)
            return ProcessResult(
                rule.id, rule.uid, result_status, skip_reason,
                eval_result.value if eval_result else None, event_id, placeholder,
            )

        # On recovery, a channel left in "sending" crossed an unknowable crash
        # boundary. Re-sending could duplicate a Telegram/FCM notification, so
        # classify it as unknown and require explicit operator review.
        claimed_record = claim.get("record", {})
        if claimed_record.get("reservedAt") and not claimed_record.get("channels"):
            # Reservations written by engine <=2.1.0 did not persist a channel
            # state before delivery. Their provider boundary is unknowable.
            claimed_channel_rows = [
                {"channel": channel, "status": "sending", "attemptCount": 1}
                for channel in rule.delivery.channels
            ]
        else:
            claimed_channel_rows = claimed_record.get("channels") or placeholder.to_dict()["channels"]
        stored_channels = {
            item.get("channel"): dict(item)
            for item in claimed_channel_rows
        }
        results: List[ChannelResult] = []
        invalid_tokens: List[str] = []
        sent_at = None
        for channel_name in rule.delivery.channels:
            previous = stored_channels.get(channel_name, {"channel": channel_name, "status": "pending"})
            if previous.get("status") in {"sent", "failed", "unknown"}:
                results.append(ChannelResult(
                    channel_name, previous["status"], previous.get("error"),
                    previous.get("errorCode"), previous.get("attemptCount"),
                    previous.get("attemptedAt"), previous.get("completedAt"),
                ))
                continue
            if previous.get("status") == "sending":
                result = ChannelResult(
                    channel_name, "unknown",
                    "worker stopped after delivery began; not retried to prevent duplicate delivery",
                    "ambiguous_delivery", int(previous.get("attemptCount") or 1),
                    previous.get("attemptedAt"), completed_at,
                )
                self.firestore.record_channel_result(rule.uid, event_id, result.to_dict(), worker_id)
                results.append(result)
                continue

            attempted_at = as_utc(now)
            try:
                begin_result = self.firestore.begin_channel_attempt(
                    rule.uid, rule.id, event_id, channel_name, worker_id, attempted_at,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("channel claim failed occurrenceId=%s channel=%s", event_id, channel_name)
                return ProcessResult(rule.id, rule.uid, STATUS_ERROR, f"channel claim failed: {exc}", event_id=event_id)
            if begin_result in {"rule_disabled", "rule_deleted"}:
                return ProcessResult(
                    rule.id, rule.uid, STATUS_DISABLED, begin_result, event_id=event_id,
                )
            if begin_result != "began":
                return ProcessResult(
                    rule.id, rule.uid, STATUS_ERROR,
                    "channel lease lost" if begin_result == "lease_lost" else "channel is not pending",
                    event_id=event_id,
                )

            channel = self.channels.get(channel_name)
            try:
                if channel is None:
                    raise RuntimeError("unknown channel")
                raw = channel.send(message, settings)
                channel_status = raw.status if raw.status in {"sent", "failed", "unknown"} else "failed"
                error = raw.error
                invalid_tokens.extend(raw.invalid_tokens)
            except Exception as exc:  # noqa: BLE001
                channel_status = "failed"
                error = f"worker exception: {type(exc).__name__}: {exc}"
            result_completed = as_utc(now).isoformat()
            error_code = self._delivery_error_code(channel_name, error)
            result = ChannelResult(
                channel_name, channel_status, error, error_code, 1,
                attempted_at.isoformat(), result_completed,
            )
            try:
                self.firestore.record_channel_result(rule.uid, event_id, result.to_dict(), worker_id)
            except Exception as exc:  # noqa: BLE001
                # The durable state is still "sending". A recovery worker will
                # mark it unknown and will not call the provider again.
                logger.exception(
                    "channel result persistence failed occurrenceId=%s channel=%s status=%s",
                    event_id, channel_name, channel_status,
                )
                return ProcessResult(rule.id, rule.uid, STATUS_ERROR, f"channel result write failed: {exc}", event_id=event_id)
            results.append(result)
            if channel_status == "sent" and sent_at is None:
                sent_at = result_completed
            logger.info(
                "channel result alertId=%s ruleId=%s occurrenceId=%s channel=%s status=%s errorCode=%s",
                rule.id, rule.id, event_id, channel_name, channel_status, error_code,
            )

        occurrence_status, process_status = _classify_delivery(results)
        completed_at = as_utc(now).isoformat()
        rule_updates = {"engineVersion": self.config.engine_version}
        if sent_at or occurrence_status == "delivery_unknown":
            rule_updates["lastValue"] = eval_result.value
        if sent_at:
            rule_updates["lastTriggeredAt"] = processing_started
        if trigger.mode == "once" and sent_at:
            rule_updates["enabled"] = False
        try:
            self.firestore.finalize_occurrence(
                rule.uid, rule.id, event_id, occurrence_status,
                {
                    "completedAt": completed_at,
                    "sentAt": sent_at,
                    "failureCode": "delivery_unknown" if occurrence_status == "delivery_unknown" else None,
                    "failureReason": (
                        "at least one channel crossed an ambiguous crash boundary"
                        if occurrence_status == "delivery_unknown" else None
                    ),
                },
                rule_updates,
                worker_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("occurrence finalization failed occurrenceId=%s", event_id)
            return ProcessResult(rule.id, rule.uid, STATUS_ERROR, f"finalization failed: {exc}", event_id=event_id)

        self._cleanup_invalid_push_tokens(rule.uid, rule.id, invalid_tokens)
        log = NotificationLog(
            **{
                **placeholder.__dict__,
                "channels": results,
                "status": occurrence_status,
                "sentAt": sent_at,
                "completedAt": completed_at,
                "failureCode": "delivery_unknown" if occurrence_status == "delivery_unknown" else None,
                "failureReason": (
                    "at least one channel crossed an ambiguous crash boundary"
                    if occurrence_status == "delivery_unknown" else None
                ),
            }
        )
        return ProcessResult(
            rule.id, rule.uid, process_status,
            f"occurrence={occurrence_status} scheduledFor={scheduled_utc.isoformat()} delaySeconds={delay_seconds}",
            eval_result.value, event_id, log,
        )

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
            self._cleanup_invalid_push_tokens(uid, rule.id, outcome.invalid_push_tokens)

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
    def _delivery_error_code(channel: str, error: Optional[str]) -> Optional[str]:
        if not error:
            return None
        lower = error.lower()
        if "network" in lower or "timeout" in lower or "connection" in lower:
            return f"{channel}_network_error"
        if "token" in lower or "credential" in lower or "auth" in lower or "chatid" in lower:
            return f"{channel}_auth_error"
        if "unknown channel" in lower:
            return "unknown_channel"
        return f"{channel}_provider_error"

    def _cleanup_invalid_push_tokens(self, uid: str, rule_id: str, tokens: List[str]) -> None:
        if not tokens:
            return
        masked = [f"{token[:12]}…" for token in tokens]
        logger.warning("rule=%s FCM marked invalid tokens count=%d tokens=%s", rule_id, len(tokens), masked)
        cleanup = getattr(self.firestore, "remove_invalid_push_tokens", None)
        if not callable(cleanup):
            logger.info("rule=%s invalid tokens recorded; Firestore cleanup is unavailable in this runtime", rule_id)
            return
        try:
            removed = cleanup(uid, tokens)
            logger.info("rule=%s removed %d FCM-confirmed invalid push token(s)", rule_id, removed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rule=%s could not remove invalid push tokens (%s)", rule_id, exc)

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
