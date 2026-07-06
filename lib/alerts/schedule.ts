// GORALERT-ALERT-SYSTEM Layer B1
// Next-fire computation for scheduled alerts. Used by the Home dashboard
// (오늘/다음 예정) and the alerts list (다음 발송 예정).
//
// Times are interpreted in the recurrence timezone (default Asia/Seoul) using
// Intl.DateTimeFormat — no external deps. We resolve a wall-clock time in the
// target tz to an absolute UTC instant (zonedTimeToUtc), so the returned Date is
// the correct moment regardless of the host machine's local timezone. The
// future Python engine still owns the canonical scheduling; this remains a
// read-only UI hint.
//
// NOTE: the biweekly cadence steps in whole-day multiples; Asia/Seoul observes
// no DST so this is exact. For a hypothetical DST tz the stride is re-resolved
// per occurrence via zonedTimeToUtc, keeping each fire on the intended wall time.

import type { Recurrence, TriggerPolicy } from "./types";

const DEFAULT_TZ = "Asia/Seoul";
const DEFAULT_TIME = "09:00";
const DAY_MS = 86_400_000;

function parseHhMm(time: string | undefined): { hours: number; minutes: number } {
  const fallback = { hours: 9, minutes: 0 };
  if (!time) return fallback;
  const match = /^(\d{1,2}):(\d{2})$/.exec(time.trim());
  if (!match) return fallback;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return fallback;
  return { hours: Math.min(23, Math.max(0, hours)), minutes: Math.min(59, Math.max(0, minutes)) };
}

type CalendarParts = { year: number; month: number; day: number };

// Calendar parts (year/month[1-12]/day) of an absolute instant, as seen in `tz`.
function getDateParts(date: Date, tz: string): CalendarParts {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const map: Record<string, number> = {};
  for (const part of dtf.formatToParts(date)) {
    if (part.type !== "literal") map[part.type] = Number(part.value);
  }
  return { year: map.year, month: map.month, day: map.day };
}

// Offset (minutes that `tz` is ahead of UTC) at the given instant.
function tzOffsetMinutes(date: Date, tz: string): number {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const map: Record<string, number> = {};
  for (const part of dtf.formatToParts(date)) {
    if (part.type !== "literal") map[part.type] = Number(part.value);
  }
  const hour = map.hour === 24 ? 0 : map.hour;
  const asUtc = Date.UTC(map.year, map.month - 1, map.day, hour, map.minute, map.second);
  return Math.round((asUtc - date.getTime()) / 60000);
}

// Converts a wall-clock time in `tz` to the matching absolute UTC instant.
function zonedTimeToUtc(
  year: number,
  month: number, // 1-12
  day: number,
  hours: number,
  minutes: number,
  tz: string,
): Date {
  // First guess: treat the wall clock as if it were UTC, then correct by the
  // tz offset at that approximate instant (stable for non-DST zones like Seoul).
  const guessMs = Date.UTC(year, month - 1, day, hours, minutes, 0, 0);
  const offset = tzOffsetMinutes(new Date(guessMs), tz);
  return new Date(guessMs - offset * 60000);
}

// Weekday (0=Sun..6=Sat) of a calendar date — independent of timezone.
function weekdayOf(parts: CalendarParts): number {
  return new Date(Date.UTC(parts.year, parts.month - 1, parts.day)).getUTCDay();
}

// Adds `days` calendar days to a date's parts and returns the new parts.
function addCalendarDays(parts: CalendarParts, days: number): CalendarParts {
  const base = new Date(Date.UTC(parts.year, parts.month - 1, parts.day));
  base.setUTCDate(base.getUTCDate() + days);
  return { year: base.getUTCFullYear(), month: base.getUTCMonth() + 1, day: base.getUTCDate() };
}

// UTC day-number (days since epoch) of a calendar date — for day arithmetic.
function dayNumber(parts: CalendarParts): number {
  return Math.floor(Date.UTC(parts.year, parts.month - 1, parts.day) / DAY_MS);
}

function lastDayOfMonth(year: number, month: number /* 1-12 */): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

// Builds the absolute instant for `parts` + offset days at `time` in `tz`.
function occurrenceAt(parts: CalendarParts, offsetDays: number, time: string | undefined, tz: string): Date {
  const { hours, minutes } = parseHhMm(time ?? DEFAULT_TIME);
  const target = addCalendarDays(parts, offsetDays);
  return zonedTimeToUtc(target.year, target.month, target.day, hours, minutes, tz);
}

