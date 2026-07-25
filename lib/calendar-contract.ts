import type { AlertRule } from "@/lib/alerts/types";
import type { CalendarEventMeta, ResolvedCalendarEvent } from "@/lib/calendar-types";

type CalendarRecord = Record<string, unknown>;

const TYPE_ALIASES: Record<string, string> = {
  buy: "buy_by",
  buyby: "buy_by",
  "buy-by": "buy_by",
  buy_deadline: "buy_by",
  "buy-deadline": "buy_by",
  exdiv: "ex_div",
  "ex-div": "ex_div",
  "ex-dividend": "ex_div",
  ex_dividend: "ex_div",
  payment: "pay",
};

const IDENTITY_TYPE_ALIASES: Record<string, string> = {
  buy_by: "buy",
  pay: "payment",
};

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function normalizeCalendarEventType(value: unknown): string {
  const raw = text(value).toLowerCase().replace(/\s+/g, "_");
  return TYPE_ALIASES[raw] ?? raw;
}

function eventDate(event: CalendarRecord): string {
  return (text(event.date) || text(event.eventDate)).slice(0, 10);
}

function eventTicker(event: CalendarRecord, fallbackTicker = ""): string {
  return (text(event.ticker) || fallbackTicker).toUpperCase();
}

function eventType(event: CalendarRecord): string {
  return normalizeCalendarEventType(event.type || event.eventType);
}

function canonicalGeneratedId(event: CalendarRecord, fallbackTicker = ""): string {
  const ticker = eventTicker(event, fallbackTicker);
  const type = eventType(event);
  const date = eventDate(event);
  if (!ticker || !type || !date) return "";
  return `dividend:${ticker}:${IDENTITY_TYPE_ALIASES[type] ?? type}:${date}`;
}

export function calendarIdentityKeys(
  event: CalendarRecord,
  fallbackId = "",
  fallbackTicker = "",
): string[] {
  const keys = new Set([
    text(event.id),
    text(event.eventId),
    text(event.canonicalEventId),
    text(event.legacyEventId),
    text(event.firestoreDocumentId),
    fallbackId,
    canonicalGeneratedId(event, fallbackTicker),
  ]);
  const ticker = eventTicker(event, fallbackTicker);
  const type = eventType(event);
  const date = eventDate(event);
  if (ticker && type && date) {
    keys.add(`${ticker}-${type}-${date}`);
    keys.add(`${ticker}-${IDENTITY_TYPE_ALIASES[type] ?? type}-${date}`);
  }
  keys.delete("");
  return Array.from(keys);
}

export function findMatchingCalendarIdentityKey(
  identityKeys: Iterable<string>,
  candidateIds: Iterable<string>,
): string | null {
  const candidates = new Set(Array.from(candidateIds));
  for (const key of Array.from(identityKeys)) {
    if (candidates.has(key)) return key;
  }
  return null;
}

export function normalizeAuthoritativeCalendarEvent(
  raw: CalendarRecord,
  fallbackId = "",
  fallbackTicker = "",
  source: ResolvedCalendarEvent["source"] = "calendarEvents",
): ResolvedCalendarEvent | null {
  const sourceKind = text(raw.sourceKind).toLowerCase();
  const rawSource = text(raw.source).toLowerCase();
  if (sourceKind === "sample" || rawSource === "sample" || rawSource === "mock") return null;
  const date = eventDate(raw);
  const type = eventType(raw);
  if (!date || !type) return null;
  const id =
    text(raw.id) ||
    text(raw.canonicalEventId) ||
    text(raw.eventId) ||
    fallbackId ||
    canonicalGeneratedId(raw, fallbackTicker);
  if (!id) return null;
  return {
    ...(raw as Omit<ResolvedCalendarEvent, "id" | "date" | "ticker" | "type" | "source" | "star" | "heart">),
    id,
    date,
    ticker: eventTicker(raw, fallbackTicker),
    type,
    title: text(raw.title),
    source,
    star: Boolean(raw.star),
    heart: Boolean(raw.heart),
  };
}

