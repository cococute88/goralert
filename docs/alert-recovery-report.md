# 고라알림 장애 복구 작업 보고 — 2026-11-03

코드 수정·안전한 로컬 회귀·독립 branch/PR 준비 범위의 보고다. 운영 재활성화, 실발송,
운영 Firestore 쓰기, 환경변수/Secret 교체, main merge 및 Production 배포는 수행하지 않았다.
운영 실행은 [승인 후 재가동 절차](./alert-recovery-runbook.md)를 따른다.

## 1. 원인 분석

직접 원인은 disabled workflow에 대한 `workflow_dispatch` 거절이다. 인증된 GitHub REST에서
메인 엔진, 테스트 Drain, Keepalive의 **`disabled_inactivity`를 확정**했다. disabled_manually나
저장소 Actions 전체 차단과 구별했다. 저장소는 public, default branch main, Actions enabled,
allowed_actions all, default workflow permission read다. branch protection은 미설정(404)이며 변경하지 않았다.

마지막 main `08add098`는 2026-08-04 21:59:23 KST다. 메인 run 37128279163은 10/3 23:02:59,
8규칙/0전달/0오류, Drain run 37138081859는 10/4 01:46:22, 0요청/0전달이었다. 실제 run 로그를
재확인했다. Keepalive run 37611227483은 10/7 20:01:03 성공이었다. 정확한 비활성화 시각은
API에 없어 확정하지 않는다. 단순히 마지막 실행 시각을 장애 시작 시각이라고 쓰지 않는다.

근본 원인은 public 저장소의 scheduled inactivity 정책에 의존하면서 checkout/echo 성공만으로
활동이 유지된다고 전제한 구조, 그리고 Actions 자체 중단을 밖에서 감지하지 못한 감시 부재다.
[GitHub 공식 기준](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)은
repository activity 60일 부재다. 자동 커밋의 구체적 actor/token 유효성이 확실하다고 주장하지 않고
외부 clock + dispatch-only workflow + 독립 dead-man 감시를 준비했다. 외부 설정 전에는 재발 방지가 가동된 것이 아니다.

추가 결함 A~E를 모두 확인했다. origin/main 코드를 격리 로딩한 fake 재현에서 A dry-run은 요청 쓰기를
1회 시도했고, C는 두 DB 확인이 실패해도 fake 채널 호출을 1회 진행했다. B는 요청 원자적 선점 부재와
시각 기반 이벤트 생성, D는 dispatch/시간만으로 running 표시, E는 failed count에도 CLI 0 가능 경로를
확인했다. 새 회귀는 각 경로와 경합·중단·모호성·오래된 예약을 검증한다.

실제 Vercel 환경변수 값/토큰 권한과 운영 네 날짜 사례(10/4 가격, 10/17 VR, 10/31 날짜, 11/1 예약)의
개별 Firestore 상태는 **NOT VERIFIED**다. 기본 repo/workflow/ref와 main의 workflow_dispatch 존재는
확인했지만 실제 서버 설정값을 추측하지 않았다. 기존 Secret은 이름·갱신 시각만 확인했다.

## 2. 읽은 파일

다음은 직접 내용 또는 관련 구간을 검토한 주요 파일이다. 수정한 신규 파일도 검토했다.
전체 Python/Web 테스트를 실행한 사실과 모든 테스트 원문을 전부 읽었다는 주장은 구별한다.

- `.github/workflows/alert-engine.yml`, `alert-test-push.yml`, `keepalive.yml`, `engine-tests.yml`,
  `pr-validation.yml`, `alert-engine-emulator-tests.yml`
- `app/api/test-push/route.ts`, `app/settings/page.tsx`, `app/history/page.tsx`, `app/page.tsx`, `app/globals.css`
- `alert_engine/main.py`, `engine.py`, `test_push.py`, `delivery.py`, `firestore_client.py`, `config.py`,
  `recurrence.py`, `audit.py`, `models.py`, `channels/base.py`, `channels/push.py`, `channels/telegram.py`, `README.md`
