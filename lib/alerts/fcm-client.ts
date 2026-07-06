// GORALERT-ALERT-SYSTEM (REQ-039 / REQ-045)
// Real browser FCM (Firebase Cloud Messaging) client token registration.
//
// Flow (registerPushToken):
//   1. Guard SSR / unsupported environments (iOS Safari, no SW, no Notification).
//   2. Request Notification permission.
//   3. Register the service worker (public/firebase-messaging-sw.js).
//   4. Call getToken() with the VAPID key (NEXT_PUBLIC_FIREBASE_VAPID_KEY).
//   5. Persist the token via saveAlertSettings (append to pushTokens, deduped).
//
// All failure paths return { ok: false, error } with a clear Korean message so
// the settings UI can show a friendly toast. The Python engine (alert_engine/)
// reads users/{uid}/alertSettings.pushTokens to deliver push via the shared
// service account — this is the web side that registers those tokens.

import { getToken, isSupported, onMessage, type MessagePayload, type Messaging } from "firebase/messaging";
import { firebaseApp } from "@/lib/firebase/client";
import { loadAlertSettings, saveAlertSettings } from "./repositories";

export type RegisterPushResult = {
  ok: boolean;
  token?: string;
  error?: string;
};

const SERVICE_WORKER_URL = "/firebase-messaging-sw.js";

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

// Registers the background message service worker. Reuses an existing
// registration when one is already present for the SW URL.
async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return null;
  try {
    const existing = await navigator.serviceWorker.getRegistration(SERVICE_WORKER_URL);
    if (existing) return existing;
    return await navigator.serviceWorker.register(SERVICE_WORKER_URL);
  } catch {
    return null;
  }
}

// Appends a token to alertSettings.pushTokens, de-duplicating. No-op write is
// avoided when the token is already registered.
async function persistToken(uid: string, token: string): Promise<void> {
  const settings = await loadAlertSettings(uid);
  const current = settings.pushTokens ?? [];
  if (current.includes(token)) return;
  const next = Array.from(new Set([...current, token]));
  await saveAlertSettings(uid, { pushTokens: next });
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

  // Notification permission.
  let permission: NotificationPermission = Notification.permission;
  if (permission === "default") {
    try {
      permission = await Notification.requestPermission();
    } catch {
      return { ok: false, error: "알림 권한 요청에 실패했습니다." };
    }
  }
  if (permission === "denied") {
    return { ok: false, error: "알림 권한이 차단되어 있습니다. 브라우저 설정에서 알림을 허용해 주세요." };
  }
  if (permission !== "granted") {
    return { ok: false, error: "알림 권한이 허용되지 않았습니다." };
  }

  const registration = await registerServiceWorker();
  if (!registration) {
    return { ok: false, error: "알림 서비스 워커 등록에 실패했습니다." };
  }

  try {
    const token = await getToken(messaging, {
      vapidKey,
      serviceWorkerRegistration: registration,
    });
    if (!token) {
      return { ok: false, error: "푸시 토큰을 발급받지 못했습니다. 잠시 후 다시 시도해 주세요." };
    }
    await persistToken(uid, token);
    return { ok: true, token };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : "푸시 토큰 등록 중 오류가 발생했습니다.",
    };
  }
}

// Subscribes to foreground messages (received while the tab is focused).
// Returns an unsubscribe function, or a no-op when messaging is unavailable.
export async function subscribeForegroundMessages(
  handler: (payload: MessagePayload) => void,
): Promise<() => void> {
  const messaging = await resolveMessaging();
  if (!messaging) return () => {};
  try {
    return onMessage(messaging, handler);
  } catch {
    return () => {};
  }
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
