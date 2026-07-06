/* eslint-disable */
// GORALERT-ALERT-SYSTEM — Firebase Cloud Messaging background service worker.
//
// 이 파일은 정적 파일로 제공되므로 런타임에 process.env 를 읽을 수 없습니다.
// 따라서 아래 firebaseConfig 값을 직접 채워야 합니다.
//
// ============================================================================
// 배포 시 아래 값을 NEXT_PUBLIC_FIREBASE_* 와 동일하게 채우세요.
//   apiKey            ← NEXT_PUBLIC_FIREBASE_API_KEY
//   authDomain        ← NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN
//   projectId         ← NEXT_PUBLIC_FIREBASE_PROJECT_ID
//   storageBucket     ← NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET
//   messagingSenderId ← NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID
//   appId             ← NEXT_PUBLIC_FIREBASE_APP_ID
// 이 값들은 모두 공개(public) 키이므로 클라이언트에 노출되어도 안전합니다.
// ============================================================================

// Firebase compat SDK (pinned to 11.x to match the app's firebase@11 dependency).
importScripts("https://www.gstatic.com/firebasejs/11.0.2/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/11.0.2/firebase-messaging-compat.js");

var firebaseConfig = {
  apiKey: "", // NEXT_PUBLIC_FIREBASE_API_KEY
  authDomain: "", // NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN
  projectId: "", // NEXT_PUBLIC_FIREBASE_PROJECT_ID
  storageBucket: "", // NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET
  messagingSenderId: "", // NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID
  appId: "", // NEXT_PUBLIC_FIREBASE_APP_ID
};

// config 가 채워진 경우에만 초기화 (빈 값이면 조용히 비활성화).
if (firebaseConfig.projectId && firebaseConfig.messagingSenderId) {
  firebase.initializeApp(firebaseConfig);

  var messaging = firebase.messaging();

  // 백그라운드(탭 비활성/종료) 수신 메시지 → 시스템 알림 표시.
  messaging.onBackgroundMessage(function (payload) {
    var notification = (payload && payload.notification) || {};
    var data = (payload && payload.data) || {};
    var title = notification.title || data.title || "고라알림";
    var options = {
      body: notification.body || data.body || "새로운 알림이 도착했습니다.",
      icon: "/gorani-logo.png",
      badge: "/gorani-logo.png",
      data: data,
    };
    self.registration.showNotification(title, options);
  });
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
