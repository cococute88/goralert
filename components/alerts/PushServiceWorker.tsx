"use client";

// GORALERT-ALERT-SYSTEM — registers the FCM background service worker on app
// load. Mounted once in the root layout. This is what makes
// navigator.serviceWorker.getRegistration() resolve to a real registration
// BEFORE the user opens Settings and taps "이 기기 알림 등록" — previously the SW
// was only ever registered inside the button handler, so a fresh browser showed
// getRegistration() === undefined and Notification.permission === "default".
//
// Registering the SW needs NO user gesture and NO notification permission (only
// requestPermission()/getToken() do, which still run from the button), so this
// is safe to run automatically on every page load. register() is idempotent.

import { useEffect } from "react";
import { isFirebaseConfigured } from "@/lib/firebase/client";
import { ensureServiceWorkerRegistered } from "@/lib/alerts/fcm-client";

export default function PushServiceWorker() {
  useEffect(() => {
    if (!isFirebaseConfigured) {
      console.log("[push] firebase not configured — skipping SW auto-registration");
      return;
    }
    void ensureServiceWorkerRegistered();
  }, []);
  return null;
}