- `lib/alerts/test-push.ts`, `repositories.ts`, `types.ts`, `collections.ts`, `schedule.ts`
- `alert_engine/tests/conftest.py`, `test_test_push_drain.py`, `test_durable_scheduling.py`,
  `test_channel_ambiguity.py`, `test_idempotency.py`, `test_legacy_scheduler_migration.py`,
  `test_delivery_isolation.py`, `test_audit.py`, `test_test_alert_noop.py`, `test_regression.py`
- `tests/alert-rule-contract.test.mjs`, `calendar-contract.test.mjs`, `firestore-emulator-rules.test.mjs`,
  `scripts/run-firestore-emulator-tests.mjs`, `scripts/generate-firebase-sw.mjs`
- `docs/android-push-verification.md`, `production-alert-health-findings.md`, `recurring-alert-integrity.md`
- `package.json`, `requirements.txt`, `pytest.ini`, `firestore.rules`, `.env.example`, `.gitignore`, `next.config.mjs`
- 변경 파일 표의 신규 backlog/store/health/dispatch/UI/Worker/회귀/복구 문서.

별도 원본 worktree에서는 기존 tracked diff의 범위와 engine/main/workflow 차이를 확인했다.
Cloud Run 이관 관련 미커밋 20개 파일 및 untracked 파일은 보존하고 이번 PR에 가져오지 않았다.

## 3. 수정 파일 목록

아래 자동 생성 inventory에 M(수정)/A(추가)를 구분했다. 삭제 파일은 없다. `keepalive.yml`은
이름을 유지한 수동 진단 workflow로 바꾸었고 파일을 삭제하지 않았다.

