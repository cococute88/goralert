"""Recurrence engine: when is a scheduled rule "due"?

Times are interpreted in the recurrence timezone (default Asia/Seoul) using the
stdlib ``zoneinfo``. This mirrors ``lib/alerts/schedule.ts`` semantics but is
the CANONICAL implementation (the TS version is a read-only UI hint).

Supported recurrence kinds (from TS ``Recurrence.kind``):
- weekly         : a specific weekday at ``time``
- biweekly       : every two weeks on ``weekday`` (optionally anchored)
- monthlyFirstDay: 1st calendar day of the month at ``time``
- monthlyLastDay : last calendar day of the month at ``time``
- calendar       : event-driven (paired with calendar data) — no fixed cadence

Key entry points:
- ``next_occurrence(recurrence, from_dt)`` -> next fire datetime (tz-aware) or None
- ``due_now(recurrence, now, window_minutes)`` -> bool, is a scheduled fire due
  inside [now - window, now]?
- ``bucket_time(now, trigger, window_minutes)`` -> stable ISO bucket for eventId
"""

from __future__ import annotations

import calendar as _calendar
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - py<3.9 fallback (not expected on CI 3.11)
    ZoneInfo = None  # type: ignore

from .models import Recurrence, TriggerPolicy

DEFAULT_TZ = "Asia/Seoul"
DEFAULT_TIME = "09:00"


def get_tz(tz_name: Optional[str]):
    """Resolve an IANA timezone without silently changing its meaning."""
    name = tz_name or DEFAULT_TZ
    if ZoneInfo is None:
        raise RuntimeError("zoneinfo is unavailable; install tzdata")
    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise ValueError(f"invalid IANA timezone: {name}") from exc


def parse_hh_mm(time_str: Optional[str]) -> tuple[int, int]:
    """Parse 'HH:mm' -> (hours, minutes); defaults to 09:00 on bad input."""
    if not time_str:
        return 9, 0
    try:
        raw = time_str.strip()
        hh, mm = raw.split(":", 1)
        hours = max(0, min(23, int(hh)))
        minutes = max(0, min(59, int(mm)))
        return hours, minutes
    except Exception:
        return 9, 0


def _at_time(d: datetime, time_str: Optional[str], tz) -> datetime:
    """Return ``d``'s date at the given wall-clock time, in tz."""
    hours, minutes = parse_hh_mm(time_str or DEFAULT_TIME)
    return datetime(d.year, d.month, d.day, hours, minutes, 0, 0, tzinfo=tz)


