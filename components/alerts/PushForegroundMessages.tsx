"use client";

// Foreground notifications are only shown after a REAL FCM message reaches
// firebase/messaging's onMessage handler. This is not a local test substitute.
import { useEffect } from "react";
import { subscribeForegroundMessages } from "@/lib/alerts/fcm-client";
import { useToast } from "./ui/toast";

export default function PushForegroundMessages() {
  const toast = useToast();

  useEffect(() => {
    let active = true;
    let unsubscribe = () => {};
    void subscribeForegroundMessages((payload) => {
      if (!active) return;
      const title = payload.data?.title ?? payload.notification?.title ?? "고라알림";
      const body = payload.data?.body ?? payload.notification?.body ?? "새로운 알림이 도착했습니다.";
      toast.show(`${title}: ${body}`, "info");
      // Browsers do not automatically display a system notification for an
      // onMessage foreground delivery. This runs only after FCM reception.
      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        new Notification(title, { body, icon: "/gorani-bell-192.png" });
      }
    }).then((next) => {
      if (active) unsubscribe = next;
      else next();
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [toast]);

  return null;
}