| 상태 | 파일 | 수정 이유 |
|---|---|---|
| M | `.env.example` | backlog 지연 기준과 외부 감시 설정 위치 안내 |
| M | `.github/workflows/alert-engine.yml` | GitHub schedule 제거, backlog 보호 설정, 외부 30분 dispatch 준비 |
| M | `.github/workflows/alert-test-push.yml` | 외부 fallback, 독립 concurrency, uid shell injection 방지 |
| M | `.github/workflows/engine-tests.yml` | feature push와 PR의 중복 CI 감소 |
| M | `.github/workflows/keepalive.yml` | 무효 no-op schedule을 수동 상태 진단으로 변경 |
| M | `.github/workflows/pr-validation.yml` | 전체 web 회귀와 actionlint, 중복 test/push 감소 |
| M | `.gitignore` | 로컬 venv/검증 artifact/비공개 감사 JSON 보호 |
| M | `alert_engine/README.md` | 실행 구조/보호/감시 운영 문서 연결 |
| M | `alert_engine/audit.py` | read-only 보호 계획/hold/inflight/조건 규칙 감사, 안전한 explicit 회복과 경합 재시도 |
| A | `alert_engine/backlog.py` | 24시간 경계의 보류 계획과 fail-closed 보호 gate |
| M | `alert_engine/channels/push.py` | 토큰/본문 조각 로그 노출 방지, 기존 무효 토큰 정리 유지 |
| M | `alert_engine/channels/telegram.py` | 예외 URL token/chat ID 노출 방지 |
| M | `alert_engine/config.py` | 엔진 3.1.0, 기본 24시간 보호 기준 |
| M | `alert_engine/delivery.py` | 호출 예외를 unknown 처리, 자동 중복 재시도 차단 |
| M | `alert_engine/engine.py` | fail-open 제거, backlog gate, dry-run repair 차단, 복구 후 미래 cursor 보존 |
| M | `alert_engine/firestore_client.py` | 새 transaction 노출, processing 큐 재개, 경로 기반 UID/ID |
| A | `alert_engine/health.py` | 전체 sweep 한 번의 sanitized 집계 heartbeat |
| M | `alert_engine/main.py` | Drain 실패 exit, 전체 실행만 heartbeat, 채널/평가 오류 집계 |
| A | `alert_engine/reliability_store.py` | 보류 interval/큐 lease/채널 경계/원자적 확정 transaction |
| M | `alert_engine/test_push.py` | stable request event, lease, 채널별 확정/모호성, stale 보류, read-only preview |
| M | `alert_engine/tests/test_delivery_isolation.py` | 모호한 provider 예외 기대값과 채널 격리 검증 |
| M | `alert_engine/tests/test_firestore_emulator.py` | 실제 queue claim/경합/보류/미래 처리/신원 경로 회귀 |
| M | `alert_engine/tests/test_legacy_scheduler_migration.py` | 명시적 복구 계약 occurrence ID 검증 |
| A | `alert_engine/tests/test_recovery_safety.py` | dry-run/lease/장애/보류/heartbeat/CLI 신규 회귀 |
| M | `alert_engine/tests/test_test_push_drain.py` | 큐 fake를 새 transaction 계약으로 갱신, 채널 timing 검증 |
| A | `app/api/engine-health/route.ts` | 권한/Firestore 조회 실패를 unknown으로 처리, 60초 cache |
| M | `app/api/test-push/route.ts` | active state 사전 검사, 204 접수 의미, 짧은 한국어 오류, timeout |
| M | `app/history/page.tsx` | 보류 범위와 채널별 오류 상세, pending을 running으로 추정하지 않음 |
| M | `app/page.tsx` | 과거 cursor를 오늘 예정과 분리, 엔진 상태 표시 |
| M | `app/settings/page.tsx` | 서버 증거 기반 진행/부분 성공/불명, 세 테스트 버튼 유지 |
| A | `components/alerts/EngineHealthStatus.tsx` | 엔진/평가/채널 마지막 확인 시각과 상태 표시 |
| A | `components/alerts/TestPushProgress.tsx` | 기존 디자인의 증거 기반 단계 컴포넌트 분리, unknown/partial/timeout 표시 |
| A | `docs/alert-recovery-report.md` | 원인/파일 inventory/검증 matrix/제한 사항 보고 |
| A | `docs/alert-recovery-runbook.md` | 승인 후 클릭 순서/비용/권한/감사/과거 회차/rollback 절차 |
| M | `docs/android-push-verification.md` | dispatch/fallback/실기기 검증 의미 갱신 |
| M | `docs/production-alert-health-findings.md` | 11월 확정 조사와 8월 조사의 구분 |
| M | `docs/recurring-alert-integrity.md` | 장기 catch-up 대신 영구 보류 정책 명시 |
| M | `firestore.rules` | sanitized heartbeat singleton get만 공개, list/write 차단 |
| A | `infra/monitor/worker.mjs` | 외부 clock, 보호 commit 검사, 상태 감시/독립 ping, 자동 enable 없음 |
| A | `infra/monitor/wrangler.toml` | 무료 경량 cron 설정, 기본 unarmed 및 secret 명세 |
| A | `lib/alerts/engine-health.ts` | 정상/지연/중단/불명 분류와 75/180분 기준 |
| A | `lib/alerts/github-dispatch.ts` | dispatch target/headers/안전한 오류 분류 공유 |
| M | `lib/alerts/repositories.ts` | server-confirmed queue 관찰, processing/partial/unknown/held 계약 |
| M | `lib/alerts/test-push.ts` | dispatch 상세 분리, 모든 요청 채널 확인 성공 검증 |
| M | `lib/alerts/types.ts` | recovery_held 및 backlog interval 선택 필드 |
| M | `package.json` | test:web 전체 오프라인 web suite 명령 |
| M | `scripts/run-firestore-emulator-tests.mjs` | emulator에서 전체 Python suite 실행 |
| A | `tests/alert-recovery.test.mjs` | API/state/UI/watcher/Worker/rollback mock 회귀 |
| M | `tests/firestore-emulator-rules.test.mjs` | 기존 owner 규칙 및 공개 heartbeat get-only 검증 |

## 4. 변경 내용

