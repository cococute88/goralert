export const DEFAULT_CALENDAR_PORTFOLIO_ID = "default";
export const MANUAL_CALENDAR_TICKERS_SOURCE = "manual-calendar-tickers";
export const MANUAL_CALENDAR_TICKERS_VERSION = 2;

export type CalendarDisplayTickerSource =
  | "manual"
  | "portfolio-events"
  | "legacy-portfolios"
  | "legacy-events"
  | "legacy-memos"
  | "empty";

export type CalendarDisplayTickerUniverseInput = {
  portfolioId: string;
  manualOverride?: unknown;
  portfolioEventTickers?: readonly string[];
  legacyPortfolioTickers?: readonly string[];
  legacyEventTickers?: readonly string[];
  legacyMemoKeys?: readonly string[];
};

export type CalendarDisplayTickerUniverse = {
  source: CalendarDisplayTickerSource;
  tickers: string[];
};

export type CalendarDisplayEventLike = {
  id?: string;
  eventId?: string;
  canonicalEventId?: string;
  date: string;
  ticker?: string;
  type: string;
  title?: string;
  source?: string;
  sourceKind?: string;
  star?: boolean;
  heart?: boolean;
};

export type CalendarDayCellModel<T extends CalendarDisplayEventLike> = {
  customEvents: T[];
  regularEvents: T[];
  visibleRegularEvents: T[];
  regularOverflowCount: number;
};

const TYPE_PRIORITY: Record<string, number> = {
  ex_div: 0,
  buy_by: 1,
  pay: 2,
  earnings: 3,
};

function normalizeTicker(value: unknown): string {
  return typeof value === "string" ? value.trim().toUpperCase() : "";
}

export function uniqueCalendarDisplayTickers(values: readonly string[]): string[] {
  const seen = new Set<string>();
  const tickers: string[] = [];
  for (const value of values) {
    const ticker = normalizeTicker(value);
    if (ticker && !seen.has(ticker)) {
      seen.add(ticker);
      tickers.push(ticker);
    }
  }
  return tickers;
}

type ManualTickerOverride = {
  valid: boolean;
  tickers: string[];
};

function readManualTickerOverride(
  value: unknown,
  namedPortfolio: boolean,
): ManualTickerOverride {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { valid: false, tickers: [] };
  }
  const record = value as Record<string, unknown>;
  if (!Array.isArray(record.tickers)) return { valid: false, tickers: [] };
  if (
    !namedPortfolio
    && (
      record.source !== MANUAL_CALENDAR_TICKERS_SOURCE
      || typeof record.version !== "number"
      || record.version < MANUAL_CALENDAR_TICKERS_VERSION
    )
  ) return { valid: false, tickers: [] };
  return {
    valid: true,
    tickers: uniqueCalendarDisplayTickers(record.tickers.map(String)),
  };
}

export function resolveCalendarDisplayTickerUniverse(
  input: CalendarDisplayTickerUniverseInput,
): CalendarDisplayTickerUniverse {
  const namedPortfolio = input.portfolioId !== DEFAULT_CALENDAR_PORTFOLIO_ID;
  const manual = readManualTickerOverride(input.manualOverride, namedPortfolio);
  if (manual.valid) return { source: "manual", tickers: manual.tickers };
  if (namedPortfolio) {
    const portfolioEvents = uniqueCalendarDisplayTickers(input.portfolioEventTickers ?? []);
    return portfolioEvents.length > 0
      ? { source: "portfolio-events", tickers: portfolioEvents }
      : { source: "empty", tickers: [] };
  }

  const legacyPortfolios = uniqueCalendarDisplayTickers(input.legacyPortfolioTickers ?? []);
  if (legacyPortfolios.length > 0) {
    return { source: "legacy-portfolios", tickers: legacyPortfolios };
  }

  const legacyEvents = uniqueCalendarDisplayTickers(input.legacyEventTickers ?? []);
  if (legacyEvents.length > 0) return { source: "legacy-events", tickers: legacyEvents };

  const legacyMemos = uniqueCalendarDisplayTickers(input.legacyMemoKeys ?? []);
  if (legacyMemos.length > 0) return { source: "legacy-memos", tickers: legacyMemos };

  const portfolioEvents = uniqueCalendarDisplayTickers(input.portfolioEventTickers ?? []);
  if (portfolioEvents.length > 0) {
    return { source: "portfolio-events", tickers: portfolioEvents };
  }

  return { source: "empty", tickers: [] };
}