function dedupeEvents(events: ResolvedCalendarEvent[]): ResolvedCalendarEvent[] {
  const seen = new Set<string>();
  return events.filter((event) => {
    const identity = canonicalGeneratedId(event as unknown as CalendarRecord) || event.canonicalEventId || event.id;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

export function resolveGeneratedCalendarEvents(
  cacheEvents: ResolvedCalendarEvent[],
  legacyEvents: ResolvedCalendarEvent[],
  metadata: CalendarEventMeta[],
  cacheDocumentTickers: Iterable<string> = [],
): ResolvedCalendarEvent[] {
  const cacheTickers = new Set(
    [
      ...Array.from(cacheDocumentTickers, (ticker) => text(ticker).toUpperCase()),
      ...cacheEvents.map((event) => text(event.ticker).toUpperCase()),
    ].filter(Boolean),
  );
  const authoritative = dedupeEvents([
    ...cacheEvents,
    ...legacyEvents.filter((event) => !cacheTickers.has(text(event.ticker).toUpperCase())),
  ]);
  const metaLookup = new Map<string, CalendarEventMeta[]>();
  metadata.forEach((meta) => {
    calendarIdentityKeys(meta as CalendarRecord).forEach((key) => {
      metaLookup.set(key, [...(metaLookup.get(key) ?? []), meta]);
    });
  });

  return authoritative.map((event) => {
    const matches = new Set<CalendarEventMeta>();
    calendarIdentityKeys(event as unknown as CalendarRecord).forEach((key) => {
      (metaLookup.get(key) ?? []).forEach((meta) => matches.add(meta));
    });
    const rows = Array.from(matches);
    const memo = rows.map((meta) => text(meta.memo)).find(Boolean) || event.memo;
    return {
      ...event,
      star: event.star || rows.some((meta) => Boolean(meta.star)),
      heart: event.heart || rows.some((meta) => Boolean(meta.heart)),
      ...(memo ? { memo } : {}),
    };
  });
}

function subtractCalendarDay(date: string): string {
  const parsed = new Date(`${date}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime())) return "";
  parsed.setUTCDate(parsed.getUTCDate() - 1);
  return parsed.toISOString().slice(0, 10);
}

export function calendarNotificationDates(
  rule: AlertRule,
  events: ResolvedCalendarEvent[],
): string[] {
  if (
    (rule.condition.kind !== "date" && rule.condition.kind !== "dividend")
    || !rule.condition.selector
  ) return [];
  const selector = rule.condition.selector;
  const match = selector.match ?? {};
  const rawTypes = Array.isArray(match.type) ? match.type : match.type ? [match.type] : [];
  const selectedTypes = new Set(
    rawTypes.map((value) => value === "buy_by_minus_1" ? value : normalizeCalendarEventType(value)),
  );
  const ticker = text(match.ticker).toUpperCase();
  const contains = text(match.titleContains).toLocaleLowerCase();
  const marks = selector.source === "calendarCustomEvents" ? [] : selector.markFilter ?? [];
  const dates: string[] = [];

  for (const event of events) {
    if (event.source !== selector.source) continue;
    if (ticker && event.ticker.toUpperCase() !== ticker) continue;
    if (contains && !text(event.title).toLocaleLowerCase().includes(contains)) continue;
    if (marks.length > 0 && !marks.some((mark) => Boolean(event[mark]))) continue;

    const type = normalizeCalendarEventType(event.type);
    const selectsBody = selectedTypes.size === 0 || selectedTypes.has(type);
    const selectsMinusOne = selectedTypes.has("buy_by_minus_1") && type === "buy_by";
    if (!selectsBody && !selectsMinusOne) continue;
    if (selectsBody) dates.push(event.date);
    if (selectsMinusOne) dates.push(subtractCalendarDay(event.date));
  }
  return Array.from(new Set(dates.filter(Boolean))).sort();
}