- **운영 엔진**: 원자적 예약 실패 시 즉시 fail-closed. read로 소유권을 대체하지 않는다. crossing
  상태 저장 실패도 오류로 반환한다. 정상 가격 comparator/recurrence/quiet hours/cooldown은 유지한다.
- **Backlog**: 24시간 초과 구간을 원래 cursor/trigger/상태와 함께 permanent recovery_held interval에
  transaction으로 보존한 뒤 최근/미래 cadence로 이어간다. cursor만 현재로 덮지 않는다. once는 원래
  cursor 보존, 기존 sent/history 보존, 중단된 sending은 unknown, 명시적 한 회차 복구는 미래 cursor 보존.
- **테스트 큐**: 요청 ID 기반 영구 log/transaction claim/10분 lease. immutable message/channel snapshot,
  per-channel sending과 결과 저장. 발송 후 기록 실패는 자동 resend하지 않는다. dry-run은 어떤 쓰기나
  채널 호출도 시도하지 않는다. 오래된/나이 불명 요청도 보류한다. no-credential 실패도 영속 증거로 남긴다.
- **채널**: 기존 deliver 및 PushChannel/TelegramChannel을 공유한다. 호출 예외는 unknown으로 보수적으로
  분류한다. FCM UNREGISTERED 정리 정책을 유지하고 Telegram 예외 URL의 bot token, Push token 조각을
  로그에 노출하지 않는다. 채널별 attempted/completed timestamps를 보존한다. 외부 exactly-once 보장 없음.
- **결과/UI**: job success/dispatch acceptance/engine evidence/channel acceptance를 분리한다.
  전체 성공/partial/failed/unknown/held/0건을 구별하고 실패 결과는 job exit 1이다. 서버 snapshot만 신뢰하며
  90초만으로 running을 표시하지 않는다. disabled 안내를 한국어로 제공하고 기술 상세를 접는다.
  대시보드 과거 cursor를 오늘 예정에서 분리하고, 기록에 보류 범위와 채널 오류 상세를 표시한다.
- **Heartbeat**: 전체 engine sweep의 마지막 완료/평가 성공/전체 정상/Push·Telegram 접수를 분리한다.
  실패는 성공 시각을 갱신하지 않고 dry-run/test/partial scope는 main heartbeat를 쓰지 않는다.
  75분 지연, 180분 중단, 조회 불가 unknown. 한 실행 한 집계 쓰기, 서버 60초 cache.
- **재발/감시**: GitHub schedule 제거, main 30분/Drain 5분 외부 clock 준비. no-op Keepalive를 수동
  진단으로 변경. Worker는 승인·보호 commit·active state를 검증하고 enable API를 호출하지 않는다.
  독립 Healthchecks가 Worker 또는 엔진 장애를 email로 통보하는 구성을 문서화했다. 실제 배포/통보 미실행.
- **CI**: PR에서 모든 Web 계약, offline Python, emulator 전체 Python + Rules, actionlint를 검사한다.
  push CI는 main만, feature branch는 PR에서 검사해 같은 변경의 push/PR 중복과 중복 개별 web 검사를 줄였다.
- **비용/설정**: 상시 서버 없음. 외부 무료 tier 범위 목표, 추가 secret은 Cloudflare의 repo 한정 Actions token과
  Healthchecks ping URL. 기존 운영 secret 교체 없음. 실제 비용/CPU/장기 휴면 조건은 배포 후 확인 필요.

## 5. 테스트 결과

최종 명령과 실제 결과는 아래와 같다. `verification/`의 실행 로그·이미지는 로컬 ignored artifact이며
운영 credential을 담지 않는다. 원격 CI 결과는 PR Checks에서 별도로 확인한다.

