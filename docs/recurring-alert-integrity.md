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
  -> evaluate and render a deterministic occurrence snapshot
  -> Firestore transaction
       re-check cursor/schedule revision and rule enabled state
       notificationLogs/{occurrenceId} create(status=processing)
       alertRules.nextScheduledAt advance
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

## 기존 규칙 durable cursor 이관

recurrence가 있고 `nextScheduledAt`, `durableSchedulerVersion`, `schedulerMigration`이 모두 없는
문서는 durable scheduler 도입 전 legacy 규칙이다. worker가 처음 만난 실행은 조건 평가, occurrence
claim, notification history 생성, Telegram/FCM 호출을 하지 않는다. 대신 하나의 Firestore transaction에서
규칙 존재·활성 상태·현재 trigger/timezone·일정 변경 여부를 다시 확인하고 이관 기준 시각보다 엄격히
미래인 첫 회차만 `nextScheduledAt`으로 저장한다. 두 worker가 경합해도 한 transaction만 초기화하고
둘 다 발송 경계에 진입하지 않는다.

이관 transaction은 다음을 저장한다.

- `durableSchedulerVersion: 1`
- `schedulerMigration.kind: legacy_cursor_bootstrap`
- `schedulerMigration.migratedAt`: server commit 시각
- `schedulerMigration.backlogPolicy: skip_automatic_backlog`
- `schedulerMigration.backlogSkippedThrough`: 미래 cursor 계산의 기준 시각
- `schedulerMigration.initializedNextScheduledAt`: 최초 미래 회차

이 metadata는 과거 회차를 `sent`, `failed`, `skipped`, `cancelled`, `delivery_unknown`으로 위조하지
않는다. 두 확인된 누락 사례와 calendar 일별 backlog를 포함해 과거 회차는 자동 재발송하지 않는다.
필요한 과거 회차만 운영자가 audit recovery로 명시한다. rollback 때도 migration metadata와 cursor,
occurrence/history를 삭제하거나 되돌리지 않는다.

신규 브라우저 규칙과 명시적 일정 변경은 저장 transaction에서 `durableSchedulerVersion: 1`과 규칙
timezone으로 계산한 미래 `nextScheduledAt` UTC Firestore Timestamp를 함께 기록한다. version 또는
scheduler metadata는 있지만 cursor가 없는 문서는 legacy로 간주하지 않는다. worker는 provider를
호출하거나 cursor를 추론하지 않고 `schedulerError(code=missing_cursor_for_versioned_rule)`를 규칙에
기록하며, audit는 `corrupt_durable_scheduler`로 표시한다.

반복 주기·시각·timezone을 수정하면 브라우저 Firestore transaction이 기존
`nextScheduledAt` 회차를 `cancelled(schedule_changed)`로 영구 기록하고, 같은 transaction에서 새
일정의 미래 cursor와 scheduler version 및 `scheduleChangedAt`을 저장한다. 처리 중인 회차가 있으면
편집 저장을 거부한다. legacy 규칙을 화면에서 일반 필드만 저장할 때는 repository가 protected
scheduler/migration 필드를 payload에서 제거하므로 전체 저장이 실패하거나 metadata를 덮어쓰지 않는다.

## 중복 방지와 장애 경계

두 worker가 같은 회차를 보더라도 Firestore `create` 트랜잭션을 성공한 하나만 lease를 얻는다.
채널 API 호출 직전에는 채널 상태를 `sending`으로 영속화한다.

- `pending` 상태에서 worker가 중단되면 lease 만료 후 재개해 발송한다.
- `sent`/`failed` 결과 저장 후 중단되면 해당 채널은 다시 호출하지 않는다.
- 외부 채널 호출 후 결과 저장 전에 중단되면 Telegram/FCM에는 범용 idempotency 보장이 없으므로
  자동 재발송하지 않고 `unknown`/`delivery_unknown`으로 남긴다. 운영자가 공급자 로그를 확인한다.
- Telegram/FCM 호출 자체가 timeout/connection loss로 끝나 공급자 수신 여부를 확정할 수 없는
  경우에도 `failed`로 단정하거나 자동 재시도하지 않고 같은 `unknown` 정책을 적용한다.

각 채널의 `pending -> sending` transaction은 occurrence lease뿐 아니라 규칙의 존재/활성 상태를
다시 확인한다. claim 뒤 provider 호출 전에 사용자가 규칙을 삭제하거나 비활성화했다면 남은 채널은
호출하지 않고 그 경합을 영구 상태로 기록한다.

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

