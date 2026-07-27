import { collection, doc, getDoc, getDocs } from "firebase/firestore";
import { firestoreDb } from "@/lib/firebase/client";
import {
  normalizeAuthoritativeCalendarEvent,
  resolveGeneratedCalendarEvents,
} from "@/lib/calendar-contract";
import {
  DEFAULT_CALENDAR_PORTFOLIO_ID,
  filterCalendarDisplayEvents,
  resolveCalendarDisplayTickerUniverse,
  uniqueCalendarDisplayTickers,
  type CalendarDisplayTickerUniverse,
} from "@/lib/calendar-display";
import type {
  CalendarCustomEvent,
  CalendarEventMeta,
  LegacyCalendarEvent,
  ResolvedCalendarEvent,
} from "@/lib/calendar-types";

function requireDb() {
  if (!firestoreDb) throw new Error("Firebase is not configured");
  return firestoreDb;
}

async function activePortfolioId(uid: string): Promise<string> {
  const snap = await getDoc(doc(requireDb(), "users", uid, "calendarSettings", "default"));
  const value = snap.exists() ? snap.data().activePortfolioId : null;
  return typeof value === "string" && value.trim() ? value.trim() : DEFAULT_CALENDAR_PORTFOLIO_ID;
}

function calendarCollection(uid: string, portfolioId: string, name: string) {
  const db = requireDb();
  return portfolioId === DEFAULT_CALENDAR_PORTFOLIO_ID
    ? collection(db, "users", uid, name)
    : collection(db, "users", uid, "calendarPortfolios", portfolioId, name);
}

function metadataFromSnapshots(
  snapshots: Array<{ id: string; data: () => Record<string, unknown> }>,
): CalendarEventMeta[] {
  return snapshots.map((item) => ({
    eventId: item.id,
    firestoreDocumentId: item.id,
    ...item.data(),
  }) as CalendarEventMeta);
}

type ResolvedCalendarLoad = {
  events: ResolvedCalendarEvent[];
  legacyEvents: ResolvedCalendarEvent[];
};

async function loadResolvedCalendarEventsForPortfolio(
  uid: string,
  portfolioId: string,
): Promise<ResolvedCalendarLoad> {
  const isDefault = portfolioId === DEFAULT_CALENDAR_PORTFOLIO_ID;
  const metadataName = isDefault ? "calendarEvents" : "calendarEventMetas";

  const [metadataSnap, cacheSnap, customSnap] = await Promise.all([
    getDocs(calendarCollection(uid, portfolioId, metadataName)),
    getDocs(calendarCollection(uid, portfolioId, "calendarCache")),
    getDocs(calendarCollection(uid, portfolioId, "calendarCustomEvents")),
  ]);
  const metadata = metadataFromSnapshots(metadataSnap.docs);

  const cacheEvents: ResolvedCalendarEvent[] = [];
  const cacheDocumentTickers = new Set<string>();
  cacheSnap.docs.forEach((cacheDoc) => {
    const data = cacheDoc.data();
    const ticker = (typeof data.ticker === "string" ? data.ticker : cacheDoc.id).trim().toUpperCase();
    if (ticker) cacheDocumentTickers.add(ticker);
    if (!Array.isArray(data.events)) return;
    data.events.forEach((raw) => {
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) return;
      const event = normalizeAuthoritativeCalendarEvent(
        raw as Record<string, unknown>,
        "",
        ticker,
        "calendarEvents",
      );
      if (event) cacheEvents.push(event);
    });
  });

  const legacyEvents = isDefault
    ? metadataSnap.docs.flatMap((item) => {
        const event = normalizeAuthoritativeCalendarEvent(
          { ...item.data(), firestoreDocumentId: item.id },
          item.id,
          "",
          "calendarEvents",
        );
        return event ? [event] : [];
      })
    : [];
  const generated = resolveGeneratedCalendarEvents(
    cacheEvents,
    legacyEvents,
    metadata,
    cacheDocumentTickers,
  );

  const custom = customSnap.docs.flatMap((item) => {
    const event = normalizeAuthoritativeCalendarEvent(
      item.data(),
      item.id,
      "",
      "calendarCustomEvents",
    );
    return event ? [event] : [];
  });
  const events = [...generated, ...custom].sort(
    (a, b) => a.date.localeCompare(b.date) || a.ticker.localeCompare(b.ticker) || a.type.localeCompare(b.type),
  );
  return { events, legacyEvents };
}

