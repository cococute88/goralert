// GORALERT-ALERT-SYSTEM Sprint 1 (data foundation layer)
// Functional Firestore CRUD for the alert system. Mirrors the style of
// lib/firebase/firestore-repositories.ts: functional async functions (no
// classes), serverTimestamp + sanitizeFirestorePayload on writes, and a
// requireDb()/firestoreDb null-guard with safe fallbacks.

import {
  addDoc,
  deleteField,
  deleteDoc,
  getDoc,
  getDocs,
  limit as fsLimit,
  onSnapshot,
  orderBy,
  query,
  runTransaction,
  serverTimestamp,
  setDoc,
  startAfter,
  Timestamp,
  updateDoc,
  where,
  type DocumentData,
  type UpdateData,
  type QueryConstraint,
  type QueryDocumentSnapshot,
} from "firebase/firestore";
import { firestoreDb } from "@/lib/firebase/client";
import { sanitizeFirestorePayload } from "./firestore-payload.mjs";
import {
  defaultAlertSettingsData,
  normalizeAlertSettingsData,
} from "./alert-settings-data.mjs";
import {
  normalizePushDevices,
  normalizePushTokens,
  removePushRegistrationsData,
  resetPushRegistrationData,
  upsertPushDeviceData,
} from "./push-device-data.mjs";

export { sanitizeFirestorePayload } from "./firestore-payload.mjs";
import {
  alertRuleDoc,
  alertRulesCol,
  alertSettingsDoc,
  alertTemplateDoc,
  alertTemplatesCol,
  calendarAlertMarkDoc,
  calendarAlertMarksCol,
  notificationLogDoc,
  notificationLogsCol,
  testPushRequestDoc,
  testPushRequestsCol,
} from "./collections";
import type {
  AlertRule,
  AlertSettings,
  AlertTemplate,
  CalendarAlertMark,
  DeliveryChannel,
  MessageTemplate,
  NotificationLog,
  PushDevice,
} from "./types";
import { DURABLE_SCHEDULER_VERSION, initialSchedulerCursor } from "./schedule";

const DEFAULT_LOG_WINDOW = 200;
const TERMINAL_SCHEDULE_STATUSES = new Set([
  "sent", "partial_failure", "failed", "skipped", "cancelled", "disabled", "delivery_unknown",
  "condition_false", "no_data", "stale_data", "provider_error", "evaluation_error",
  "skipped_quiet_hours", "skipped_cooldown",
]);

function requireDb() {
  if (!firestoreDb) throw new Error("Firebase is not configured");
  return firestoreDb;
}

// Default settings returned when no settings doc exists yet.
function defaultAlertSettings(): AlertSettings {
  return defaultAlertSettingsData() as AlertSettings;
}

function normalizeAlertSettings(data: DocumentData): AlertSettings {
  const normalized = normalizeAlertSettingsData(data) as AlertSettings;
  return {
    ...normalized,
    pushTokens: normalizePushTokens(normalized.pushTokens),
    pushDevices: normalizePushDevices(normalized.pushDevices) as PushDevice[],
  };
}

// --- AlertRule ---------------------------------------------------------------

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== undefined)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function scheduleDefinition(rule: Pick<AlertRule, "trigger" | "condition">): string {
  const oneShotSelector = rule.trigger.mode === "once" && "selector" in rule.condition
    ? rule.condition.selector
    : undefined;
  return canonicalJson({ trigger: rule.trigger, oneShotSelector });
}

function persistedDate(value: unknown): Date | null {
  if (value instanceof Date) return Number.isFinite(value.getTime()) ? value : null;
  if (typeof value === "string") {
    const parsed = new Date(value);
    return Number.isFinite(parsed.getTime()) ? parsed : null;
  }
  if (value && typeof value === "object" && "toDate" in value) {
    const toDate = (value as { toDate?: unknown }).toDate;
    if (typeof toDate === "function") {
      const parsed = toDate.call(value) as Date;
      return parsed instanceof Date && Number.isFinite(parsed.getTime()) ? parsed : null;
    }
  }
  return null;
}

