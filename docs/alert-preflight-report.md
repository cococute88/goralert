# PR #14 운영 전 최종 점검 — 2026-11-03 KST

## 1. 원인 분석

초기 PR head `eed4ed5`는 `RECOVERY_ARMED=false`여도 workflow 조회와 Healthchecks `/fail`을
실행했다. 실행 승인과 감시 시작의 경계가 없고 문서가 success ping으로 check를 먼저 시작하도록
안내해 초기 설정 오탐을 만들 수 있었다. 새 setup 무호출 회귀가 원본에서 실패함을 확인했다.

추가로 heartbeat 부재/오래됨과 Firestore 권한·서버 설정 오류가 모두 unknown으로 합쳐져 Worker가
권한 오류에서도 자동 작업을 요청할 수 있었다. 일부 엔진 의존 파일은 safe-commit 비교 대상에서 빠져 있었다.

backlog에서도 selector 없는 once + calendar(일별 시각) 규칙을 반복 규칙처럼 미래로 이동시키는
계획 오류를 재현했다. 이는 엔진의 기존 `should_advance` 계약과 달랐다. 24시간 정책 자체는 유지하고
그 조건만 일치시켰다. 운영 데이터의 네 날짜 알림을 실제로 감사했다는 주장은 하지 않는다.

작업 시작: `C:\gv\goralert-recovery`, `codex/alert-reliability-recovery`, clean, remote/upstream 정상,
원격 branch와 0 ahead/0 behind, PR #14 OPEN. main은 `08add098`, 운영 3 workflow는
`disabled_inactivity`였다. 별도 `C:\gv\goralert-app`의 main/미커밋 작업은 건드리지 않았다.

## 2. 읽은 주요 파일

- `infra/monitor/worker.mjs`, `infra/monitor/wrangler.toml`
- `docs/alert-recovery-runbook.md`, 기존 `docs/alert-recovery-report.md`
- `alert_engine/backlog.py`, `reliability_store.py`, `engine.py`의 회복/보류/커서 진행 구간,
  `main.py`의 dry-run/Drain/heartbeat 구간, `test_push.py`의 선점/나이/결과 경계
- `app/api/engine-health/route.ts`, `lib/alerts/engine-health.ts`, `lib/alerts/github-dispatch.ts`, `firestore.rules`
- `.github/workflows/alert-engine.yml`, `alert-test-push.yml`, `keepalive.yml`, `engine-tests.yml`,
  `pr-validation.yml`, `alert-engine-emulator-tests.yml`
- `tests/alert-recovery.test.mjs`, `tests/firestore-emulator-rules.test.mjs`,
  `alert_engine/tests/test_recovery_safety.py`, `test_firestore_emulator.py`, `test_durable_scheduling.py` 관련 구간
- `scripts/run-firestore-emulator-tests.mjs`, `package.json`, 이번 신규 테스트/준비 script/보고서

## 3. 수정 파일 목록과 이유

이번 추가 변경은 기존 PR head 대비 다음 15개 파일이다. 기존 Firebase rules/운영 데이터/사용자 설정,
가격/캘린더 평가 의미, Production env/Secret은 변경하지 않았다. 삭제 파일은 없다.

| 구분 | 파일 | 이유 |
|---|---|---|
| 수정 | `infra/monitor/worker.mjs` | setup 무호출, 활성 감시 실패 유지, 사전 조건/의존 코드/실패 보고 강화 |
| 수정 | `infra/monitor/wrangler.toml` | 별도 `MONITORING_ACTIVE=false` 기본값 |
| 수정 | `app/api/engine-health/route.ts` | 읽기 가능/heartbeat 부재/권한 오류/대상 오류 구분 |
| 수정 | `lib/alerts/engine-health.ts` | dispatchReady/preflight 선택 필드 계약 |
| 수정 | `alert_engine/backlog.py` | selector 없는 일회성 일별 규칙의 원래 cursor 보존 |
| 수정 | `alert_engine/tests/test_recovery_safety.py` | once/selector, 24시간 경계, 120건 누적 요청 보류 회귀 |
| 수정 | `alert_engine/tests/test_firestore_emulator.py` | 실제 transaction에서 once calendar cursor 보존 검증 |
| 수정 | `tests/alert-recovery.test.mjs` | 활성 감시 fixture에 새 명시적 gate와 안전 SHA 적용 |
| 추가 | `tests/monitor-preflight.test.mjs` | 준비/활성/초기 복구/서비스 실패/권한/의존 파일/cron 실패 회귀 |
| 추가 | `tests/engine-health-preflight.test.mjs` | API 사전 조건 및 실제 익명 REST→앱 route emulator 검증 |
| 수정 | `scripts/run-firestore-emulator-tests.mjs` | 위 REST/app 통합 검증을 emulator 실행에 포함 |
| 수정 | `.github/workflows/alert-engine-emulator-tests.yml` | 상태 API/신규 통합 테스트 변경 시 emulator CI 실행 |
| 추가 | `scripts/prepare-monitor-bundle.mjs` | local-only GUI용 worker.js/설정/체크섬 생성, 외부 호출 없음 |
| 수정 | `docs/alert-recovery-runbook.md` | GUI 승인 A~E, Healthchecks New 유지, 실패별 복구 절차 |
| 추가 | `docs/alert-preflight-report.md` | 이번 사전 점검의 근거·검증 한계·준비 판정 |