| 명령/검증 | 결과 |
|---|---|
| `.venv\Scripts\python.exe -m pytest alert_engine/tests -q` | PASS: 284, emulator module 1개 skip(아래 별도 실행) |
| `npx --yes firebase-tools@15.18.0 emulators:exec --only auth,firestore --project demo-goralert "node scripts/run-firestore-emulator-tests.mjs"` | PASS: Python 301개 + Rules 2개, skip 없음 |
| `npm run test:web` | PASS: offline web 109, emulator Rules 2개 skip(아래 별도 실행) |
| `npm run typecheck` | PASS |
| `npm run lint` | PASS |
| `npm run build` | PASS: public Firebase config 부재의 비활성 SW 경고, 실 Push config 검증 아님 |
| `actionlint 1.7.12` | PASS: 모든 Actions YAML |
| `git diff --check` | PASS |
| 실제 React progress 컴포넌트의 Chromium 렌더 | PASS: 390/1440px, light/dark, 가로 overflow 없음, technical disclosure 열림 |
| baseline fake 재현 | A/C 결함 재현, 운영 연결 없음 |

다음 matrix의 PASS는 offline fake/REST mock/Firestore demo emulator 범위다. 실제 운영 delivery의
PASS가 아니다. UI visual은 실제 컴포넌트 fixture이며 로그인한 운영 전체 화면은 NOT VERIFIED다.

| 요구 시나리오 | 결과/근거 |
|---|---|
| 1 active workflow | PASS: route/Worker mock |
| 2 disabled_inactivity | 실제 API 확정 + PASS: dispatch 차단 |
| 3 disabled_manually | PASS: mock 차단/감시 |
| 4 HTTP 422 | PASS: 거절/한국어 오류/실행 확인 안 함 |
| 5 dispatch 인증 실패 | PASS: preflight와 POST 401/403 |
| 6 204 후 job 실패 | PASS: acceptance와 실패 run/health 구별 |
| 7 정상 Push | PASS: production registry fake, 실제 기기 NOT VERIFIED |
| 8 정상 Telegram | PASS: production registry fake, 실제 수신 NOT VERIFIED |
| 9 Push만 실패 | PASS: partial/independent channel |
| 10 Telegram만 실패 | PASS: partial/independent channel |
| 11 모두 실패 | PASS: failed count/nonzero CLI |
| 12 요청 0건 | PASS: processed=0/sent=0 |
| 13 중복 요청 | PASS: fixed event/terminal reuse |
| 14 동시 worker | PASS: fake + 실제 emulator transaction |
| 15 발송 후 DB 저장 실패 | PASS: unknown/no resend; confirmed success 재사용 |
| 16 Firestore 조회 실패 | PASS: no send/unknown health |
| 17 예약 쓰기 실패 | PASS: reservation/hold fail-closed |
| 18 dry-run writes | PASS: 쓰기 method 시도 금지 fake + terminal repair 검사 |
| 19 worker 중단/restart | PASS: unexpired wait, expired pending resume, sending ambiguity |
| 20 장기 누적 예약 | PASS: interval hold/history 보존 |
| 21 복구 후 과거 재처리 | PASS: 반복 전송 차단, explicit 단일 복구 |
| 22 기존 occurrence 중복 | PASS: idempotency/terminal 상태 |
| 23 미래 정상 예약 | PASS: hold 이후 미래 실제 emulator 처리 |
| 24 가격 조건 | PASS: 기존 evaluator 전체 회귀 |
| 25 crossing 정합성 | PASS: 기존 비교/무변경 회귀 + DB 실패 |
| 26 Keepalive/장기 비활동 | API inactivity 확정, schedule 제거/외부 mock PASS; 실제 60일 운영 NOT VERIFIED |
| 27 heartbeat 누락 | PASS: unknown/age threshold |
| 28 health 조회 실패 | PASS: 정상 추정 차단 |
| 29 fallback 불가 | PASS: disabled 차단, fallback 보장 문구 없음 |
| 30 UI 실제 상태 | PASS: state machine/server snapshot/render; 운영 로그인/기기 NOT VERIFIED |
| 31 전체 Python | 위 명령 및 PR CI |
| 32 전체 web | 위 명령 및 PR CI |
| 33 TypeScript | PASS |
| 34 build/lint | PASS, production Firebase config/실발송 별도 |
| 35 Actions YAML | PASS: actionlint |

