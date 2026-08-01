# 반복 알림 회차 무결성

## 상태 전이와 불변조건

고정 시각 반복 알림은 `alertRules.nextScheduledAt`을 정식 스케줄 커서로 사용한다.
Firestore에는 timezone-aware `datetime`을 전달하므로 실제 저장값은 UTC Timestamp이고,
월/일/시각 계산은 각 규칙의 IANA timezone(기본 `Asia/Seoul`)에서 수행한다.

회차 ID는 `ruleId + ':' + scheduled wall-clock ISO`이다. 예를 들어
`2026-08-01 07:00 Asia/Seoul`은 UTC로 `2026-07-31T22:00:00Z`에 해당하지만,
회차 ID에는 원래 지역 시각과 `+09:00` 오프셋이 들어간다. 따라서 UTC 날짜가 전날이어도
월초 규칙의 의미가 바뀌지 않는다.

```text
due(nextScheduledAt <= workerNow)
  -> Firestore transaction
       notificationLogs/{occurrenceId} create(status=processing)
       alertRules.nextScheduledAt advance
  -> evaluate at original scheduledFor
  -> channel pending -> sending (durable) -> sent|failed
  -> sent | partial_failure | failed | skipped | delivery_unknown
```

`notificationLogs` 회차 문서 생성과 다음 커서 갱신은 같은 트랜잭션이다. 따라서 회차 기록 없이
커서만 미래로 갈 수 없다. 조건 불충족, quiet hours, cooldown, 평가 오류도 각각 사유가 있는
`skipped` 또는 `failed` 회차로 남는다.

## 지연 실행과 catch-up

due 판정에는 만료되는 시간 창을 사용하지 않는다. 커서가 현재 시각 이하인 동안 그 회차는 계속
due이다. 따라서 07:00 작업을 놓치고 09:19에 실행해도 07:00 회차를 처리한다. 처리 문서에는
`scheduledFor`, `processingStartedAt`, `completedAt`, `timezone`, `attemptCount`가 저장된다.

한 규칙에서 여러 회차가 연속 누락된 경우 한 엔진 실행은 가장 오래된 회차 하나를 처리한다.
다음 엔진 실행이 다음 커서를 다시 처리한다. 이는 대량 장애 복구 때 한 사용자가 전체 실행 시간을
독점하거나 근거 없이 여러 과거 알림을 한꺼번에 발송하는 일을 막는다.

특정 캘린더 이벤트를 대상으로 하는 일회성 규칙은 대상 날짜까지 매일 평가 커서를 전진시키며,
조건이 맞지 않은 날도 사유가 있는 `skipped` 회차로 남긴다. 대상 날짜에 발송 처리가 끝난 뒤에만
규칙을 비활성화한다.

기존 문서에 `nextScheduledAt`이 없으면 다음 순서로 최초 커서를 복원한다.

1. `lastProcessedScheduledAt` 직후
2. `lastTriggeredAt` 직후
3. `createdAt`부터 계산한 첫 회차
4. 위 필드도 없는 비정상 레거시 문서만 기존 평가 창 범위에서 보수적으로 추론

반복 주기·시각·timezone을 수정하면 브라우저 Firestore transaction이 기존
`nextScheduledAt` 회차를 `cancelled(schedule_changed)`로 영구 기록하고 커서를 제거한 뒤
`scheduleChangedAt`을 저장한다. 처리 중인 회차가 있으면 편집 저장을 거부한다. 엔진은 다음
실행에서 이 시각을 새 스케줄의 anchor로 사용하므로 이전 주기의 회차를 새 규칙으로 발송하지 않는다.

첫 처리 트랜잭션이 Timestamp 커서를 저장하므로 별도 일괄 마이그레이션은 필요 없다.

## 중복 방지와 장애 경계

두 worker가 같은 회차를 보더라도 Firestore `create` 트랜잭션을 성공한 하나만 lease를 얻는다.
채널 API 호출 직전에는 채널 상태를 `sending`으로 영속화한다.

- `pending` 상태에서 worker가 중단되면 lease 만료 후 재개해 발송한다.
- `sent`/`failed` 결과 저장 후 중단되면 해당 채널은 다시 호출하지 않는다.
- 외부 채널 호출 후 결과 저장 전에 중단되면 Telegram/FCM에는 범용 idempotency 보장이 없으므로
  자동 재발송하지 않고 `unknown`/`delivery_unknown`으로 남긴다. 운영자가 공급자 로그를 확인한다.

