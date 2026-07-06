import { collection, getDocs } from "firebase/firestore";
import { firestoreDb } from "@/lib/firebase/client";
import type {
  LegacyCalendarEvent,
  CalendarCustomEvent,
  CalendarEventMeta,
} from "@/lib/calendar-types";

function requireDb() {
  if (!firestoreDb) throw new Error("Firebase is not configured");
  return firestoreDb;
}

export async function loadLegacyImportedCalendarEvents(
  uid: string
): Promise<LegacyCalendarEvent[]> {
  const db = requireDb();
  const snap = await getDocs(
    collection(db, "users", uid, "calendarEvents")
  );
  return snap.docs.map((doc) => ({ id: doc.id, ...doc.data() }) as LegacyCalendarEvent);
}

export async function loadCalendarCustomEvents(
  uid: string
): Promise<CalendarCustomEvent[]> {
  const db = requireDb();
  const snap = await getDocs(
    collection(db, "users", uid, "calendarCustomEvents")
  );
  return snap.docs.map((doc) => ({ id: doc.id, ...doc.data() }) as CalendarCustomEvent);
}

export async function loadCalendarEventMetas(
  uid: string
): Promise<CalendarEventMeta[]> {
  const db = requireDb();
  const snap = await getDocs(
    collection(db, "users", uid, "calendarEventMetas")
  );
  return snap.docs.map((doc) => ({ eventId: doc.id, ...doc.data() }) as CalendarEventMeta);
}
