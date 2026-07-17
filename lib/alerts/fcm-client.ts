// GORALERT-ALERT-SYSTEM (REQ-039 / REQ-045)
// Real browser FCM (Firebase Cloud Messaging) client token registration.
//
// Flow (registerPushToken) — SERVICE WORKER IS REGISTERED FIRST:
//   1. Guard SSR / unsupported environments (iOS Safari, no SW, no Notification).
//   2. Register the service worker (public/firebase-messaging-sw.js) and wait
//      until it is ACTIVE. getToken() needs an active worker, so this precedes
//      everything else.
//   3. Request Notification permission (only after the SW is active).
//   4. Call getToken() with the VAPID key (NEXT_PUBLIC_FIREBASE_VAPID_KEY),
//      passing the registered worker.
//   5. Persist the token via saveAlertSettings (append to pushTokens, deduped).
//
// The service worker is ALSO registered automatically on app load via
// components/alerts/PushServiceWorker.tsx (no user gesture / permission needed),
// so navigator.serviceWorker.getRegistration() resolves before the user opens
// Settings. Every step logs to the console under the "[push]" prefix.
//
// All failure paths return { ok: false, error } with a clear Korean message so
// the settings UI can show a friendly toast. The Python engine (alert_engine/)
// reads users/{uid}/alertSettings.pushTokens to deliver push via the shared
// service account — this is the web side that registers those tokens.

import { deleteToken, getToken, isSupported, onMessage, type MessagePayload, type Messaging } from "firebase/messaging";
import { firebaseApp, isFirebaseConfigured } from "@/lib/firebase/client";
import { generateId } from "./id";
import {
  loadAlertSettings,
  removePushRegistrations,
  upsertPushDevice,
  type PushRegistrationSnapshot,
} from "./repositories";
import type {
  PushDevice,
  PushDeviceBrowser,
  PushDevicePlatform,
  PushDeviceType,
} from "./types";

export type RegisterPushResult = {
  ok: boolean;
  deviceId?: string;
  snapshot?: PushRegistrationSnapshot;
  localCleanupFailed?: boolean;
  error?: string;
};

export type CurrentPushRegistration = {
  deviceId?: string;
  token?: string;
  snapshot?: PushRegistrationSnapshot;
  error?: string;
};

export type PushDiagnostics = {
  notificationApi: boolean;
  serviceWorkerApi: boolean;
  firebaseConfigured: boolean;
  messagingSupported: boolean | null;
  permission: NotificationPermission | "unsupported";
  vapidKeyConfigured: boolean;
  workerScriptReachable: boolean | null;
  workerScope?: string;
  workerState: { active: boolean; waiting: boolean; installing: boolean };
  pushSubscription: boolean | null;
  foregroundHandlerRegistered: boolean;
  currentToken: { issued: boolean; stored: boolean | null; masked?: string; error?: string } | null;
};

const SERVICE_WORKER_URL = "/firebase-messaging-sw.js";
const DEVICE_ID_STORAGE_PREFIX = "goralert.push-device-id.";
let foregroundHandlerRegistered = false;

function deviceIdStorageKey(uid: string): string {
  return `${DEVICE_ID_STORAGE_PREFIX}${uid}`;
}

export function getStoredPushDeviceId(uid: string): string | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    return window.localStorage.getItem(deviceIdStorageKey(uid))?.trim() || undefined;
  } catch {
    return undefined;
  }
}

function getOrCreatePushDeviceId(uid: string): string {
  const existing = getStoredPushDeviceId(uid);
  if (existing) return existing;
  const created = generateId();
  try {
    window.localStorage.setItem(deviceIdStorageKey(uid), created);
  } catch {
    throw new Error("현재 기기 식별 정보를 저장할 수 없습니다. 브라우저 저장소 설정을 확인해 주세요.");
  }
  return created;
}

export function clearStoredPushDeviceId(uid: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.localStorage.removeItem(deviceIdStorageKey(uid));
    return true;
  } catch {
    return false;
  }
}

type NavigatorWithUserAgentData = Navigator & {
  userAgentData?: { mobile?: boolean; platform?: string };
};

const PLATFORM_LABELS: Record<PushDevicePlatform, string> = {
  android: "Android",
  windows: "Windows",
  macos: "macOS",
  ios: "iOS",
  linux: "Linux",
  unknown: "알 수 없는 플랫폼",
};

