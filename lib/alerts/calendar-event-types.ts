import type { CalendarEventType } from "@/lib/calendar-types";

// These are the event codes written by the calendar repositories and consumed
// by the Python alert engine. `buy_by_minus_1` is alert-only: it derives its
// notification date from a `buy_by` event and is never written to calendar data.
// Keep the display mapping here rather than making users type implementation
// codes into a form.
export const BUY_BY_MINUS_ONE_EVENT_TYPE = "buy_by_minus_1" as const;

export type AlertCalendarEventType = CalendarEventType | typeof BUY_BY_MINUS_ONE_EVENT_TYPE;

export const CALENDAR_EVENT_TYPE_OPTIONS = [
  { value: "ex_div", label: "배당락일" },
  { value: "buy_by", label: "매수 마감일" },
  { value: BUY_BY_MINUS_ONE_EVENT_TYPE, label: "매수 마감일-1" },
  { value: "pay", label: "배당 지급일" },
  { value: "earnings", label: "실적 발표" },
  { value: "custom", label: "사용자 일정" },
] as const satisfies ReadonlyArray<{ value: AlertCalendarEventType; label: string }>;

export const CALENDAR_EVENT_TYPES = CALENDAR_EVENT_TYPE_OPTIONS.map((option) => option.value) as AlertCalendarEventType[];

const LEGACY_EVENT_TYPE_ALIASES: Record<string, CalendarEventType> = {
  "ex-dividend": "ex_div",
  "buy-deadline": "buy_by",
};

export function normalizeCalendarEventTypes(value: unknown): string[] {
  const raw = Array.isArray(value) ? value : value === undefined || value === null ? [] : [value];
  const normalized = raw
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => LEGACY_EVENT_TYPE_ALIASES[item] ?? item);
  return Array.from(new Set(normalized));
}

export function isKnownCalendarEventType(value: string): value is AlertCalendarEventType {
  return (CALENDAR_EVENT_TYPES as string[]).includes(value);
}
