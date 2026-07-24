export type CalendarEventType = "ex_div" | "buy_by" | "pay" | "earnings" | "custom";
export type CalendarEventStatus = "confirmed" | "estimated";

export type LegacyCalendarEvent = {
  id: string;
  date: string;
  ticker: string;
  type: CalendarEventType | string;
  title?: string;
  canonicalEventId?: string;
  legacyEventId?: string;
  sourceKind?: string;
  star?: boolean;
  heart?: boolean;
  memo?: string;
};

export type CalendarCustomEvent = {
  id: string;
  date: string;
  ticker?: string;
  type: string;
  title: string;
};

export type CalendarEventMeta = {
  eventId?: string;
  canonicalEventId?: string;
  firestoreDocumentId?: string;
  ticker?: string;
  sourceKind?: string;
  star?: boolean;
  heart?: boolean;
  memo?: string;
};

export type ResolvedCalendarEvent = LegacyCalendarEvent & {
  source: "calendarEvents" | "calendarCustomEvents";
  star: boolean;
  heart: boolean;
};
