export type CalendarEventType = "ex_div" | "buy_by" | "pay" | "earnings" | "custom";
export type CalendarEventStatus = "confirmed" | "estimated";

export type LegacyCalendarEvent = {
  id: string;
  date: string;
  ticker: string;
  type: CalendarEventType | string;
  title?: string;
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
  star?: boolean;
  heart?: boolean;
};