## 4. 변경 내용

| 감시 flag | clock flag | dispatch | Healthchecks |
|---|---|---|---|
| 미설정/false | false 또는 approved-v1 | 0 | 0 ping, healthy=null/setup |
| true | false/미승인 값 | 0 | `/fail`: 활성 운영의 clock 해제를 숨기지 않음 |
| true | approved-v1 + 정상 사전 조건 | cadence대로 | 실제 health/Drain 결과로 success 또는 `/fail` |
| true | approved-v1 + unsafe SHA/inactive/API·권한 오류 | 0 | `/fail` 또는 ping 불능 시 task 실패/독립 deadline |

- setup은 configuration 확인에 외부 서비스를 호출하지 않는다. secrets 없는 첫 배포도 setup이다.
  공개 Worker GET은 mode/clockArmed/configured boolean만 보여 주고 healthy를 주장하지 않는다.
- Healthchecks는 준비 중 **New**로 둔다. 기존 check는 ping이 paused를 자동 재개할 수 있으므로
  pause만으로 오탐 방지를 보장하지 않는다. 이미 Up인 check를 setup으로 바꾸면 독립 deadline이 누락을 감지한다.
- 승인된 clock은 40자리 safe SHA, main ancestry, `alert_engine/` 전체/requirements/운영 workflow 무변경,
  두 workflow active, 앱의 `dispatchReady=true`를 검사한다. 구형 endpoint·target mismatch도 차단한다.
- Firestore GET 404는 읽기가 허용된 미생성 heartbeat로 구분한다. 401/403/5xx/요청 실패는 dispatch 불가다.
  익명 REST는 Firebase 공식 문서대로 security rules를 적용하며 admin credential을 앱에 추가하지 않았다.
- 초기 heartbeat가 없거나 오래돼도 연결 사전 조건이 통과하면 다음 :03/:33의 메인 실행으로 복구할 수 있다.
  이때 active monitor는 unhealthy를 숨기지 않는다. 초기 오탐 없는 cutover는 최초 수동 확인을 먼저 한다.
- Healthchecks non-2xx/timeout은 cron task를 실패시킨다. Worker 자체 정지/CPU 초과는 ping 누락으로
  외부 deadline이 감지한다. 토큰 만료 401/403과 rate limit 429/5xx도 테스트했다.
- 24시간 보류·미래 일정 정책은 그대로다. 한번만 실행할 일별 예약은 원래 cursor를 유지하고,
  selector 기반 calendar once만 기존 엔진과 같이 미래 평가를 이어간다. 120개 stale 테스트 요청은
  50/50/20으로 보류되며 fake 채널 호출은 0회였다. 운영 과거 데이터는 변경하지 않았다.

무료 구성은 문서상 가능하다. Cloudflare Free 100,000 requests/day, cron 5개/account,
50 subrequests/invocation, cron CPU 10ms다. 본 구성은 cron 1개/288회·일/최대 8 subrequests다.
Healthchecks Hobbyist는 $0/20 checks/100 logs이고 신규 check의 New 상태를 사용할 수 있다.
실제 계정 잔여 quota, CPU, email 수신 및 유료 옵션 상태는 **NOT VERIFIED**다.

## 5. 테스트 결과

최종 실행 로그는 로컬 ignored `verification/preflight-*`에 있다. 기존 412개 케이스를 유지하면서
새 gate에 맞는 fixture 한정 변경과 추가 회귀를 수행했다. Python 306개 + Web 144개 = **450개 PASS**로 기존 대비 38개를 추가했다. Web offline 141개와 emulator 전용 3개는 중복 계산하지 않았다. 원격 CI는 최종 채팅/PR에서 별도 확인한다.