export async function loadResolvedCalendarEvents(uid: string): Promise<ResolvedCalendarEvent[]> {
  const portfolioId = await activePortfolioId(uid);
  return (await loadResolvedCalendarEventsForPortfolio(uid, portfolioId)).events;
}

function stringArrayValues(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function legacyPortfolioTickers(value: unknown): string[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.values(value as Record<string, unknown>).flatMap(stringArrayValues);
}

async function loadCalendarDisplayTickerUniverse(
  uid: string,
  portfolioId: string,
  legacyEvents: ResolvedCalendarEvent[],
  resolvedEvents: ResolvedCalendarEvent[],
): Promise<CalendarDisplayTickerUniverse> {
  const db = requireDb();
  const isDefault = portfolioId === DEFAULT_CALENDAR_PORTFOLIO_ID;
  if (!isDefault) {
    const manualSnap = await getDoc(
      doc(db, "users", uid, "calendarPortfolios", portfolioId, "settings", "tickers"),
    );
    return resolveCalendarDisplayTickerUniverse({
      portfolioId,
      manualOverride: manualSnap.exists() ? manualSnap.data() : null,
      portfolioEventTickers: uniqueCalendarDisplayTickers(
        resolvedEvents
          .filter((event) => event.type !== "custom")
          .map((event) => event.ticker),
      ),
    });
  }

  const [manualSnap, portfoliosSnap, memosSnap] = await Promise.all([
    getDoc(doc(db, "users", uid, "calendarSettings", "manualTickers")),
    getDoc(doc(db, "users", uid, "legacyDividendCalendarMeta", "portfolios")),
    getDoc(doc(db, "users", uid, "legacyDividendCalendarMeta", "memos")),
  ]);
  const portfolioItems = portfoliosSnap.exists() ? portfoliosSnap.data().items : null;
  const memoItems = memosSnap.exists() ? memosSnap.data().items : null;

  return resolveCalendarDisplayTickerUniverse({
    portfolioId,
    manualOverride: manualSnap.exists() ? manualSnap.data() : null,
    legacyPortfolioTickers: legacyPortfolioTickers(portfolioItems),
    legacyEventTickers: uniqueCalendarDisplayTickers(
      legacyEvents
        .filter((event) => event.type !== "custom")
        .map((event) => event.ticker),
    ),
    legacyMemoKeys:
      memoItems && typeof memoItems === "object" && !Array.isArray(memoItems)
        ? Object.keys(memoItems as Record<string, unknown>)
        : [],
    portfolioEventTickers: uniqueCalendarDisplayTickers(
      resolvedEvents
        .filter((event) => event.type !== "custom")
        .map((event) => event.ticker),
    ),
  });
}

export async function loadCalendarDisplayEvents(uid: string): Promise<ResolvedCalendarEvent[]> {
  return (await loadCalendarDisplaySnapshot(uid)).events;
}

export async function loadCalendarDisplaySnapshot(
  uid: string,
): Promise<{ events: ResolvedCalendarEvent[]; portfolioId: string }> {
  const portfolioId = await activePortfolioId(uid);
  const resolved = await loadResolvedCalendarEventsForPortfolio(uid, portfolioId);
  const universe = await loadCalendarDisplayTickerUniverse(
    uid,
    portfolioId,
    resolved.legacyEvents,
    resolved.events,
  );
  return {
    portfolioId,
    events: filterCalendarDisplayEvents(resolved.events, universe.tickers),
  };
}

export async function loadLegacyImportedCalendarEvents(uid: string): Promise<LegacyCalendarEvent[]> {
  return (await loadResolvedCalendarEvents(uid)).filter((event) => event.source === "calendarEvents");
}

export async function loadCalendarCustomEvents(uid: string): Promise<CalendarCustomEvent[]> {
  return (await loadResolvedCalendarEvents(uid))
    .filter((event) => event.source === "calendarCustomEvents")
    .map((event) => ({
      id: event.id,
      date: event.date,
      ticker: event.ticker || undefined,
      type: event.type,
      title: event.title ?? "",
    }));
}

export async function loadCalendarEventMetas(uid: string): Promise<CalendarEventMeta[]> {
  const portfolioId = await activePortfolioId(uid);
  const isDefault = portfolioId === DEFAULT_CALENDAR_PORTFOLIO_ID;
  const name = isDefault ? "calendarEvents" : "calendarEventMetas";
  const snap = await getDocs(calendarCollection(uid, portfolioId, name));
  return metadataFromSnapshots(snap.docs);
}
