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

import { deleteToken, getToken, isSupported, onMessage, type MessagePayload, type Messaging } from "firebase/messaging";
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
// registration when one is already present for the SW URL. Throws with the real
// error so callers surface the actual cause instead of a generic message.
async function registerServiceWorker(): Promise<ServiceWorkerRegistration> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    throw new Error("이 브라우저는 서비스 워커를 지원하지 않습니다.");
  }
  const existing = await navigator.serviceWorker.getRegistration(SERVICE_WORKER_URL);
  if (existing) return existing;
  return await navigator.serviceWorker.register(SERVICE_WORKER_URL);
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

// Removes a token from alertSettings.pushTokens. No-op when it isn't present so
// the registered-device count only changes when a real token is dropped.
async function removeToken(uid: string, token: string): Promise<void> {
  const settings = await loadAlertSettings(uid);
  const current = settings.pushTokens ?? [];
  if (!current.includes(token)) return;
  const next = current.filter((existing) => existing !== token);
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

  // Service worker registration + getToken() (which internally performs
  // PushManager.subscribe). Any failure here — SW registration error, a rejected
  // push subscription, or an FCM token error — must surface the REAL message so
  // the UI never shows "성공" on a failed subscribe.
  try {
    const registration = await registerServiceWorker();
    const token = await getToken(messaging, {
      vapidKey,
      serviceWorkerRegistration: registration,
    });
    // Only a real, non-empty FCM token counts as success. Persist (and therefore
    // increment the registered-device count) exclusively on token success.
    if (!token) {
      return { ok: false, error: "푸시 토큰을 발급받지 못했습니다. 잠시 후 다시 시도해 주세요." };
    }
    await persistToken(uid, token);
    return { ok: true, token };
  } catch (err) {
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
    // Resolve this device's token first so we remove exactly it from settings.
    let token: string | undefined;
    const vapidKey = getVapidKey();
    if (vapidKey && Notification.permission === "granted") {
      try {
        const registration = await registerServiceWorker();
        token = await getToken(messaging, { vapidKey, serviceWorkerRegistration: registration });
      } catch {
        token = undefined;
      }
    }

    // Invalidate the FCM registration for this device.
    await deleteToken(messaging);

    // Drop this device's token from the persisted list (count decreases).
    if (token) {
      await removeToken(uid, token);
    }
    return { ok: true, token };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : "알림 등록 해제 중 오류가 발생했습니다.",
    };
  }
}

export type TestPushResult = {
  ok: boolean;
  error?: string;
};

// Sends a REAL test push to this device by displaying a notification through the
// registered service worker. This genuinely exercises the browser push pipeline
// (permission → active service worker → showNotification), so the caller can
// record the history log as `sent` ONLY when this actually succeeds. A pure
// server FCM round-trip is the Python engine's job; this in-app test verifies the
// device-side delivery path that users interact with from the settings screen.
export async function sendTestPush(message: { title: string; body: string }): Promise<TestPushResult> {
  if (typeof window === "undefined") {
    return { ok: false, error: "브라우저에서만 테스트 푸시를 보낼 수 있습니다." };
  }
  if (typeof Notification === "undefined" || !("serviceWorker" in navigator)) {
    return { ok: false, error: "이 브라우저에서는 푸시 알림을 사용할 수 없습니다." };
  }
  if (Notification.permission !== "granted") {
    return {
      ok: false,
      error: "알림 권한이 없습니다. 먼저 ‘이 기기 알림 등록’으로 권한을 허용해 주세요.",
    };
  }
  try {
    const registration = await registerServiceWorker();
    // Wait until the worker is active — showNotification throws otherwise.
    await navigator.serviceWorker.ready;
    await registration.showNotification(message.title, {
      body: message.body,
      icon: "/gorani-logo.png",
      badge: "/gorani-logo.png",
      data: { url: "/" },
    });
    return { ok: true };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : "테스트 푸시 발송에 실패했습니다.",
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