| 명령/검증 | 범위 |
|---|---|
| `.venv\Scripts\python.exe -m pytest alert_engine/tests -q` | PASS: 288개, emulator module 1개 skip(아래 별도 실행) |
| `npx --yes firebase-tools@15.18.0 emulators:exec --only auth,firestore --project demo-goralert "node scripts/run-firestore-emulator-tests.mjs"` | PASS: Python 306개, Web 통합 13개(중복 unit 10개 + emulator 전용 3개) |
| `npm run test:web` | PASS: offline 141개, emulator 전용 3개는 위 별도 통과 |
| `npm run typecheck`, `npm run lint`, `npm run build` | PASS: 타입/린트/빌드, local Firebase public config 미제공 SW 경고는 실 Push 검증 아님 |
| `verification/actionlint/actionlint.exe` | PASS: 모든 Actions YAML |
| `node scripts/prepare-monitor-bundle.mjs` | PASS: 비승인 GUI 파일 생성, source/worker.js SHA256 동일, no network/no secret/no deployment |
| `git diff --check` | PASS: 공백/patch 검증 |

Worker/API는 주입된 fetch mock, Python 채널은 fake이며 실제 FCM/Telegram 전송이 아니다.
Firestore writes는 명시적인 loopback demo emulator fixture뿐이다. 운영 쓰기·발송·enable·merge는 0회다.

## 6. 발생 이슈와 제한

- **해결**: setup `/fail` 오탐, heartbeat unknown과 읽기 권한 오류 혼동, safe-commit 의존 코드 누락,
  selector 없는 once calendar의 미래 이동을 재현·수정했다.
- **NOT VERIFIED / 자격 증명 필요**: 실제 Firebase 네 날짜/전체 backlog 감사, live Vercel env/token 권한,
  live root rules 및 `/api/engine-health`, Cloudflare 계정 배포/CPU, Healthchecks 실통보.
- **실통보 한계**: mock으로 `/fail` 요청을 검증한 것은 email 도착 증명이 아니다. Worker 정지 시의
  deadline 정책은 공식 문서 근거이며 실제 서비스에서 정지·수신 테스트는 수행하지 않았다.
- **운영 승인 필요**: merge와 연결된 Production 배포, 기존 PR의 새 root get 규칙 반영,
  workflow 활성화, 최초 실제 engine/Drain/채널 실행, 외부 clock·감시 시작은 각각 별도 승인 단위다.
- Healthchecks 자체 장애까지 관리자 통보를 보장하지 않는다. 네트워크 결과 모호성에 exactly-once는
  불가능하며 기존 unknown/no resend 정책을 유지한다. npm의 기존 취약점은 이 사전 점검 범위에서 유지한다.

## 7. PR 정보 및 준비 판정

- 기존 [PR #14](https://github.com/cococute88/goralert/pull/14)에만 추가 commit을 Push한다. 새 PR 없음.
- branch `codex/alert-reliability-recovery`, target main. exact 추가 SHA/최종 원격 CI는 최종 채팅에서 확인한다.
- main merge/Production deploy/운영 enable·실발송·운영 데이터 쓰기를 하지 않았다. Vercel 연동이
  branch Preview를 만들 수 있으며 Production과 구분한다.

**[운영 반영 준비 판정] NOT READY**

코드는 회귀로 검증할 수 있지만 운영 데이터에 대한 24시간 정책 동의와 live 연결/권한·통보 검증이
완료되지 않았다. 무조건 merge→enable을 승인할 상태는 아니다. [runbook의 승인 A~E](./alert-recovery-runbook.md)를
따라 읽기 전용 확인을 먼저 완료한 뒤 단계별 병합·배포·가동을 승인한다. GUI 파일은 미리 준비했다.

## 최신 공식 근거 (2026-11-03 확인)

- [Cloudflare Free 제한](https://developers.cloudflare.com/workers/platform/limits/)
- [Cloudflare Dashboard 생성](https://developers.cloudflare.com/workers/get-started/dashboard/)
- [Cron GUI 및 최대 15분 전파](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
- [Healthchecks 가격](https://healthchecks.io/pricing/)
- [Healthchecks New/Up/Down/Paused](https://healthchecks.io/docs/)
- [Healthchecks pause 후 ping 동작](https://healthchecks.io/docs/configuring_checks/)
- [Healthchecks fail 신호](https://healthchecks.io/docs/signaling_failures/)
- [익명 Firestore REST의 Security Rules 적용](https://firebase.google.com/docs/firestore/use-rest-api)