def _ensure_aware(dt: datetime, tz) -> datetime:
    """Normalize a datetime into ``tz`` (assume tz if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def parse_datetime(value: Any, tz_name: Optional[str] = None) -> Optional[datetime]:
    """Parse Firestore Timestamp/datetime/ISO values into an aware datetime.

    Firestore returns aware ``datetime`` objects.  The ISO branch exists for
    legacy rule fields and always treats a naive value in the rule timezone,
    never in the server's operating-system timezone.
    """
    if value is None:
        return None
    if hasattr(value, "to_datetime"):
        try:
            value = value.to_datetime()
        except Exception:
            return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    tz = get_tz(tz_name)
    return _ensure_aware(dt, tz)


def as_utc(dt: datetime) -> datetime:
    """Return a timezone-aware UTC datetime suitable for Firestore storage."""
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc)


def _last_day_of_month(year: int, month: int) -> int:
    return _calendar.monthrange(year, month)[1]


def _next_weekday(from_dt: datetime, weekday: int, time_str: Optional[str], tz) -> datetime:
    """Next datetime >= from_dt whose weekday matches (Mon=0..Sun=6 -> see note).

    The TS layer uses 0=Sunday..6=Saturday (JS getDay). Python's weekday() is
    0=Monday..6=Sunday. We accept the TS convention (0=Sun) and convert.
    """
    # Map TS weekday (0=Sun..6=Sat, JS getDay) to Python weekday() (0=Mon..6=Sun):
    # Sun(0)->6, Mon(1)->0, Tue(2)->1 ... Sat(6)->5.
    ts_to_py = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
    target_py = ts_to_py.get(int(weekday) % 7, 0)

    candidate = _at_time(from_dt, time_str, tz)
    diff = (target_py - candidate.weekday()) % 7
    candidate = candidate + timedelta(days=diff)
    if candidate < from_dt:
        candidate = candidate + timedelta(days=7)
    return candidate


def next_occurrence(recurrence: Optional[Recurrence], from_dt: Optional[datetime] = None) -> Optional[datetime]:
    """Next fire datetime (tz-aware) at/after ``from_dt``; None if event-driven.

    ``calendar`` recurrence is event-driven (paired with calendar data) so it
    has no predictable cadence here and returns None.
    """
    if recurrence is None:
        return None

    tz = get_tz(recurrence.tz)
    base = _ensure_aware(from_dt, tz) if from_dt else datetime.now(tz)
    time_str = recurrence.time
    kind = recurrence.kind

    if kind == "weekly":
        weekday = recurrence.weekday if recurrence.weekday is not None else 1  # Monday
        return _next_weekday(base, weekday, time_str, tz)

    if kind == "biweekly":
        weekday = recurrence.weekday if recurrence.weekday is not None else 6  # Saturday (US-001)
        first_match = _next_weekday(base, weekday, time_str, tz)
        if not recurrence.anchorDate:
            return first_match
        # Anchor the 2-week cadence to anchorDate.
        try:
            anchor_date = datetime.fromisoformat(recurrence.anchorDate[:10])
        except Exception:
            return first_match
        anchor = _at_time(anchor_date.replace(tzinfo=tz), time_str, tz)
        # Align anchor to the requested weekday, then walk forward in 14-day steps.
        candidate = _next_weekday(anchor - timedelta(days=1), weekday, time_str, tz)
        while candidate < base:
            candidate = candidate + timedelta(days=14)
        return candidate

    if kind == "monthlyFirstDay":
        year, month = base.year, base.month
        candidate = _at_time(datetime(year, month, 1, tzinfo=tz), time_str, tz)
        if candidate < base:
            month += 1
            if month > 12:
                month = 1
                year += 1
            candidate = _at_time(datetime(year, month, 1, tzinfo=tz), time_str, tz)
        return candidate

    if kind == "monthlyLastDay":
        year, month = base.year, base.month
        last = _last_day_of_month(year, month)
        candidate = _at_time(datetime(year, month, last, tzinfo=tz), time_str, tz)
        if candidate < base:
            month += 1
            if month > 12:
                month = 1
                year += 1
            last = _last_day_of_month(year, month)
            candidate = _at_time(datetime(year, month, last, tzinfo=tz), time_str, tz)
        return candidate

    # "calendar" or unknown -> event-driven / not predictable.
    return None


def scheduled_occurrence(
    recurrence: Optional[Recurrence],
    from_dt: datetime,
) -> Optional[datetime]:
    """Return the first scheduled wall-clock occurrence at/after ``from_dt``.

    Unlike ``next_occurrence``, ``calendar`` means a daily evaluation cursor
    here. Selector-backed calendar rules still decide whether to send by
    evaluating the event date at this exact occurrence.
    """
    if recurrence is None:
        return None
    if recurrence.kind != "calendar":
        return next_occurrence(recurrence, from_dt)
    tz = get_tz(recurrence.tz)
    base = _ensure_aware(from_dt, tz)
    candidate = _at_time(base, recurrence.time or DEFAULT_TIME, tz)
    if candidate < base:
        candidate = _at_time(base + timedelta(days=1), recurrence.time or DEFAULT_TIME, tz)
    return candidate


def next_scheduled_occurrence(
    recurrence: Optional[Recurrence],
    occurrence: datetime,
) -> Optional[datetime]:
    """Return the occurrence strictly after ``occurrence``."""
    return scheduled_occurrence(recurrence, occurrence + timedelta(microseconds=1))


def first_future_scheduled_occurrence(
    recurrence: Optional[Recurrence],
    after: datetime,
) -> Optional[datetime]:
    """Return the first scheduled occurrence strictly after ``after``.

    Legacy cursor migration uses this boundary so the worker never claims an
    occurrence at or before the migration cutoff. Calendar recurrences use the
    same daily evaluation cadence as the durable worker.
    """
    return scheduled_occurrence(recurrence, after + timedelta(microseconds=1))


def due_occurrence(
    recurrence: Optional[Recurrence],
    now: datetime,
    *,
    next_scheduled_at: Any = None,
    anchor: Any = None,
) -> Optional[datetime]:
    """Return the durable due occurrence, with no time-window expiry.

    ``next_scheduled_at`` is the canonical Firestore cursor. Legacy rules are
    initialized from ``anchor`` (normally creation time or the instant after
    the last processed occurrence). Once an occurrence is in the past it stays
    due until a permanent occurrence record is created.
    """
    if recurrence is None:
        return None
    tz = get_tz(recurrence.tz)
    aware_now = _ensure_aware(now, tz)
    candidate = parse_datetime(next_scheduled_at, recurrence.tz)
    if candidate is None:
        parsed_anchor = parse_datetime(anchor, recurrence.tz)
        if parsed_anchor is None:
            # Last-resort compatibility for malformed legacy rows that predate
            # createdAt. Limit inference to the current cadence period rather
            # than silently skipping today's already-past wall time.
            if recurrence.kind in {"monthlyFirstDay", "monthlyLastDay"}:
                parsed_anchor = aware_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            elif recurrence.kind == "calendar":
                parsed_anchor = aware_now.replace(hour=0, minute=0, second=0, microsecond=0)
            elif recurrence.kind == "biweekly":
                parsed_anchor = aware_now - timedelta(days=14)
            else:
                parsed_anchor = aware_now - timedelta(days=7)
        candidate = scheduled_occurrence(recurrence, parsed_anchor)
    if candidate is None or candidate > aware_now:
        return None
    return candidate


def due_now(
    recurrence: Optional[Recurrence],
    now: Optional[datetime] = None,
    window_minutes: int = 30,
) -> bool:
    """True when a scheduled fire falls within [now - window, now].

    The engine runs on a cron cadence; a rule scheduled for 08:00 is considered
    "due" when the run executes at 08:00 (+/- the window). We look for the next
    occurrence at/after (now - window) and check it is <= now.

    NOTE: the default window_minutes (30) is kept in sync with
    config.DEFAULT_EVAL_WINDOW_MINUTES. The engine always passes the resolved
    config value explicitly; this default only applies to direct callers/tests.
    """
    if recurrence is None:
        return False
    if recurrence.kind == "calendar":
        # Event-driven cadence is handled by the date evaluator against calendar
        # data, not by a fixed schedule.
        return False

    tz = get_tz(recurrence.tz)
    now = _ensure_aware(now, tz) if now else datetime.now(tz)
    window_start = now - timedelta(minutes=max(0, window_minutes))
    occ = next_occurrence(recurrence, window_start)
    if occ is None:
        return False
    return window_start <= occ <= now


def calendar_due_now(
    recurrence: Optional[Recurrence],
    now: Optional[datetime] = None,
    window_minutes: int = 30,
) -> bool:
    """True when a calendar-alert wall time is within the due window."""
    return calendar_due_occurrence(recurrence, now, window_minutes) is not None


def calendar_due_occurrence(
    recurrence: Optional[Recurrence],
    now: Optional[datetime] = None,
    window_minutes: int = 30,
) -> Optional[datetime]:
    """Return the calendar wall-time occurrence in ``[now-window, now]``.

    Building the candidate from ``window_start`` (rather than always from
    ``now``) preserves late-night occurrences first evaluated after midnight.
    """
    if recurrence is None or recurrence.kind != "calendar":
        return None
    tz = get_tz(recurrence.tz)
    aware = _ensure_aware(now, tz) if now else datetime.now(tz)
    window_start = aware - timedelta(minutes=max(0, window_minutes))
    occurrence = _at_time(window_start, recurrence.time or DEFAULT_TIME, tz)
    if occurrence < window_start:
        occurrence = _at_time(window_start + timedelta(days=1), recurrence.time or DEFAULT_TIME, tz)
    return occurrence if occurrence <= aware else None


def bucket_time(now: datetime, trigger: Optional[TriggerPolicy], window_minutes: int = 30) -> str:
    """Stable ISO bucket string used to build the idempotency eventId.

    For a scheduled recurrence we bucket by the scheduled occurrence time (so a
    given fire maps to exactly one bucket across retries/overlapping runs). For
    non-scheduled (threshold) rules we bucket ``now`` down to the window grid.
    """
    recurrence = trigger.recurrence if trigger else None
    if recurrence is not None and recurrence.kind == "calendar":
        tz = get_tz(recurrence.tz)
        aware = _ensure_aware(now, tz)
        occurrence = calendar_due_occurrence(recurrence, aware, window_minutes)
        if occurrence is None:
            occurrence = _at_time(aware, recurrence.time or DEFAULT_TIME, tz)
        return occurrence.isoformat()

    if recurrence is not None and recurrence.kind not in ("calendar", None):
        tz = get_tz(recurrence.tz)
        aware = _ensure_aware(now, tz)
        window_start = aware - timedelta(minutes=max(0, window_minutes))
        occ = next_occurrence(recurrence, window_start)
        if occ is not None and occ <= aware:
            return occ.isoformat()

    # Default: floor `now` to the window grid (UTC) for a deterministic bucket.
    aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    utc = aware.astimezone(timezone.utc)
    window = max(1, window_minutes)
    floored_minute = (utc.minute // window) * window
    floored = utc.replace(minute=floored_minute, second=0, microsecond=0)
    return floored.isoformat()
