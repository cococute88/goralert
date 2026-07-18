// GORALERT-ALERT-SYSTEM — build-time generator for the FCM background service worker.
//
// public/firebase-messaging-sw.js is served as a *static* file, so it cannot read
// process.env at runtime. This script is the single source of truth for the
// service worker: it injects the NEXT_PUBLIC_FIREBASE_* values (all public keys)
// into the worker body and writes public/firebase-messaging-sw.js at build/dev
// time. Wired into the `dev` and `build` npm scripts so the config is always
// filled automatically — no manual hardcoding required.
//
// Env resolution order: real process.env (Vercel build) → .env.local → .env.

import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

// Minimal .env loader (no dependency). Only fills keys not already in process.env.
function loadEnvFile(file) {
  const path = resolve(ROOT, file);
  if (!existsSync(path)) return;
  const content = readFileSync(path, "utf8");
  for (const rawLine of content.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    if (key in process.env) continue;
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    process.env[key] = value;
  }
}

// .env.local wins over .env (matches Next.js precedence).
loadEnvFile(".env.local");
loadEnvFile(".env");

const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY ?? "",
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN ?? "",
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID ?? "",
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET ?? "",
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID ?? "",
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID ?? "",
};

const requiredConfigKeys = ["apiKey", "projectId", "messagingSenderId", "appId"];
const missingConfigKeys = requiredConfigKeys.filter((key) => !firebaseConfig[key]);
if (missingConfigKeys.length) {
  const message = `Firebase Messaging service worker is not generated with usable config; missing ${missingConfigKeys.join(", ")}.`;
  if (process.env.VERCEL_ENV === "production") {
    throw new Error(message);
  }
  console.warn(`[generate-firebase-sw] WARNING: ${message}`);
}

const configLiteral = JSON.stringify(firebaseConfig, null, 2);

const serviceWorker = `/* eslint-disable */
// GORALERT-ALERT-SYSTEM — Firebase Cloud Messaging background service worker.
//
// ⚠️ GENERATED FILE — do not edit by hand.
// Produced by scripts/generate-firebase-sw.mjs at dev/build time. The
// firebaseConfig below is injected from NEXT_PUBLIC_FIREBASE_* env vars (all
// public keys, safe to expose to the client). Edit the generator, not this file.

// Firebase compat SDK (pinned to 11.x to match the app's firebase@11 dependency).
importScripts("https://www.gstatic.com/firebasejs/11.0.2/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/11.0.2/firebase-messaging-compat.js");

var firebaseConfig = ${configLiteral};

// Do not initialize a worker with partial config: it can look registered while
// FCM token issuance/background delivery is impossible.
if (firebaseConfig.apiKey && firebaseConfig.projectId && firebaseConfig.messagingSenderId && firebaseConfig.appId) {
  firebase.initializeApp(firebaseConfig);

  var messaging = firebase.messaging();

  // 백그라운드(탭 비활성/종료) 수신 메시지 → 시스템 알림 표시.
  messaging.onBackgroundMessage(function (payload) {
    // PushChannel sends data-only messages. Legacy notification payloads are
    // displayed by the browser/FCM automatically, so rendering them again here
    // would create duplicate system notifications.
    if (payload && payload.notification) return;
    var notification = (payload && payload.notification) || {};
    var data = (payload && payload.data) || {};
    var title = notification.title || data.title || "고라알림";
    var options = {
      body: notification.body || data.body || "새로운 알림이 도착했습니다.",
      icon: "/gorani-bell-192.png",
      badge: "/gorani-bell-32.png",
      data: data,
    };
    self.registration.showNotification(title, options);
  });
} else {
  console.error("[push] Firebase Messaging worker disabled: required public Firebase config is missing.");
}

// 알림 클릭 시 앱(고라알림)으로 포커스/이동.
self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var targetUrl = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clientList) {
      for (var i = 0; i < clientList.length; i++) {
        var client = clientList[i];
        if ("focus" in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
      return undefined;
    }),
  );
});
`;

const outPath = resolve(ROOT, "public", "firebase-messaging-sw.js");
writeFileSync(outPath, serviceWorker, "utf8");

const filled = missingConfigKeys.length === 0;
console.log(
  `[generate-firebase-sw] wrote ${outPath} (config ${filled ? "filled from env" : "EMPTY — NEXT_PUBLIC_FIREBASE_* not set"})`,
);