서로 다른 테스트 합계는 Python 301개 + Web 111개 = **412개 PASS**다. Web offline 실행에서 skip된 Rules 2개는 demo emulator에서 별도 통과했으며 중복 계산하지 않았다. 기존 baseline 대비 신규 회귀는 74개다. 요청 lease/동시성/실패 경계/보류/복구, server snapshot 및 dispatch/health/Worker 정책을 포함한다.
Python 3.11.15, Node 24.16.0에서 로컬 검증했다. PR CI는 저장소 정의대로 Python 3.11/Node 20이다.
운영 FCM/Telegram 호출 0회, 운영 Firestore 쓰기 0회다. emulator의 demo 데이터만 테스트했다.

## 6. 발생 이슈 및 남은 위험

- **해결**: 처음 새 transaction 동시성 테스트에서 emulator lock timeout이 발생했다. exhausted contention만
  제한 재시도하고 읽기 reconciliation으로 발송 소유권을 부여하지 않도록 수정했다. 전체 suite를 돌리면서
  기존 audit recovery vs legacy 초기화 경합에서도 같은 문제를 발견해 동일하게 수정·재검증했다.
- **해결**: progress 컴포넌트 분리 중 누락된 icon import를 타입/렌더 테스트가 잡아 수정했다.
  추가로 done metadata에 결과가 없거나 채널이 누락된 경우 success를 표시하지 않는 검사를 추가했다.
- **BLOCKED(자격 증명)**: 운영 Firestore read-only 감사와 Vercel 실제 env/dispatch token 권한 확인.
  로컬 env/ADC/gcloud/Vercel CLI 자격 증명이 제공되지 않았다. Secret 값 취득을 우회하지 않았다.
- **승인 대기/NOT VERIFIED**: PR merge, rules publish, Production 배포, 운영 workflow enable, 실발송,
  외부 Worker 배포 및 Healthchecks 실통보. 따라서 현재 운영 장애가 이미 복구됐다고 보고하지 않는다.
- **제약**: provider acceptance와 기기 수신은 다르고, timeout/DB 기록 실패에 exactly-once는 불가능하다.
  안전한 unknown 보류로 중복보다 자동 재발송 억제를 우선한다. 운영자가 공급자 증거를 확인해야 한다.
- **제약**: 초기 큰 pending queue 조회 비용은 pending/processing 수에 비례한다. 완료 전체 scan은 없다.
  root heartbeat get은 sanitized 집계만 공개한다. Healthchecks 자체 장애까지 무중단 보장하지 않는다.
- **기존 이슈**: npm audit 29개(12 moderate/16 high/1 critical) 보고. Next.js 등 광범위 업그레이드는
  별도 보안 작업으로 남겼다. 로컬 build의 Firebase config 경고는 실 Push 미검증을 뜻한다.

## 7. Git/PR 및 운영 영향

- 원본: `C:\gv\goralert-app`, main, 작업 시작 전 origin/main과 0 ahead/0 behind.
- 독립 worktree: `C:\gv\goralert-recovery`, branch `codex/alert-reliability-recovery`, target `main`.
- 기존 local branch 목록과 관련 open PR을 확인했다. 시작 시 open PR 없음. 기존 파일은 reset/stash/delete/force push 없이 보존했다.
- 엔진/앱/외부 clock·문서 순서의 논리적 commits. 정확한 SHA/PR 번호/직접 URL/최종 CI는 최종 채팅 보고에 기재한다.
- main 반영 없음. Production 배포 없음. 운영 workflow enable/dispatch 없음. 사용자 승인 없이 merge하지 않는다.
- [운영 재가동 체크리스트](./alert-recovery-runbook.md#운영-재가동-체크리스트--사용자-승인-이후)에 GitHub,
  Firebase, Vercel, 외부 감시 화면의 클릭 순서와 과거 회차 보류·단일 복구·rollback을 정리했다.