const BROWSER_LABELS: Record<PushDeviceBrowser, string> = {
  chrome: "Chrome",
  "samsung-internet": "삼성 인터넷",
  edge: "Edge",
  safari: "Safari",
  firefox: "Firefox",
  unknown: "알 수 없는 브라우저",
};

function detectPlatform(userAgent: string, platformHint: string, touchPoints: number): PushDevicePlatform {
  const combined = `${userAgent} ${platformHint}`;
  if (/android/i.test(combined)) return "android";
  if (/windows/i.test(combined)) return "windows";
  if (/iphone|ipad|ipod/i.test(userAgent) || (/mac/i.test(combined) && touchPoints > 1)) return "ios";
  if (/mac/i.test(combined)) return "macos";
  if (/linux/i.test(combined)) return "linux";
  return "unknown";
}

function detectBrowser(userAgent: string): PushDeviceBrowser {
  if (/samsungbrowser/i.test(userAgent)) return "samsung-internet";
  if (/edg(e|a|ios)?\//i.test(userAgent)) return "edge";
  if (/firefox|fxios/i.test(userAgent)) return "firefox";
  if (/chrome|crios/i.test(userAgent)) return "chrome";
  if (/safari/i.test(userAgent)) return "safari";
  return "unknown";
}

function detectDeviceType(userAgent: string, mobileHint: boolean | undefined, platform: PushDevicePlatform): PushDeviceType {
  if (/ipad|tablet|playbook|silk/i.test(userAgent)) return "tablet";
  if (platform === "android" && !/mobile/i.test(userAgent)) return "tablet";
  if (mobileHint === true || /mobile|iphone|ipod/i.test(userAgent)) return "mobile";
  if (["windows", "macos", "linux"].includes(platform)) return "desktop";
  return "unknown";
}

export function detectPushDeviceMetadata(): Pick<PushDevice, "label" | "platform" | "browser" | "deviceType"> {
  if (typeof navigator === "undefined") {
    return { label: "알 수 없는 기기", platform: "unknown", browser: "unknown", deviceType: "unknown" };
  }
  const enhanced = navigator as NavigatorWithUserAgentData;
  const userAgent = navigator.userAgent || "";
  const platformHint = enhanced.userAgentData?.platform || navigator.platform || "";
  const platform = detectPlatform(userAgent, platformHint, navigator.maxTouchPoints || 0);
  const browser = detectBrowser(userAgent);
  const deviceType = detectDeviceType(userAgent, enhanced.userAgentData?.mobile, platform);
  const label = platform === "unknown" && browser === "unknown"
    ? "알 수 없는 기기"
    : `${PLATFORM_LABELS[platform]} · ${BROWSER_LABELS[browser]}`;
  return { label, platform, browser, deviceType };
}

function buildPushDevice(uid: string, token: string): PushDevice {
  const now = new Date().toISOString();
  return {
    id: getOrCreatePushDeviceId(uid),
    token,
    ...detectPushDeviceMetadata(),
    registeredAt: now,
    lastSeenAt: now,
    tokenUpdatedAt: now,
  };
}

// VAPID public key (Web Push). Generated in Firebase Console →
// 프로젝트 설정 → Cloud Messaging → 웹 푸시 인증서. Exposed as a public env var.
function getVapidKey(): string | undefined {
  const key = process.env.NEXT_PUBLIC_FIREBASE_VAPID_KEY;
  return key && key.trim() ? key.trim() : undefined;
}

// Resolves the Messaging instance only when running in a supported browser.
// Returns null on SSR / unsupported browsers (e.g. iOS Safari) so callers can
// degrade gracefully instead of throwing.
async function resolveMessaging(): Promise<Messaging | null> {
  if (typeof window === "undefined") return null;
  if (!firebaseApp) return null;
  if (!("serviceWorker" in navigator)) return null;
  if (typeof Notification === "undefined") return null;
  try {
    const supported = await isSupported();
    if (!supported) return null;
  } catch {
    return null;
  }
  // Import lazily-safe: getMessaging can throw if the environment lost support
  // between isSupported() and here.
  try {
    const { getMessaging } = await import("firebase/messaging");
    return getMessaging(firebaseApp);
  } catch {
    return null;
  }
}

// Registers the background message service worker and waits until it is ACTIVE.
// navigator.serviceWorker.register() is idempotent: called again with the same
// script URL + scope it returns the existing registration instead of creating a
// duplicate, so we can safely call it every time. getToken()/showNotification()
// both require an *active* worker, hence the `ready` await. Logs every step
// (start / success / active / failure) and rethrows the real error so callers
// can surface the actual cause instead of a generic message.
async function registerServiceWorker(): Promise<ServiceWorkerRegistration> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    throw new Error("이 브라우저는 서비스 워커를 지원하지 않습니다.");
  }
  console.log("[push] SW register start", SERVICE_WORKER_URL);
  try {
    const registration = await navigator.serviceWorker.register(SERVICE_WORKER_URL);
    console.log("[push] SW register success", registration);
    // Wait for the worker to reach the active state (register() resolves before
    // activation on a first install). getToken()/showNotification() need it active.
    await navigator.serviceWorker.ready;
    console.log("[push] SW ready — active script:", registration.active?.scriptURL ?? "(pending)");
    return registration;
  } catch (err) {
    console.error("[push] SW register failed", err);
    throw err;
  }
}