// Computes the next fire time for a recurring trigger, or null when the cadence
// is event-driven (calendar) or cannot be determined.
export function nextOccurrence(trigger: TriggerPolicy | undefined, from: Date = new Date()): Date | null {
  const recurrence: Recurrence | undefined = trigger?.recurrence;
  if (!recurrence) return null;

  const tz = recurrence.tz || DEFAULT_TZ;
  const time = recurrence.time;
  const fromParts = getDateParts(from, tz);

  switch (recurrence.kind) {
    case "weekly": {
      const weekday = ((recurrence.weekday ?? 1) % 7 + 7) % 7; // default Monday
      const diff = (weekday - weekdayOf(fromParts) + 7) % 7;
      let candidate = occurrenceAt(fromParts, diff, time, tz);
      if (candidate.getTime() < from.getTime()) candidate = occurrenceAt(fromParts, diff + 7, time, tz);
      return candidate;
    }

    case "biweekly": {
      const weekday = ((recurrence.weekday ?? 6) % 7 + 7) % 7; // default Saturday (US-001)

      // No anchor → behave like weekly on the target weekday.
      if (!recurrence.anchorDate) {
        const diff = (weekday - weekdayOf(fromParts) + 7) % 7;
        let candidate = occurrenceAt(fromParts, diff, time, tz);
        if (candidate.getTime() < from.getTime()) candidate = occurrenceAt(fromParts, diff + 7, time, tz);
        return candidate;
      }

      const anchorDate = new Date(recurrence.anchorDate);
      if (!Number.isFinite(anchorDate.getTime())) {
        const diff = (weekday - weekdayOf(fromParts) + 7) % 7;
        let candidate = occurrenceAt(fromParts, diff, time, tz);
        if (candidate.getTime() < from.getTime()) candidate = occurrenceAt(fromParts, diff + 7, time, tz);
        return candidate;
      }

      // Align the anchor onto the requested weekday, then step in 14-day cadence.
      const anchorParts = getDateParts(anchorDate, tz);
      const alignDiff = (weekday - weekdayOf(anchorParts) + 7) % 7;
      const aligned = addCalendarDays(anchorParts, alignDiff);

      const spanDays = dayNumber(fromParts) - dayNumber(aligned);
      const steps = spanDays <= 0 ? 0 : Math.ceil(spanDays / 14);
      let candidate = occurrenceAt(aligned, steps * 14, time, tz);
      if (candidate.getTime() < from.getTime()) candidate = occurrenceAt(aligned, (steps + 1) * 14, time, tz);
      return candidate;
    }

    case "monthlyFirstDay": {
      let year = fromParts.year;
      let month = fromParts.month;
      let candidate = zonedTimeToUtc(year, month, 1, parseHhMm(time).hours, parseHhMm(time).minutes, tz);
      if (candidate.getTime() < from.getTime()) {
        month += 1;
        if (month > 12) {
          month = 1;
          year += 1;
        }
        candidate = zonedTimeToUtc(year, month, 1, parseHhMm(time).hours, parseHhMm(time).minutes, tz);
      }
      return candidate;
    }

    case "monthlyLastDay": {
      let year = fromParts.year;
      let month = fromParts.month;
      const { hours, minutes } = parseHhMm(time);
      let candidate = zonedTimeToUtc(year, month, lastDayOfMonth(year, month), hours, minutes, tz);
      if (candidate.getTime() < from.getTime()) {
        month += 1;
        if (month > 12) {
          month = 1;
          year += 1;
        }
        candidate = zonedTimeToUtc(year, month, lastDayOfMonth(year, month), hours, minutes, tz);
      }
      return candidate;
    }

    case "calendar":
    default:
      // Event-driven (driven by calendar data) — not predictable here.
      return null;
  }
}

// True when the next occurrence falls on the same calendar day as `from`,
// evaluated in the recurrence timezone (default Asia/Seoul).
export function occursToday(trigger: TriggerPolicy | undefined, from: Date = new Date()): boolean {
  const next = nextOccurrence(trigger, from);
  if (!next) return false;
  const tz = trigger?.recurrence?.tz || DEFAULT_TZ;
  const nextParts = getDateParts(next, tz);
  const fromParts = getDateParts(from, tz);
  return nextParts.year === fromParts.year && nextParts.month === fromParts.month && nextParts.day === fromParts.day;
}

const KO_DATETIME = new Intl.DateTimeFormat("ko-KR", {
  month: "long",
  day: "numeric",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: DEFAULT_TZ,
});

// Human-readable ko-KR label for a computed next-occurrence date (Asia/Seoul).
export function formatNextOccurrence(date: Date | null): string {
  if (!date) return "예정 없음";
  try {
    return KO_DATETIME.format(date);
  } catch {
    return date.toLocaleString("ko-KR");
  }
}