export function isCustomCalendarDisplayEvent(event: CalendarDisplayEventLike): boolean {
  return (
    event.type === "custom"
    || event.source === "calendarCustomEvents"
    || event.sourceKind === "custom"
  );
}

export function buildCalendarDisplayAlertMatch(
  event: CalendarDisplayEventLike,
): { ticker?: string; type?: string; titleContains?: string } {
  const ticker = normalizeTicker(event.ticker);
  const title = String(event.title ?? "").trim();
  return {
    ...(ticker ? { ticker } : {}),
    ...(event.type ? { type: event.type } : {}),
    ...(isCustomCalendarDisplayEvent(event) && title ? { titleContains: title } : {}),
  };
}

function displayIdentity(event: CalendarDisplayEventLike): string {
  return String(event.canonicalEventId || event.eventId || event.id || "");
}

export function filterCalendarDisplayEvents<T extends CalendarDisplayEventLike>(
  events: readonly T[],
  tickers: readonly string[],
): T[] {
  const activeTickers = new Set(uniqueCalendarDisplayTickers(tickers));
  const byIdentity = new Map<string, T>();
  const withoutIdentity: T[] = [];

  for (const event of events) {
    const custom = isCustomCalendarDisplayEvent(event);
    const ticker = normalizeTicker(event.ticker);
    if (!custom && ticker && !activeTickers.has(ticker)) continue;
    if (!custom && !ticker) continue;

    const identity = displayIdentity(event);
    if (!identity) {
      withoutIdentity.push(event);
      continue;
    }
    const existing = byIdentity.get(identity);
    if (!existing || event.source === "calendarCustomEvents") {
      byIdentity.set(identity, event);
    }
  }

  return [...Array.from(byIdentity.values()), ...withoutIdentity].sort(
    (left, right) =>
      left.date.localeCompare(right.date)
      || normalizeTicker(left.ticker).localeCompare(normalizeTicker(right.ticker))
      || left.type.localeCompare(right.type)
      || displayIdentity(left).localeCompare(displayIdentity(right)),
  );
}

function markPriority(event: CalendarDisplayEventLike): number {
  if (event.star) return 0;
  if (event.heart) return 1;
  return 2;
}

export function compareCalendarCellEvents(
  left: CalendarDisplayEventLike,
  right: CalendarDisplayEventLike,
): number {
  return (
    markPriority(left) - markPriority(right)
    || (TYPE_PRIORITY[left.type] ?? 99) - (TYPE_PRIORITY[right.type] ?? 99)
    || normalizeTicker(left.ticker).localeCompare(normalizeTicker(right.ticker))
    || displayIdentity(left).localeCompare(displayIdentity(right))
  );
}

export function buildCalendarDayCellModel<T extends CalendarDisplayEventLike>(
  events: readonly T[],
  regularLimit = 3,
): CalendarDayCellModel<T> {
  const customEvents = events
    .filter(isCustomCalendarDisplayEvent)
    .slice()
    .sort(
      (left, right) =>
        String(left.title ?? "").localeCompare(String(right.title ?? ""))
        || displayIdentity(left).localeCompare(displayIdentity(right)),
    );
  const regularEvents = events
    .filter((event) => !isCustomCalendarDisplayEvent(event))
    .slice()
    .sort(compareCalendarCellEvents);
  const visibleRegularEvents = regularEvents.slice(0, regularLimit);

  return {
    customEvents,
    regularEvents,
    visibleRegularEvents,
    regularOverflowCount: Math.max(0, regularEvents.length - visibleRegularEvents.length),
  };
}
