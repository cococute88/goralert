# Android FCM Push 운영 및 수동 검증

## 배포 환경 변수와 Secret

Vercel에 다음 값을 설정한 뒤 Production으로 재배포합니다.

- `NEXT_PUBLIC_FIREBASE_API_KEY`, `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`, `NEXT_PUBLIC_FIREBASE_PROJECT_ID`, `NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET`, `NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID`, `NEXT_PUBLIC_FIREBASE_APP_ID`
- `NEXT_PUBLIC_FIREBASE_VAPID_KEY` — Firebase Console의 Cloud Messaging Web Push 인증서 공개 키
- `GITHUB_DISPATCH_TOKEN` — `actions:write` 권한의 서버 전용 토큰
- `GITHUB_REPO=cococute88/goralert`, `GITHUB_WORKFLOW_FILE=alert-test-push.yml`, `GITHUB_WORKFLOW_REF=main`

GitHub Actions Secrets에는 `FIREBASE_SERVICE_ACCOUNT`와 `TELEGRAM_BOT_TOKEN`을 설정합니다. service account JSON, GitHub PAT, Telegram token, FCM token은 브라우저·Git·PR에 절대 넣지 않습니다. GitHub Actions **Variables**에는 (공개 식별자인) `FIREBASE_PROJECT_ID`를 설정해 service account의 프로젝트와 웹 Firebase 프로젝트가 일치하는지 검사할 수 있습니다.

## Firebase Console 확인

1. Web app config의 `projectId`, `messagingSenderId`, `appId`, `apiKey`가 Vercel 값과 같은지 확인합니다.
2. Cloud Messaging의 Web Push 인증서/VAPID 공개 키가 `NEXT_PUBLIC_FIREBASE_VAPID_KEY`와 같은지 확인합니다.
3. service account JSON의 `project_id`가 Web app의 `projectId`와 같은지 확인합니다.
4. Firebase Cloud Messaging API가 해당 프로젝트에서 사용 가능한지, 인증·승인된 앱 도메인이 Firebase Auth와 웹 앱 설정에 등록되어 있는지 확인합니다.
5. 배포 URL에서 `/firebase-messaging-sw.js`가 HTTP 200인지 확인합니다. production build는 필수 public Firebase config가 비어 있으면 실패합니다.

Preview 환경은 production 배포를 막지 않기 위해 필수 public Firebase 값이 없으면 build 경고와 비활성 Service Worker를 생성합니다. Preview에서 Push 수신 성공을 주장할 수 없으며, 실제 Push 검증은 위 값이 모두 설정된 Production URL에서만 수행합니다.

## Chrome (Android)

1. HTTPS production URL에서 로그인하고 설정의 **이 기기 알림 등록**을 누릅니다.
2. Android 시스템과 Chrome 사이트 알림 권한을 모두 허용합니다. 설치 여부는 성공 조건이 아닙니다.
3. 개발 환경에서만 표시되는 Push 진단에서 SW active, Push 구독, FCM token 발급 및 Firestore 저장을 확인합니다. 화면에는 토큰 일부만 표시됩니다.
4. **테스트 Push**를 누릅니다. 요청 접수 → Actions → 엔진 결과와 채널별 Push 결과를 각각 확인합니다.
5. 탭을 연 상태(foreground)와 앱을 종료/백그라운드로 둔 상태(background)를 각각 검증하고, 알림 클릭이 앱을 포커스하는지 확인합니다.

## 삼성 인터넷 (Android)

위 절차를 Chrome과 별개로 반복합니다. 브라우저 저장소가 달라 FCM token도 별도이며, 한 브라우저의 성공은 다른 브라우저의 성공을 의미하지 않습니다. `isSupported()`가 false이거나 등록 단계가 실패하면 성공으로 표시하지 말고 해당 진단 단계와 삼성 인터넷/Android 알림 차단 상태를 기록합니다.

## 결과 해석

- Telegram 성공 + Push 실패는 PushChannel의 채널별 FCM 결과를 우선 봅니다. Telegram 성공은 Push 성공 증거가 아닙니다.
- `no pushTokens`는 현재 브라우저에서 FCM token 등록 또는 Firestore 저장 전 단계 실패입니다.
- `UNREGISTERED`/`INVALID_ARGUMENT`/`SENDER_ID_MISMATCH`는 FCM이 확인한 무효 token만 정리합니다.
- `THIRD_PARTY_AUTH_ERROR`, 인증 오류, 프로젝트 불일치는 VAPID·service account·Firebase 프로젝트 구성을 확인합니다.
- Dispatch 실패는 규칙 저장 실패가 아닙니다. 현재 workflow의 5분 schedule은 큐에 남은 요청의 fallback입니다.
