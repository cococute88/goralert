export type DatedCalendarItem = {
  date: string;
};

export function startOfCalendarMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

export function addCalendarMonths(month: Date, offset: number): Date {
  return new Date(month.getFullYear(), month.getMonth() + offset, 1);
}

export function calendarMonthKey(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  return `${year}-${month}`;
}

export function sortCalendarEventsByDate<T extends DatedCalendarItem>(events: readonly T[]): T[] {
  return events
    .map((event, index) => ({ event, index }))
    .sort(
      (left, right) =>
        left.event.date.localeCompare(right.event.date) || left.index - right.index,
    )
    .map(({ event }) => event);
}

export function calendarEventsForMonth<T extends DatedCalendarItem>(
  events: readonly T[],
  month: Date,
): T[] {
  const prefix = `${calendarMonthKey(month)}-`;
  return sortCalendarEventsByDate(events.filter((event) => event.date.startsWith(prefix)));
}

export function calendarEventsForDate<T extends DatedCalendarItem>(
  events: readonly T[],
  isoDate: string,
): T[] {
  return events.filter((event) => event.date === isoDate);
}

export function groupCalendarEventsByDate<T extends DatedCalendarItem>(
  events: readonly T[],
): Array<[string, T[]]> {
  const groups = new Map<string, T[]>();
  for (const event of events) {
    const current = groups.get(event.date);
    if (current) current.push(event);
    else groups.set(event.date, [event]);
  }
  return Array.from(groups.entries());
}
