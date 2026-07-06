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
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - py<3.9 fallback (not expected on CI 3.11)
    ZoneInfo = None  # type: ignore

from .models import Recurrence, TriggerPolicy

DEFAULT_TZ = "Asia/Seoul"
DEFAULT_TIME = "09:00"


def get_tz(tz_name: Optional[str]):
    """Resolve a tz name to a tzinfo, defaulting to Asia/Seoul, then UTC."""
    name = tz_name or DEFAULT_TZ
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        try:
            return ZoneInfo(DEFAULT_TZ)
        except Exception:
            return timezone.utc


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


def bucket_time(now: datetime, trigger: Optional[TriggerPolicy], window_minutes: int = 30) -> str:
    """Stable ISO bucket string used to build the idempotency eventId.

    For a scheduled recurrence we bucket by the scheduled occurrence time (so a
    given fire maps to exactly one bucket across retries/overlapping runs). For
    non-scheduled (threshold) rules we bucket ``now`` down to the window grid.
    """
    recurrence = trigger.recurrence if trigger else None
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
