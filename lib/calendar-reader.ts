import { collection, doc, getDoc, getDocs } from "firebase/firestore";
import { firestoreDb } from "@/lib/firebase/client";
import {
  normalizeAuthoritativeCalendarEvent,
  resolveGeneratedCalendarEvents,
} from "@/lib/calendar-contract";
import type {
  CalendarCustomEvent,
  CalendarEventMeta,
  LegacyCalendarEvent,
  ResolvedCalendarEvent,
} from "@/lib/calendar-types";

const DEFAULT_PORTFOLIO_ID = "default";

function requireDb() {
  if (!firestoreDb) throw new Error("Firebase is not configured");
  return firestoreDb;
}

async function activePortfolioId(uid: string): Promise<string> {
  const snap = await getDoc(doc(requireDb(), "users", uid, "calendarSettings", "default"));
  const value = snap.exists() ? snap.data().activePortfolioId : null;
  return typeof value === "string" && value.trim() ? value.trim() : DEFAULT_PORTFOLIO_ID;
}

function calendarCollection(uid: string, portfolioId: string, name: string) {
  const db = requireDb();
  return portfolioId === DEFAULT_PORTFOLIO_ID
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

export async function loadResolvedCalendarEvents(uid: string): Promise<ResolvedCalendarEvent[]> {
  const portfolioId = await activePortfolioId(uid);
  const isDefault = portfolioId === DEFAULT_PORTFOLIO_ID;
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
  return [...generated, ...custom].sort(
    (a, b) => a.date.localeCompare(b.date) || a.ticker.localeCompare(b.ticker) || a.type.localeCompare(b.type),
  );
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
  const isDefault = portfolioId === DEFAULT_PORTFOLIO_ID;
  const name = isDefault ? "calendarEvents" : "calendarEventMetas";
  const snap = await getDocs(calendarCollection(uid, portfolioId, name));
  return metadataFromSnapshots(snap.docs);
}