// Registers the FCM service worker WITHOUT requesting notification permission or
// fetching a token. Safe to call on app load (needs NO user gesture) so the SW
// exists in the browser as early as possible — that way
// navigator.serviceWorker.getRegistration() resolves to a real registration
// before the user ever opens Settings. Plain SW registration only needs
// navigator.serviceWorker, so it does NOT depend on firebase/messaging
// isSupported(). Best-effort: logs and swallows errors (returns null).
export async function ensureServiceWorkerRegistered(): Promise<ServiceWorkerRegistration | null> {
  if (typeof window === "undefined") return null;
  if (!("serviceWorker" in navigator)) {
    console.log("[push] serviceWorker unsupported — skipping SW registration");
    return null;
  }
  try {
    return await registerServiceWorker();
  } catch (err) {
    console.error("[push] ensureServiceWorkerRegistered failed", err);
    return null;
  }
}

// Requests permission + registers an FCM token for this device, persisting it
// to the user's alert settings. Safe to call from the browser only.
export async function registerPushToken(uid: string): Promise<RegisterPushResult> {
  if (typeof window === "undefined") {
    return { ok: false, error: "브라우저에서만 알림을 등록할 수 있습니다." };
  }

  const vapidKey = getVapidKey();
  if (!vapidKey) {
    return {
      ok: false,
      error: "푸시 인증 키(NEXT_PUBLIC_FIREBASE_VAPID_KEY)가 설정되지 않았습니다. 관리자에게 문의하세요.",
    };
  }

  const messaging = await resolveMessaging();
  if (!messaging) {
    return {
      ok: false,
      error: "이 브라우저에서는 푸시 알림을 사용할 수 없습니다. (iOS Safari 등 일부 환경 미지원)",
    };
  }

  // 1) SERVICE WORKER FIRST. Register + wait until active BEFORE requesting
  //    permission or fetching a token. getToken() internally performs
  //    PushManager.subscribe against this worker, so the worker must exist and
  //    be active first. (This also guarantees getRegistration() resolves.)
  let registration: ServiceWorkerRegistration;
  try {
    registration = await registerServiceWorker();
  } catch (err) {
    console.error("[push] registerPushToken: SW registration failed", err);
    return {
      ok: false,
      error: err instanceof Error ? err.message : "서비스 워커 등록에 실패했습니다.",
    };
  }

  // 2) NOTIFICATION PERMISSION — only after the SW is registered and active.
  let permission: NotificationPermission = Notification.permission;
  console.log("[push] Notification.permission (before request):", permission);
  if (permission === "default") {
    try {
      permission = await Notification.requestPermission();
      console.log("[push] Notification.permission (after request):", permission);
    } catch (err) {
      console.error("[push] Notification.requestPermission failed", err);
      return { ok: false, error: "알림 권한 요청에 실패했습니다." };
    }
  }
  if (permission === "denied") {
    return { ok: false, error: "알림 권한이 차단되어 있습니다. 브라우저 설정에서 알림을 허용해 주세요." };
  }
  if (permission !== "granted") {
    return { ok: false, error: "알림 권한이 허용되지 않았습니다." };
  }

  // 3) getToken() with the already-registered worker. Any failure here — a
  //    rejected push subscription or an FCM token error — must surface the REAL
  //    message so the UI never shows "성공" on a failed subscribe.
  try {
    console.log("[push] getToken start");
    const token = await getToken(messaging, {
      vapidKey,
      serviceWorkerRegistration: registration,
    });
    // Only a real, non-empty FCM token counts as success. Persist (and therefore
    // increment the registered-device count) exclusively on token success.
    if (!token) {
      console.warn("[push] getToken returned empty token");
      return { ok: false, error: "푸시 토큰을 발급받지 못했습니다. 잠시 후 다시 시도해 주세요." };
    }
    console.log("[push] getToken success");
    let device: PushDevice;
    try {
      device = buildPushDevice(uid, token);
    } catch (err) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : "현재 기기 정보 확인에 실패했습니다.",
      };
    }
    try {
      const snapshot = await upsertPushDevice(uid, device);
      return { ok: true, deviceId: device.id, snapshot };
    } catch {
      return {
        ok: false,
        error: "알림 기기 정보를 서버에 저장하지 못했습니다. 네트워크 상태를 확인해 주세요.",
      };
    }
  } catch (err) {
    console.error("[push] getToken failed", err);
    return {
      ok: false,
      error: err instanceof Error ? err.message : "푸시 등록 중 오류가 발생했습니다.",
    };
  }
}