function localOccurrenceIso(instant: Date, timeZone: string): string {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const parts = Object.fromEntries(
    formatter.formatToParts(instant)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  const localAsUtc = Date.UTC(
    Number(parts.year), Number(parts.month) - 1, Number(parts.day),
    Number(parts.hour), Number(parts.minute), Number(parts.second),
  );
  const offsetMinutes = Math.round((localAsUtc - instant.getTime()) / 60_000);
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const absoluteOffset = Math.abs(offsetMinutes);
  const offset = `${sign}${String(Math.floor(absoluteOffset / 60)).padStart(2, "0")}:${String(absoluteOffset % 60).padStart(2, "0")}`;
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}${offset}`;
}

export async function saveAlertRule(uid: string, rule: AlertRule): Promise<void> {
  const db = requireDb();
  // Scheduler-owned fields must never be overwritten from a stale browser
  // snapshot. A schedule edit is committed with an explicit cancellation of
  // the old cursor so no occurrence disappears without permanent history.
  const {
    nextScheduledAt: _nextScheduledAt,
    lastProcessedScheduledAt: _lastProcessedScheduledAt,
    lastOccurrenceId: _lastOccurrenceId,
    scheduleStatus: _scheduleStatus,
    scheduleChangedAt: _scheduleChangedAt,
    lastTriggeredAt: _lastTriggeredAt,
    lastValue: _lastValue,
    engineVersion: _engineVersion,
    durableSchedulerVersion: _durableSchedulerVersion,
    schedulerMigration: _schedulerMigration,
    schedulerRecovery: _schedulerRecovery,
    schedulerError: _schedulerError,
    ...editableRule
  } = rule;
  const writeCutoff = new Date();
  const initialCursor = initialSchedulerCursor(rule.trigger, writeCutoff);
  if (rule.trigger.recurrence && !initialCursor) {
    throw new Error("유효한 다음 반복 알림 시각을 계산할 수 없습니다.");
  }
  const schedulerWrite = rule.trigger.recurrence && initialCursor
    ? {
        durableSchedulerVersion: DURABLE_SCHEDULER_VERSION,
        nextScheduledAt: Timestamp.fromDate(initialCursor),
      }
    : {};
  const payload = sanitizeFirestorePayload({
    ...editableRule,
    uid,
    updatedAt: serverTimestamp(),
    createdAt: rule.createdAt ?? serverTimestamp(),
  });
  const ruleRef = alertRuleDoc(db, uid, rule.id);
  await runTransaction(db, async (transaction) => {
    const currentSnap = await transaction.get(ruleRef);
    if (!currentSnap.exists()) {
      transaction.set(ruleRef, { ...payload, ...schedulerWrite }, { merge: true });
      return;
    }

    const current = currentSnap.data() as AlertRule;
    if (scheduleDefinition(current) === scheduleDefinition(rule)) {
      transaction.set(ruleRef, payload, { merge: true });
      return;
    }
    if (current.scheduleStatus === "processing") {
      throw new Error("처리 중인 알림은 현재 회차가 끝난 뒤 일정을 변경할 수 있습니다.");
    }

    const cursor = persistedDate(current.nextScheduledAt);
    let cancellationRef: ReturnType<typeof notificationLogDoc> | null = null;
    if (cursor) {
      const timezone = current.trigger?.recurrence?.tz || "Asia/Seoul";
      const eventId = `${rule.id}:${localOccurrenceIso(cursor, timezone)}`;
      cancellationRef = notificationLogDoc(db, uid, eventId);
      const cancellationSnap = await transaction.get(cancellationRef);
      if (cancellationSnap.exists() && !TERMINAL_SCHEDULE_STATUSES.has(cancellationSnap.data().status)) {
        throw new Error("처리 중인 알림은 현재 회차가 끝난 뒤 일정을 변경할 수 있습니다.");
      }
      if (cancellationSnap.exists()) cancellationRef = null;
      if (cancellationRef) {
        const message = current.delivery?.message ?? { title: current.name, body: current.name };
        transaction.set(cancellationRef, sanitizeFirestorePayload({
          id: eventId,
          eventId,
          ruleId: rule.id,
          kind: current.kind,
          firedAt: writeCutoff.toISOString(),
          evaluatedAt: writeCutoff.toISOString(),
          message,
          channels: [],
          isTest: false,
          ruleName: current.name,
          status: "cancelled",
          scheduledFor: cursor.toISOString(),
          timezone,
          completedAt: writeCutoff.toISOString(),
          attemptCount: 0,
          nextScheduleUpdated: true,
          failureCode: "schedule_changed",
          failureReason: "scheduled occurrence cancelled by an explicit rule schedule edit",
          createdAt: serverTimestamp(),
        }));
      }
    }

    transaction.set(ruleRef, {
      ...payload,
      ...(rule.trigger.recurrence
        ? schedulerWrite
        : {
            nextScheduledAt: deleteField(),
            durableSchedulerVersion: deleteField(),
          }),
      scheduleChangedAt: serverTimestamp(),
      scheduleStatus: "schedule_changed",
    }, { merge: true });
  });
}

export async function loadAlertRules(uid: string): Promise<AlertRule[]> {
  if (!firestoreDb) return [];
  const snap = await getDocs(query(alertRulesCol(firestoreDb, uid), orderBy("updatedAt", "desc")));
  return snap.docs.map((item) => item.data() as unknown as AlertRule);
}

export async function getAlertRule(uid: string, id: string): Promise<AlertRule | null> {
  if (!firestoreDb) return null;
  const snap = await getDoc(alertRuleDoc(firestoreDb, uid, id));
  return snap.exists() ? (snap.data() as unknown as AlertRule) : null;
}

export async function deleteAlertRule(uid: string, id: string): Promise<void> {
  await deleteDoc(alertRuleDoc(requireDb(), uid, id));
}

export async function setAlertRuleEnabled(uid: string, id: string, enabled: boolean): Promise<void> {
  await updateDoc(alertRuleDoc(requireDb(), uid, id), sanitizeFirestorePayload({
    enabled,
    updatedAt: serverTimestamp(),
  }) as UpdateData<DocumentData>);
}

// --- NotificationLog ---------------------------------------------------------
// History is 永久 보존 (permanent — never deleted). The UI windows the view via
// `limit`; full history remains queryable for search.

export async function appendNotificationLog(uid: string, log: NotificationLog): Promise<void> {
  const db = requireDb();
  const payload = sanitizeFirestorePayload({
    ...log,
    createdAt: log.createdAt ?? serverTimestamp(),
  });
  await setDoc(notificationLogDoc(db, uid, log.id), payload, { merge: true });
}

export async function loadNotificationLogs(uid: string, options: { limit?: number } = {}): Promise<NotificationLog[]> {
  if (!firestoreDb) return [];
  const max = options.limit ?? DEFAULT_LOG_WINDOW;
  const snap = await getDocs(query(notificationLogsCol(firestoreDb, uid), orderBy("firedAt", "desc"), fsLimit(max)));
  return snap.docs.map((item) => item.data() as unknown as NotificationLog);
}

// searchNotificationLogs searches the FULL retained history (REQ-016.3 /
// REQ-024.3 / REQ-024.4) — NOT just the most recent window. It pages through the
// collection newest-first via cursor pagination (startAfter), applying
// server-side filters (kind/isTest/firedAt range) backed by the composite
// indexes in firestore.indexes.json, and refines client-side for free text
// (ruleName/tickers/message — Firestore has no substring search) and per-channel
// status (status lives inside the channels[] array). A `ticker` filter uses the
// `tickers` array-contains index for an efficient server-side ticker-scoped
// search. Pagination stops once `limit` matches are found or `maxScan` docs have
// been examined (cost guard, REQ-024.3).
export async function searchNotificationLogs(
  uid: string,
  options: {
    text?: string;
    ticker?: string;
    kind?: NotificationLog["kind"];
    status?: "sent" | "failed";
    from?: string;
    to?: string;
    includeTest?: boolean;
    limit?: number;
    pageSize?: number;
    maxScan?: number;
  } = {},
): Promise<NotificationLog[]> {
  if (!firestoreDb) return [];
  const db = firestoreDb;
  const max = options.limit ?? DEFAULT_LOG_WINDOW;
  const pageSize = Math.max(1, options.pageSize ?? 300);
  // Cost guard: never scan more than this many docs in a single search call.
  const maxScan = Math.max(pageSize, options.maxScan ?? 5000);

  const tickerFilter = options.ticker?.trim();
  const useTicker = Boolean(tickerFilter);

  // Server-side constraints. When filtering by ticker we use the array-contains
  // index on `tickers` and move the remaining predicates client-side (avoids a
  // combinatorial explosion of composite indexes).
  const serverConstraints: QueryConstraint[] = [];
  if (useTicker) {
    serverConstraints.push(where("tickers", "array-contains", tickerFilter!.toUpperCase()));
  } else {
    if (options.kind) serverConstraints.push(where("kind", "==", options.kind));
    if (options.includeTest === false) serverConstraints.push(where("isTest", "==", false));
    if (options.from) serverConstraints.push(where("firedAt", ">=", options.from));
    if (options.to) serverConstraints.push(where("firedAt", "<=", options.to));
  }

  const needle = options.text?.trim().toLowerCase();
  const matches = (row: NotificationLog): boolean => {
    if (options.status === "sent" && !row.channels.some((channel) => channel.status === "sent")) return false;
    if (options.status === "failed" && !(
      row.status === "failed"
      || row.status === "partial_failure"
      || row.status === "delivery_unknown"
      || row.status === "provider_error"
      || row.status === "evaluation_error"
      || row.status === "stale_data"
      || row.status === "no_data"
      || row.channels.some((channel) => channel.status === "failed" || channel.status === "unknown")
    )) return false;
    if (needle) {
      const haystack = [row.ruleName ?? "", ...(row.tickers ?? []), row.message?.title ?? "", row.message?.body ?? ""]
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    // Predicates applied client-side only on the ticker fast-path.
    if (useTicker) {
      if (options.kind && row.kind !== options.kind) return false;
      if (options.includeTest === false && row.isTest) return false;
      if (options.from && row.firedAt < options.from) return false;
      if (options.to && row.firedAt > options.to) return false;
    }
    return true;
  };

  const results: NotificationLog[] = [];
  let cursor: QueryDocumentSnapshot<DocumentData> | undefined;
  let scanned = 0;

  while (results.length < max && scanned < maxScan) {
    const pageConstraints: QueryConstraint[] = [
      ...serverConstraints,
      orderBy("firedAt", "desc"),
      ...(cursor ? [startAfter(cursor)] : []),
      fsLimit(pageSize),
    ];
    const snap = await getDocs(query(notificationLogsCol(db, uid), ...pageConstraints));
    if (snap.empty) break;

    for (const docSnap of snap.docs) {
      const row = docSnap.data() as unknown as NotificationLog;
      if (matches(row)) {
        results.push(row);
        if (results.length >= max) break;
      }
    }

    scanned += snap.docs.length;
    cursor = snap.docs[snap.docs.length - 1];
    if (snap.docs.length < pageSize) break; // history exhausted
  }

  return results;
}

// --- AlertTemplate -----------------------------------------------------------
// Built-in templates (DEFAULT_TEMPLATES) are merged in by the UI layer and are
// NOT stored here unless the user clones one into their own favorites.

export async function saveAlertTemplate(uid: string, template: AlertTemplate): Promise<void> {
  const db = requireDb();
  const payload = sanitizeFirestorePayload({
    ...template,
    createdAt: template.createdAt ?? serverTimestamp(),
    updatedAt: serverTimestamp(),
  });
  await setDoc(alertTemplateDoc(db, uid, template.id), payload, { merge: true });
}

export async function loadAlertTemplates(uid: string): Promise<AlertTemplate[]> {
  if (!firestoreDb) return [];
  const snap = await getDocs(alertTemplatesCol(firestoreDb, uid));
  return snap.docs.map((item) => item.data() as unknown as AlertTemplate);
}

export async function deleteAlertTemplate(uid: string, id: string): Promise<void> {
  await deleteDoc(alertTemplateDoc(requireDb(), uid, id));
}

// --- AlertSettings -----------------------------------------------------------

export async function loadAlertSettings(uid: string): Promise<AlertSettings> {
  if (!firestoreDb) throw new Error("Firebase is not configured");
  const snap = await getDoc(alertSettingsDoc(firestoreDb, uid));
  if (!snap.exists()) return defaultAlertSettings();
  return normalizeAlertSettings(snap.data());
}

export function watchAlertSettings(
  uid: string,
  onChange: (
    settings: AlertSettings,
    metadata: { hasPendingWrites: boolean },
  ) => void,
  onError?: (error: Error) => void,
): () => void {
  if (!firestoreDb) {
    onError?.(new Error("Firebase is not configured"));
    return () => {};
  }
  return onSnapshot(
    alertSettingsDoc(firestoreDb, uid),
    { includeMetadataChanges: true },
    (snap) => onChange(
      snap.exists() ? normalizeAlertSettings(snap.data()) : defaultAlertSettings(),
      { hasPendingWrites: snap.metadata.hasPendingWrites },
    ),
    (error) => onError?.(error),
  );
}

export function watchAlertRules(
  uid: string,
  onChange: (rules: AlertRule[]) => void,
  onError?: (error: Error) => void,
): () => void {
  if (!firestoreDb) {
    onChange([]);
    return () => {};
  }
  return onSnapshot(
    alertRulesCol(firestoreDb, uid),
    (snap) => onChange(snap.docs.map((item) => item.data() as unknown as AlertRule)),
    (error) => onError?.(error),
  );
}

export async function saveAlertSettings(uid: string, partial: Partial<AlertSettings>): Promise<void> {
  const updates = Object.fromEntries(
    Object.entries(partial).map(([key, value]) => [key, value === undefined ? deleteField() : value]),
  );
  const payload = sanitizeFirestorePayload({
    ...updates,
    updatedAt: serverTimestamp(),
  });
  await setDoc(alertSettingsDoc(requireDb(), uid), payload, { merge: true });
}

export type PushRegistrationTarget = {
  deviceId?: string;
  // Used only for an exact legacy/current-token match. Never render or log it.
  token?: string;
};

export type PushRegistrationSnapshot = {
  pushTokens: string[];
  pushDevices: PushDevice[];
  removedCount?: number;
  changed?: boolean;
};

// Keep the legacy token array and the metadata list consistent in one retrying
// transaction. Firestore retries the callback when another browser changes the
// settings document concurrently, so a newly registered token is not lost.
export async function upsertPushDevice(
  uid: string,
  device: PushDevice,
  options: { requireExistingDeviceId?: boolean } = {},
): Promise<PushRegistrationSnapshot> {
  const db = requireDb();
  const ref = alertSettingsDoc(db, uid);
  return runTransaction(db, async (transaction) => {
    const snap = await transaction.get(ref);
    const current = snap.exists() ? snap.data() : {};
    const next = upsertPushDeviceData(current, device, new Date().toISOString(), options);
    if (next.changed) {
      transaction.set(ref, {
        pushTokens: next.pushTokens,
        pushDevices: next.pushDevices,
        updatedAt: serverTimestamp(),
      }, { merge: true });
    }
    return next as PushRegistrationSnapshot;
  });
}

export async function removePushRegistrations(
  uid: string,
  targets: PushRegistrationTarget[],
): Promise<PushRegistrationSnapshot> {
  const db = requireDb();
  const ref = alertSettingsDoc(db, uid);
  return runTransaction(db, async (transaction) => {
    const snap = await transaction.get(ref);
    const current = snap.exists() ? snap.data() : {};
    const next = removePushRegistrationsData(current, targets);
    // Writing an already-empty result is intentional: repeated requests stay
    // idempotent and never fall back to deleting an unrelated array element.
    transaction.set(ref, {
      pushTokens: next.pushTokens,
      pushDevices: next.pushDevices,
      updatedAt: serverTimestamp(),
    }, { merge: true });
    return next as PushRegistrationSnapshot;
  });
}

export async function resetPushRegistrations(uid: string): Promise<PushRegistrationSnapshot> {
  const db = requireDb();
  const ref = alertSettingsDoc(db, uid);
  return runTransaction(db, async (transaction) => {
    // The read makes this conflict with concurrent device registration. A retry
    // then clears the latest arrays instead of overwriting from stale state.
    await transaction.get(ref);
    const next = resetPushRegistrationData();
    transaction.set(ref, {
      ...next,
      updatedAt: serverTimestamp(),
    }, { merge: true });
    return next as PushRegistrationSnapshot;
  });
}

// --- CalendarAlertMark -------------------------------------------------------

export async function saveCalendarAlertMark(uid: string, mark: CalendarAlertMark): Promise<void> {
  const db = requireDb();
  const payload = sanitizeFirestorePayload({
    ...mark,
    markType: "bell" as const,
    createdAt: mark.createdAt ?? serverTimestamp(),
    updatedAt: serverTimestamp(),
  });
  await setDoc(calendarAlertMarkDoc(db, uid, mark.id), payload, { merge: true });
}

export async function loadCalendarAlertMarks(uid: string): Promise<CalendarAlertMark[]> {
  if (!firestoreDb) return [];
  const snap = await getDocs(calendarAlertMarksCol(firestoreDb, uid));
  return snap.docs.map((item) => item.data() as unknown as CalendarAlertMark);
}

export async function deleteCalendarAlertMark(uid: string, id: string): Promise<void> {
  await deleteDoc(calendarAlertMarkDoc(requireDb(), uid, id));
}

// --- Test-push request queue (browser -> engine bridge) ----------------------
//
// The settings "테스트 Push / Telegram" buttons DO NOT deliver in the browser.
// They enqueue a request document that the Python engine drains through the
// production delivery path (send_test_alert -> deliver -> PushChannel), so test
// and production share ONE send implementation. The UI then observes the same
// document for the engine-written outcome (results come from the channel's real
// return value — never fabricated client-side).

export type TestPushRequestStatus = "pending" | "done" | "failed" | "error";

export type TestPushRequestResult = {
  channel: DeliveryChannel;
  status: "sent" | "failed";
  error?: string;
};

export type TestPushRequest = {
  status: TestPushRequestStatus;
  channels: DeliveryChannel[];
  message?: MessageTemplate;
  results?: TestPushRequestResult[];
  logId?: string;
  error?: string;
};

// Enqueue a test send and return the new request's document id. The engine
// (alert_engine/test_push.py) picks it up and delivers via PushChannel.
export async function enqueueTestPushRequest(
  uid: string,
  input: { channels: DeliveryChannel[]; message?: MessageTemplate },
): Promise<string> {
  const payload = sanitizeFirestorePayload({
    status: "pending" as const,
    channels: input.channels,
    message: input.message,
    requestedAt: serverTimestamp(),
  });
  const ref = await addDoc(testPushRequestsCol(requireDb(), uid), payload);
  return ref.id;
}

// A pending (not-yet-drained) test-push request, surfaced in the 기록 탭 so an
// in-flight test shows as "아직 처리 중" before the engine writes its NotificationLog.
export type PendingTestPushRequest = {
  id: string;
  channels: DeliveryChannel[];
  message?: MessageTemplate;
};

// Observe the caller's still-pending test-push requests in real time. Used by the
// history screen to show a "처리 중" indicator while GitHub Actions/the engine is
// still running (before any NotificationLog exists). Returns an unsubscribe fn.
// Uses a single-field (status) equality filter — no composite index required.
export function watchPendingTestPushRequests(
  uid: string,
  onChange: (items: PendingTestPushRequest[]) => void,
): () => void {
  if (!firestoreDb) return () => {};
  const q = query(testPushRequestsCol(firestoreDb, uid), where("status", "==", "pending"));
  return onSnapshot(
    q,
    (snap) => {
      onChange(
        snap.docs.map((docSnap) => {
          const data = docSnap.data() as Partial<PendingTestPushRequest>;
          return {
            id: docSnap.id,
            channels: data.channels ?? [],
            message: data.message,
          };
        }),
      );
    },
    () => onChange([]),
  );
}

// Observe a test-push request until the engine finalizes it (or timeout).
// Resolves with the terminal document (status !== "pending") whose `results`
// reflect the real PushChannel/TelegramChannel outcome, or null on timeout.
export function waitForTestPushResult(
  uid: string,
  requestId: string,
  timeoutMs = 90_000,
): Promise<TestPushRequest | null> {
  const db = requireDb();
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: TestPushRequest | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      unsubscribe();
      resolve(value);
    };
    const timer = setTimeout(() => finish(null), timeoutMs);
    const unsubscribe = onSnapshot(
      testPushRequestDoc(db, uid, requestId),
      (snap) => {
        const data = snap.data() as TestPushRequest | undefined;
        if (data && data.status && data.status !== "pending") finish(data);
      },
      () => finish(null),
    );
  });
}
