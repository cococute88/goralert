import type {
  AlertRule,
  Condition,
  DateEventSelector,
} from "@/lib/alerts/types";
import {
  calendarSelectorMatchTypes,
  calendarSelectorTitleContains,
  normalizeCalendarEventType,
} from "@/lib/calendar-contract";
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

function normalizedTypes(match: DateEventSelector["match"] | undefined): Set<string> {
  return new Set(calendarSelectorMatchTypes(match).map((item) => {
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
  const types = normalizedTypes(match);
  if (types.size > 0 && !types.has(normalizeCalendarEventType(event.type))) return false;
  const titleContains = calendarSelectorTitleContains(match);
  if (
    titleContains
    && !String(event.title ?? "").toLocaleLowerCase().includes(titleContains.toLocaleLowerCase())
  ) {
    return false;
  }
  const marks = event.source === "calendarCustomEvents" ? [] : selector.markFilter ?? [];
  return marks.length === 0 || marks.some((mark) => Boolean(event[mark]));
}

function conditionCalendarDates(
  condition: Condition,
  events: readonly AlertableCalendarEvent[],
): Set<string> | null {
  if ((condition.kind === "date" || condition.kind === "dividend") && condition.selector) {
    return new Set(
      events
        .filter((event) => calendarSelectorMatchesEvent(condition.selector!, event))
        .map((event) => event.date),
    );
  }
  if (condition.kind !== "composite" || condition.conditions.length === 0) return null;
  const childDates = condition.conditions.map((child) => conditionCalendarDates(child, events));
  if (condition.operator === "or") {
    if (childDates.some((dates) => dates === null)) return null;
    return new Set(childDates.flatMap((dates) => dates ? Array.from(dates) : []));
  }
  const constrained = childDates.filter((dates): dates is Set<string> => dates !== null);
  if (constrained.length === 0) return null;
  return new Set(
    Array.from(constrained[0]).filter(
      (date) => constrained.slice(1).every((dates) => dates.has(date)),
    ),
  );
}

function conditionTargetsEvent(
  condition: Condition,
  event: AlertableCalendarEvent,
  events: readonly AlertableCalendarEvent[],
): boolean {
  if ((condition.kind === "date" || condition.kind === "dividend") && condition.selector) {
    return calendarSelectorMatchesEvent(condition.selector, event);
  }
  if (condition.kind !== "composite") return false;
  const childTargetsEvent = condition.conditions.some(
    (child) => conditionTargetsEvent(child, event, events),
  );
  if (!childTargetsEvent) return false;
  if (condition.operator === "or") return true;
  const allowedDates = conditionCalendarDates(condition, events);
  return allowedDates === null || allowedDates.has(event.date);
}

export function alertRuleTargetsCalendarEvent(
  rule: AlertRule,
  event: AlertableCalendarEvent,
  events: readonly AlertableCalendarEvent[] = [event],
): boolean {
  return rule.enabled && conditionTargetsEvent(rule.condition, event, events);
}

export function deriveAlertedCalendarEventIds(
  events: readonly AlertableCalendarEvent[],
  rules: readonly AlertRule[],
): Set<string> {
  return new Set(
    events
      .filter((event) => rules.some((rule) => alertRuleTargetsCalendarEvent(rule, event, events)))
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

export function isSingleCalendarEventOccurrenceFuture(
  draft: Partial<AlertRule>,
  now = new Date(),
): boolean {
  const condition = draft.condition;
  if (condition?.kind !== "date" || !condition.selector) return false;
  const date = condition.selector.match?.date?.slice(0, 10) ?? "";
  const time = draft.trigger?.recurrence?.time ?? "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time)) return false;
  const seoulNow = new Date(now.getTime() + 9 * 60 * 60 * 1000).toISOString();
  return `${date}T${time}:00.000Z` > seoulNow;
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