// Unregisters THIS device: resolves the device's current FCM token, invalidates
// the FCM registration (deleteToken), and removes the token from the user's
// pushTokens so the registered-device count decreases and the Python engine
// stops delivering to it. Best-effort by design — a browser whose permission was
// revoked may not surface a token, but we still attempt deleteToken and report
// the real outcome so the UI never claims a fake success.
export async function unregisterPushToken(uid: string): Promise<RegisterPushResult> {
  if (typeof window === "undefined") {
    return { ok: false, error: "브라우저에서만 알림 등록을 해제할 수 있습니다." };
  }

  const messaging = await resolveMessaging();
  if (!messaging) {
    return {
      ok: false,
      error: "이 브라우저에서는 푸시 알림을 사용할 수 없습니다.",
    };
  }

  try {
    // Resolve only this browser's exact token/id. If neither is available we
    // fail safely; there is deliberately no "remove the last array item"
    // fallback because that could unregister another device.
    const current = await inspectCurrentPushRegistration(uid, false);
    if (!current.deviceId && !current.token) {
      return { ok: false, error: "현재 기기 정보를 확인할 수 없습니다. 기기 목록에서 다시 선택해 주세요." };
    }
    const snapshot = await removePushRegistrations(uid, [{
      deviceId: current.deviceId,
      token: current.token,
    }]);
    const cleanup = await deleteCurrentPushTokenLocally(uid);
    return {
      ok: true,
      deviceId: current.deviceId,
      snapshot,
      localCleanupFailed: !cleanup.ok,
      error: cleanup.error,
    };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : "알림 등록 해제 중 오류가 발생했습니다.",
    };
  }
}

// Checks the current browser without requesting permission. If this browser's
// stable id already has a device entry, a token refresh and the throttled
// `lastSeenAt` confirmation are persisted transactionally. An absent server
// entry is never recreated here (important after an explicit full reset).
export async function inspectCurrentPushRegistration(
  uid: string,
  confirmExisting = true,
): Promise<CurrentPushRegistration> {
  const deviceId = getStoredPushDeviceId(uid);
  if (typeof window === "undefined" || typeof Notification === "undefined" || Notification.permission !== "granted") {
    return { deviceId };
  }
  const vapidKey = getVapidKey();
  const messaging = await resolveMessaging();
  if (!vapidKey || !messaging) return { deviceId };
  try {
    const registration = await registerServiceWorker();
    const token = await getToken(messaging, { vapidKey, serviceWorkerRegistration: registration });
    if (!token) return { deviceId, error: "현재 기기 정보 확인에 실패했습니다." };
    if (!confirmExisting || !deviceId) return { deviceId, token };
    const device = buildPushDevice(uid, token);
    const snapshot = await upsertPushDevice(uid, device, { requireExistingDeviceId: true });
    return { deviceId, token, snapshot };
  } catch {
    return { deviceId, error: "현재 기기 정보 확인에 실패했습니다." };
  }
}

