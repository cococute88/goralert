import type {
  AlertRule,
  Condition,
  DateEventSelector,
} from "@/lib/alerts/types";
import { normalizeCalendarEventType } from "@/lib/calendar-contract";
import {
  buildCalendarDisplayAlertMatch,
  compareCalendarCellEvents,
  isCustomCalendarDisplayEvent,
  type CalendarDisplayEventLike,
} from "@/lib/calendar-display";

export type AlertableCalendarEvent = CalendarDisplayEventLike & {
  eventId: string;
  identityKeys: string[];
  source: "calendarEvents" | "calendarCustomEvents";
  star: boolean;
  heart: boolean;
};

function normalizedTypes(value: string | string[] | undefined): Set<string> {
  const raw = Array.isArray(value) ? value : value ? [value] : [];
  return new Set(raw.map((item) => {
    const normalized = normalizeCalendarEventType(item);
    return normalized === "buy_by_minus_1" ? "buy_by" : normalized;
  }));
}

export function calendarSelectorMatchesEvent(
  selector: DateEventSelector,
  event: AlertableCalendarEvent,
): boolean {
  if (selector.source !== event.source) return false;
  const match = selector.match ?? {};
  if (match.eventId && !event.identityKeys.includes(match.eventId)) return false;
  if (match.date && match.date !== event.date) return false;
  if (match.ticker && (event.ticker ?? "").trim().toUpperCase() !== match.ticker.trim().toUpperCase()) {
    return false;
  }
  const types = normalizedTypes(match.type);
  if (types.size > 0 && !types.has(normalizeCalendarEventType(event.type))) return false;
  if (
    match.titleContains?.trim()
    && !String(event.title ?? "").toLocaleLowerCase().includes(match.titleContains.trim().toLocaleLowerCase())
  ) {
    return false;
  }
  const marks = event.source === "calendarCustomEvents" ? [] : selector.markFilter ?? [];
  return marks.length === 0 || marks.some((mark) => Boolean(event[mark]));
}

function conditionTargetsEvent(condition: Condition, event: AlertableCalendarEvent): boolean {
  if ((condition.kind === "date" || condition.kind === "dividend") && condition.selector) {
    return calendarSelectorMatchesEvent(condition.selector, event);
  }
  if (condition.kind !== "composite") return false;
  return condition.conditions.some((child) => conditionTargetsEvent(child, event));
}

export function alertRuleTargetsCalendarEvent(
  rule: AlertRule,
  event: AlertableCalendarEvent,
): boolean {
  return rule.enabled && conditionTargetsEvent(rule.condition, event);
}

export function deriveAlertedCalendarEventIds(
  events: readonly AlertableCalendarEvent[],
  rules: readonly AlertRule[],
): Set<string> {
  return new Set(
    events
      .filter((event) => rules.some((rule) => alertRuleTargetsCalendarEvent(rule, event)))
      .map((event) => event.eventId),
  );
}

export function calendarEventIndicator(
  event: AlertableCalendarEvent,
  alertedEventIds: ReadonlySet<string>,
): "bell" | "star" | "heart" | null {
  if (alertedEventIds.has(event.eventId)) return "bell";
  if (event.star) return "star";
  if (event.heart) return "heart";
  return null;
}

export function compareAlertAwareCalendarCellEvents(
  left: AlertableCalendarEvent,
  right: AlertableCalendarEvent,
  alertedEventIds: ReadonlySet<string>,
): number {
  const alertPriority = Number(alertedEventIds.has(right.eventId)) - Number(alertedEventIds.has(left.eventId));
  return alertPriority || compareCalendarCellEvents(left, right);
}

export function buildSingleCalendarEventDraft(event: AlertableCalendarEvent): Partial<AlertRule> {
  const label = event.type === "custom" ? "사용자 일정" : event.type;
  const name = isCustomCalendarDisplayEvent(event)
    ? String(event.title ?? "").trim()
    : `${event.ticker ?? ""} ${calendarEventTypeName(event.type)}`.trim();
  const identity = event.identityKeys[0] || event.eventId;
  return {
    kind: "date",
    name,
    enabled: true,
    condition: {
      kind: "date",
      selector: {
        source: event.source,
        match: {
          eventId: identity,
          date: event.date,
          ...buildCalendarDisplayAlertMatch(event),
        },
      },
    },
    trigger: {
      mode: "once",
      recurrence: { kind: "calendar", time: "09:00", tz: "Asia/Seoul" },
    },
    delivery: {
      channels: ["telegram", "push"],
      message: {
        title: name || "캘린더 알림",
        body: `${name || label} (${event.date}) 알림입니다`,
      },
    },
  };
}

const EVENT_TYPE_NAMES: Record<string, string> = {
  ex_div: "배당락일",
  buy_by: "매수 마감",
  pay: "배당 지급",
  earnings: "실적 발표",
  custom: "사용자 일정",
};

export function calendarEventTypeName(type: string): string {
  return EVENT_TYPE_NAMES[type] ?? type;
}