audit는 `legacy_cursor_uninitialized`, `legacy_cursor_initialized`, `explicit_recovery_requested`,
`corrupt_durable_scheduler`, `overdue_cursor`, `durable_rule_healthy`를 구분한다. 이관 완료 행에는
backlog 자동 발송 생략 여부, 생략 기준 시각, 초기화한 미래 occurrence가 함께 나온다.
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
발송하지 않고 해당 규칙 하나에 `schedulerRecovery(status=requested, scheduledFor, occurrenceId,
duplicateRiskAcknowledged)`를 transaction으로 기록하고 cursor를 그 과거 회차로 설정한다. 동시에
`durableSchedulerVersion`을 기록하므로 일반 legacy bootstrap과 구분된다. 동일 occurrence 문서 또는
임의 문서 ID의 동일 eventId가 이미 있거나, duplicate-risk 승인이 없거나, 처리 중이거나, 지정 시각이
현재 cursor보다 과거가 아니면 거부한다. legacy migration 이후에도 특정 과거 회차를 요청할 수 있다.
두 번째 명령만 정상 선점·기록·발송 파이프라인을 실행한다.

## 배포 및 rollback

Firestore는 schemaless이므로 DDL 마이그레이션은 없다. 기존 규칙과 기존 발송 기록은 그대로
읽을 수 있고 신규 필드는 선택적이다. 배포 전 Python 테스트, TypeScript 타입 검사, Next.js 빌드,
실제 프로젝트 자격 증명을 사용한 audit dry-run을 수행한다.

운영 cutover는 다음 순서를 지킨다.

1. worker를 중지하고 queued/in-progress 실행이 없는지 확인한다.
2. 대상 프로젝트가 `gorani-vercel`인지 확인한 뒤 indexes를 먼저 배포하고 composite 4개와
   `alertRules.enabled` collection-group field override가 모두 READY가 될 때까지 기다린다.
3. 신규 프런트엔드 코드를 배포한다.
4. 프런트엔드 호환성을 확인한 뒤 Firestore rules를 별도로 배포한다.
5. read-only audit로 legacy/migrated/corrupt/recovery/overdue 상태를 확인한다.
6. worker를 재활성화하고 첫 실행에서 legacy 규칙이 미래 cursor만 초기화하는지 모니터링한다.

`alertRules.enabled` collection-group 인덱스는 `firestore.indexes.json`에 선언되어 있다. 배포되지
않으면 엔진은 전체 collection-group scan으로 우회하지 않고 실패한다.

`notificationLogs.firedAt DESC` 단독 정렬은 Firestore 자동 single-field index가 제공한다. 이를
composite 목록에 선언하면 Firebase API가 `index is not necessary`로 배포를 거부하므로 별도
composite로 선언하지 않는다. 저장소의 4개 composite는 `kind`, `isTest`, 두 필드 조합, `tickers`
필터와 `firedAt DESC` 조합을 각각 지원한다.

```bash
firebase deploy --only firestore:indexes --project gorani-vercel
```

코드 rollback 시 worker부터 다시 중지한다. 새 엔진이 기록한 `nextScheduledAt`과
`schedulerMigration`을 구버전 엔진이 이해하지 못하므로 구버전 worker를 즉시 재활성화하지 않는다.
metadata, 신규 회차/로그, indexes를 삭제하지 않고 rules를 임시 완화하지 않는다.

## Firebase Emulator 검증

운영 자격 증명 없이 실제 Firestore transaction과 security rules를 함께 검증한다.

```bash
npm run test:firestore-emulator
```

이 명령은 `demo-goralert` 프로젝트로 Auth/Firestore Emulator만 실행하고 다음을 확인한다.

- 회차 생성과 cursor 갱신의 원자성 및 두 worker 동시 선점
- transaction 충돌 재시도와 보수적 contention reconciliation
- lease 만료 복구, stale worker finalization 차단, 채널 상태 저장
- cursor 없는 구버전 규칙의 미래-only 초기화, history 불변, 두 worker 수렴
- 일정 변경·삭제·비활성화·explicit recovery와 legacy 초기화 경합
- version은 있지만 cursor가 없는 손상 데이터의 provider 차단
- 삭제된 규칙을 finalization이 재생성하지 않는지
- 로그인 사용자의 own rule/history 읽기·쓰기 허용 및 다른 사용자 경로 차단

PR에서는 `Alert Engine Firestore Emulator Tests` workflow가 동일한 테스트를 실행한다.

브라우저 보안 규칙은 migration/recovery/error metadata, 처리 중 cursor, lease/occurrence 상태,
worker가 작성한 production history를 직접 변경하지 못하게 한다. 신규 규칙 create와 명시적
schedule-change transaction에만 정확한 scheduler version과 Timestamp cursor를 허용한다. 브라우저에는
일반 규칙 편집과 자체 테스트 로그가 유지되며 Admin SDK worker는 security rules를 우회하는 서비스
계정 경로를 쓴다.