// Local cleanup applies only to the browser executing this function. The
// server-side removal must be completed separately before calling it for a
// selected device or a full reset. The local id is cleared even when FCM local
// token deletion fails, and the partial failure is reported to the caller.
export async function deleteCurrentPushTokenLocally(uid: string): Promise<{ ok: boolean; error?: string }> {
  const idCleared = clearStoredPushDeviceId(uid);
  const messaging = await resolveMessaging();
  if (!messaging) {
    return { ok: false, error: "현재 기기의 로컬 알림 정보를 삭제할 수 없는 브라우저입니다." };
  }
  try {
    await deleteToken(messaging);
    return idCleared
      ? { ok: true }
      : { ok: false, error: "현재 기기의 로컬 식별 정보를 삭제하지 못했습니다." };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : "현재 기기의 로컬 알림 정보 삭제에 실패했습니다.",
    };
  }
}

// NOTE: there is intentionally NO in-browser "test push" here. A real test send
// must exercise the production delivery path (Python engine -> PushChannel ->
// FCM), so the settings/alerts screens ENQUEUE a request via
// repositories.enqueueTestPushRequest and the engine delivers it. A local
// showNotification() would be a *different* code path from production and is
// therefore not used to test push (single-source-of-truth requirement).

// Subscribes to foreground messages (received while the tab is focused).
// Returns an unsubscribe function, or a no-op when messaging is unavailable.
export async function subscribeForegroundMessages(
  handler: (payload: MessagePayload) => void,
): Promise<() => void> {
  const messaging = await resolveMessaging();
  if (!messaging) return () => {};
  try {
    foregroundHandlerRegistered = true;
    const unsubscribe = onMessage(messaging, handler);
    return () => {
      foregroundHandlerRegistered = false;
      unsubscribe();
    };
  } catch {
    return () => {};
  }
}

// Read-only, explicit diagnostics for development/debug mode. This never logs a
// token or PushSubscription endpoint and does not request notification permission.
export async function getPushDiagnostics(uid?: string): Promise<PushDiagnostics> {
  const notificationApi = typeof Notification !== "undefined";
  const serviceWorkerApi = typeof navigator !== "undefined" && "serviceWorker" in navigator;
  const initial: PushDiagnostics = {
    notificationApi,
    serviceWorkerApi,
    firebaseConfigured: isFirebaseConfigured,
    messagingSupported: null,
    permission: notificationApi ? Notification.permission : "unsupported",
    vapidKeyConfigured: Boolean(getVapidKey()),
    workerScriptReachable: null,
    workerState: { active: false, waiting: false, installing: false },
    pushSubscription: null,
    foregroundHandlerRegistered,
    currentToken: null,
  };
  if (!serviceWorkerApi) return initial;

  try {
    initial.messagingSupported = await isSupported();
  } catch {
    initial.messagingSupported = false;
  }
  try {
    const response = await fetch(SERVICE_WORKER_URL, { cache: "no-store" });
    initial.workerScriptReachable = response.ok;
  } catch {
    initial.workerScriptReachable = false;
  }
  try {
    const registration = await navigator.serviceWorker.getRegistration();
    if (!registration) return initial;
    initial.workerScope = registration.scope;
    initial.workerState = {
      active: Boolean(registration.active),
      waiting: Boolean(registration.waiting),
      installing: Boolean(registration.installing),
    };
    initial.pushSubscription = Boolean(await registration.pushManager.getSubscription());
    // A token check is intentionally opt-in (only callers that pass uid, such
    // as the development diagnostics panel). It never prints the full token.
    if (uid && initial.permission === "granted" && getVapidKey()) {
      const messaging = await resolveMessaging();
      if (messaging) {
        try {
          const token = await getToken(messaging, { vapidKey: getVapidKey(), serviceWorkerRegistration: registration });
          if (!token) {
            initial.currentToken = { issued: false, stored: null, error: "empty-token" };
          } else {
            const settings = await loadAlertSettings(uid);
            initial.currentToken = {
              issued: true,
              stored: (settings.pushTokens ?? []).includes(token),
              masked: `${token.slice(0, 12)}…`,
            };
          }
        } catch (err) {
          initial.currentToken = { issued: false, stored: null, error: err instanceof Error ? err.name : "token-error" };
        }
      }
    }
  } catch {
    // Keep the independently collected diagnostics; registration failure is
    // represented by the absent scope/state instead of a fabricated success.
  }
  return initial;
}

// Convenience check the UI can use to decide whether to show the register
// button at all. Mirrors resolveMessaging without instantiating Messaging.
export async function isPushSupported(): Promise<boolean> {
  if (typeof window === "undefined") return false;
  if (!("serviceWorker" in navigator)) return false;
  if (typeof Notification === "undefined") return false;
  try {
    return await isSupported();
  } catch {
    return false;
  }
}