이 정책은 외부 API의 모호한 타임아웃에서 중복 발송을 막는 at-most-once 경계다. 결과를 알 수 없는
호출을 성공으로 조작하지 않는다.

## 발송·실패 기록

회차 문서에는 아래 정보가 보존된다.

- `eventId`, `ruleId`, `scheduledFor`, `timezone`
- `processingStartedAt`, `completedAt`, `attemptCount`
- 채널별 `status`, `attemptedAt`, `completedAt`, `attemptCount`, `errorCode`, `error`
- 회차 `status`, `failureCode`, `failureReason`
- 계산된 `nextScheduledAt`, `nextScheduleUpdated`

로그에는 알림 본문 원문 외의 토큰, API 키, FCM registration token, Telegram chat ID를 쓰지 않는다.

## 운영 점검

기본 명령은 읽기 전용 dry-run이다.

```bash
python -m alert_engine.audit
python -m alert_engine.audit --uid USER_UID
python -m alert_engine.audit --uid USER_UID --name "한투→토스하나이체" --now 2026-08-01T09:19:00+09:00
python -m alert_engine.audit --uid USER_UID --name "미래에셋 예약매도" --now 2026-08-01T09:19:00+09:00
```

출력의 `missing_occurrence_record`는 예정 회차에 정확한 ID의 성공/실패/시도 기록이 없음을 뜻한다.
`duplicate_occurrence_record`는 레거시 임의 문서 ID 때문에 같은 eventId가 둘 이상 존재함을 뜻한다.
도구는 기록을 삭제하거나 성공 처리하지 않으며 메시지 본문과 인증 정보도 출력하지 않는다.

## 명시적 복구 준비

먼저 Telegram Bot/FCM 공급자 로그와 기존 Firestore 기록을 확인해 실제 발송 가능성을 판단한다.
중복 위험을 수용하고 단일 회차를 다시 due로 만들기로 결정한 경우에만 다음을 실행한다.

```bash
python -m alert_engine.audit \
  --uid USER_UID \
  --rule-id RULE_ID \
  --scheduled-for 2026-07-31T12:15:00+09:00 \
  --apply-reset-cursor \
  --acknowledge-duplicate-risk

python -m alert_engine.main --uid USER_UID --job-scope all
```

월초 사례는 `--scheduled-for 2026-08-01T07:00:00+09:00`을 사용한다. 첫 명령은 알림을
발송하지 않고 해당 규칙 하나의 커서만 복구 요청 상태로 되돌린다. 동일 occurrence 문서가 이미
있거나 지정 시각이 현재 커서보다 과거가 아니면 거부한다. 두 번째 명령이 정상 선점·기록·발송
파이프라인을 실행한다.

## 배포 및 rollback

Firestore는 schemaless이므로 DDL 마이그레이션은 없다. 기존 규칙과 기존 발송 기록은 그대로
읽을 수 있고 신규 필드는 선택적이다. 배포 전 Python 테스트, TypeScript 타입 검사, Next.js 빌드,
실제 프로젝트 자격 증명을 사용한 audit dry-run을 수행한다.

`alertRules.enabled` collection-group 인덱스는 `firestore.indexes.json`에 이미 선언되어 있다.
배포되지 않은 경우 엔진은 전체 테이블 scan으로 우회하지 않고 실패하므로 다음을 먼저 실행한다.

```bash
firebase deploy --only firestore:indexes
```

코드 rollback은 가능하지만 새 엔진이 기록한 `nextScheduledAt`을 구버전 엔진이 무시하므로,
구버전의 시간 창 누락 문제가 다시 생긴다. 신규 회차/로그 필드는 삭제하지 않는다.

## Firebase Emulator 검증

운영 자격 증명 없이 실제 Firestore transaction과 security rules를 함께 검증한다.

```bash
npm run test:firestore-emulator
```

이 명령은 `demo-goralert` 프로젝트로 Auth/Firestore Emulator만 실행하고 다음을 확인한다.

- 회차 생성과 cursor 갱신의 원자성 및 두 worker 동시 선점
- transaction 충돌 재시도와 보수적 contention reconciliation
- lease 만료 복구, stale worker finalization 차단, 채널 상태 저장
- cursor 없는 구버전 규칙의 catch-up과 history/collection-group 조회
- 삭제된 규칙을 finalization이 재생성하지 않는지
- 로그인 사용자의 own rule/history 읽기·쓰기 허용 및 다른 사용자 경로 차단

PR에서는 `Alert Engine Firestore Emulator Tests` workflow가 동일한 테스트를 실행한다.
