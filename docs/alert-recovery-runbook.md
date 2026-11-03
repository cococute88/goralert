# 알림 장애 복구 및 장기 운영 절차

기준일: 2026-11-03 KST. 이 문서는 **준비 절차**다. 이 변경에서 운영 workflow 활성화,
실발송, Firestore 운영 쓰기, Secret 교체, main merge 또는 Production 배포를 수행하지 않는다.
각 운영 단계는 사용자 승인 이후에만 진행한다.

## 확인된 장애와 적용 범위

- main: `08add098184dab88d562766ebb4f4047315c5004`, 2026-08-04 21:59:23 KST.
- 메인 엔진, 테스트 Drain, Keepalive: GitHub REST에서 모두 `disabled_inactivity` 확인.
- 저장소는 public, Actions 허용, allowed_actions=all, 기본 branch=main.
- 마지막 메인 실행: 2026-10-03 23:02:59 KST, [run 37128279163](https://github.com/cococute88/goralert/actions/runs/37128279163).
  실제 로그: processed=8, delivered=0, errors=0, not_due=5, condition_false=3.
- 마지막 Drain: 2026-10-04 01:46:22 KST, [run 37138081859](https://github.com/cococute88/goralert/actions/runs/37138081859).
  실제 로그: processed=0, sent=0, failed=0, error=0. 채널 전달 성공을 검증한 실행이 아니다.
- 마지막 Keepalive: 2026-10-07 20:01:03 KST, [run 37611227483](https://github.com/cococute88/goralert/actions/runs/37611227483).
- 비활성화된 정확한 시각은 workflow API가 제공하지 않는다. 마지막 정상 실행 이후부터
  이번 상태 조회 사이의 장애이며, 60일 정책과 `disabled_inactivity`는 확인됐지만 시각을 역산해 확정하지 않는다.
- 현재 Vercel 서버 환경변수의 실제 값/권한 및 운영 Firestore의 10/4·10/17·10/31·11/1 개별
  알림 상태는 로컬 자격 증명이 없어 **NOT VERIFIED**다. 저장소 기본 대상은
  `cococute88/goralert`, `alert-test-push.yml`, `main`이다.

GitHub는 public 저장소에서 60일 repository inactivity 후 **scheduled workflow**를 비활성화한다고
명시한다. 기존 checkout/echo 성공은 repository activity 보장이 아니며, Keepalive 자신도 중단됐다.
자동 커밋이 어떤 actor/token 조건에서 inactivity 판정을 갱신하는지 공식 보장이 충분하지 않아
이 구현은 자동 커밋을 사용하지 않는다.

## 변경 후 실행 및 감시 구조

```text
앱 테스트 버튼 -> 기존 Firestore 큐 -> 인증된 Vercel dispatch bridge
                                      -> GitHub Actions -> Python common deliver
                                                            -> FCM / Telegram
                                                            -> 채널별 영속 결과 -> 앱

Cloudflare Worker (매 5분) -> workflow 상태/보호 commit/엔진 heartbeat/Drain 결과 조회
                          -> 메인 엔진 :03/:33 UTC dispatch (30분)
                          -> 테스트 Drain 매 5분 dispatch
                          -> Healthchecks success 또는 /fail ping -> 관리자 email
Worker 자체 중단         -> Healthchecks deadline 누락 -> 관리자 email
```

메인 엔진과 Drain은 GitHub `schedule`을 제거하고 `workflow_dispatch`만 유지한다. 따라서 문서상
scheduled inactivity 판정에 주기를 맡기지 않는다. GitHub의 전반적 가용성/정책 변경이나 토큰 만료까지
방지한다고 보장하지 않으며 별도 감시가 이를 통보한다. 기존 Actions runner, Firebase, Vercel, 채널
구현은 유지한다. `keepalive.yml`은 수동 상태 조회 도구가 되며 활성화할 필요가 없다.

외부 Worker는 초기 `MONITORING_ACTIVE=false`, `RECOVERY_ARMED=false`다. 준비 상태에서는 외부 호출·dispatch·Healthchecks ping을 모두 하지 않는다. Healthchecks는 첫 ping 전 **New**로 유지하며 수동 success ping으로 미리 시작하지 않는다. 이미 활성화된 감시를 준비 모드로 바꾸어도 기존 Healthchecks는 ping 누락으로 장애를 감지한다.

감시를 활성화한 뒤에는 clock 해제도 실제 장애다. `MONITORING_ACTIVE=true`이고 `RECOVERY_ARMED=false`이면 dispatch 없이 `/fail`을 보낸다. Worker가 감시를 자동 pause하거나 성공으로 위장하지 않는다. `approved-v1`과 `MIN_SAFE_COMMIT`이 설정돼야
dispatch한다. main이 검토된 commit을 포함하며 보호 대상 engine/workflow 파일이 그 commit 이후
변하지 않았는지 확인한다. 보호 파일 변경, 되돌리기 commit, 비교 결과 누락/300개 이상 파일,
inactive workflow, 인증 오류는 차단·통보한다. 향후 engine 변경도 검토 후 기준 commit을 갱신해야 한다.
앱 UI/일반 문서만 바뀌면 갱신할 필요가 없다. `alert_engine/` 전체(의존 코드·테스트·README 포함), root requirements 및 두 운영 workflow 변경은 재검토 후 기준 SHA를 갱신한다. 자동 workflow enable 코드는 없다.

### 상태 해석

완료한 전체 scope engine 실행만 `systemHealth/alertEngine`에 집계 1회를 기록한다. 사용자 ID,
본문, token은 넣지 않는다. 평가 실패는 평가 성공 시각을 갱신하지 않고, 전달 실패는 전체 정상 시각을
갱신하지 않는다. test drain, uid 한정, partial scope, dry-run은 이 heartbeat를 쓰지 않는다.
프로세스 중단은 성공 heartbeat를 남기지 않는다.

- 정상: 마지막 정상 완료 75분 이내이고 최신 평가/전달 오류 없음.
- 실행 지연: 75분 초과 또는 최신 평가/전달 오류 존재.
- 엔진 중단: 180분 초과 또는 workflow disabled.
- 확인 불가: heartbeat 부재, 미래/잘못된 시각, 조회 실패, GitHub state 불명.

75분은 30분 주기 두 번과 실행 지연 여유를 고려한 경고 기준이다. 장애 SLA를 보장하지 않는다.
Push/Telegram 최근 공급자 접수 시각과 평가 성공 시각을 분리한다. `sent`는 공급자 접수 증거이며
Android 기기 수신/사용자 열람의 보장이 아니다. Push는 기존대로 최소 한 등록 기기 접수 기준이다.

`/api/engine-health`는 60초 process cache를 사용한다. 공개 heartbeat get은 집계 정보만 허용하고
collection list/client write는 차단한다. 기존 계정/사용자 collection 권한은 유지한다.

## 밀린 예약 및 테스트 요청 정책

기본 허용 지연은 **24시간** (`ALERT_BACKLOG_MAX_AGE_MINUTES=1440`)이다. 일상적 2시간 지연은
기존 회차 의미로 처리하지만, 장기 장애의 과거 알림은 자동 발송하지 않는다.

1. 오래된 cursor를 만나면 `backlog_plan`이 보류 범위를 계산한다. 미래 규칙은 이 경로에 들어오지 않는다.
2. 하나의 transaction에서 규칙 활성/일정 변경/cursor/진행 중 lease를 재확인한다.
3. `notificationLogs/{ruleId}:backlog:{originalCursorUTC}`에 `recovery_held` interval을 영구 생성한다.
   원래 cursor, 원래 schedule status/occurrence ID, trigger snapshot, 시작 포함/끝 제외 범위를 보존한다.
4. 그 기록과 **같은 commit**에서 허용 지연 범위 내 첫 회차로 cursor를 이어간다. 그것도 없으면
   실제 cadence상의 첫 미래 회차가 된다. 단순히 현재 시각을 저장하지 않는다.
5. 이후 실행이 최근/미래 회차를 정상 처리한다. 보류 구간의 모든 회차를 하나씩 재발송하지 않는다.
6. 독립적인 미래 회차가 없는 once 규칙은 원래 cursor를 유지한 채 보류한다. `calendar` 주기라도 이벤트 selector가 없는 일회성 날짜/가격 규칙은 다음 날로 옮기지 않는다. 이벤트 selector 기반 once만 기존 엔진처럼 미래 일별 평가를 이어간다. 반복 발송하지 않는다.
7. 기존 `sent`/failure 기록은 변경하지 않는다. 보류 interval 자체는 미발송 확정 목록이 아니므로
   과거 개별 로그/공급자 결과와 함께 감사한다. 보류는 `sent`나 완료/스킵으로 위조하지 않는다.
8. 중단된 processing 회차의 활성 lease는 건드리지 않는다. 만료된 `sending`은 `unknown`으로
   보존하며 pending 과거 채널을 자동 발송하지 않는다. 기존 성공 결과도 보존한다.

예: 10/1 월초 cursor가 11/3에 복구되면 [10/1, 12/1) 범위가 보류되고 12/1의 정상 예약은 유지된다.
일별 calendar 규칙은 24시간 이내 최근 회차를 유지한다. 오래된 가격 조건의 과거 참/거짓은 현재
가격으로 복원하지 않는다. recurrence 없는 가격/crossing 규칙은 기존 평가 의미를 유지한다.

테스트 큐는 `settings-test:request:{requestId}`를 영구 ID로 사용한다. transaction 선점과 10분 lease,
`pending -> sending -> sent/failed/unknown`을 적용한다. 완료 재실행은 새 이벤트가 되지 않는다.
발송 후 DB 기록 실패는 lease 만료 후 unknown으로 확정하며 재발송하지 않는다. 저장된 sent는 재사용한다.
24시간 초과 또는 requestedAt 증거가 없는 요청은 `recovery_held`로 남기고 자동 소비/발송하지 않는다.
동일 요청 ID가 다른 사용자 경로를 가리키지 않도록 UID/ID는 문서 경로에서만 가져온다.

Drain의 sent count는 **요청한 모든 채널 성공**이다. partial/unknown/failed/held/error는 각각 세고 job
exit 1을 반환한다. 0건은 processed=0/sent=0인 정상적인 빈 실행이다. 개별 요청 실패는 다음 요청을
막지 않는다. DB 소유권을 확정할 수 없으면 발송하지 않는다. 외부 exactly-once는 보장할 수 없다.

### 읽기 전용 감사 (운영 변경 전)

운영 자격 증명은 환경에 안전하게 제공하고 파일·콘솔·PR에 복사하지 않는다. 이번 작업에는 제공되지
않아 운영 감사는 실행하지 않았다. 로컬에서 다음은 read-only이며 실제 발송하지 않는다.

```powershell
.venv\Scripts\python.exe -m alert_engine.audit > private-audit.json
.venv\Scripts\python.exe -m alert_engine.main --test-push --dry-run
```

감사 JSON에는 UID/규칙명이 포함되므로 로컬에만 보관하고 공유 전에 가린다. audit는 scheduler,
proposedBacklogHold, recorded occurrence와 채널 상태, in-flight/missing-inflight,
held interval, 중복 eventId, unscheduled condition을 구분한다. status 조회 실패는 정상으로 추정하지 않는다.
10/4 가격 조건, 10/17 VR 주문, 10/31 날짜, 11/1 예약을 규칙 ID로 대조하고 실제 기록/공급자 증거를 확인한다.

### 과거 회차를 꼭 보내야 할 때

사용자가 별도로 승인한 **한 회차만** 기존 audit recovery로 요청한다. 작업 전 공급자 기록, 동일 eventId,
legacy random-ID log 및 unknown 결과를 확인한다. 복구 권한은 기본 경로의 자동 보류를 우회할 수 있으므로
명령 자체가 승인된 운영 데이터 변경이다.

```text
python -m alert_engine.audit --uid USER_UID --rule-id RULE_ID
  --scheduled-for 2026-10-01T07:00:00+09:00
  --apply-reset-cursor --acknowledge-duplicate-risk
```

실제로는 한 줄 명령으로 실행한다. 이는 발송 없이 명시적 recovery metadata를 준비한다. 이어지는
engine 실행이 그 한 회차를 처리하며 저장돼 있던 미래 cursor를 복원한다. held once의 같은 cursor도
동일 trigger snapshot의 보류 증거가 있을 때만 허용한다. 원래 occurrence가 이미 존재하면 거부한다.
unknown/기존 sent 회차를 무조건 다시 보내는 기능은 제공하지 않는다. 보류 기록은 삭제하지 않는다.
스킵을 원하면 발송하지 않은 보류 기록을 그대로 보존하고 운영 결정 기록을 별도로 남긴다.

## 운영 재가동 체크리스트 — 사용자 승인 이후

현재 판정은 **NOT READY**다. 코드 회귀 통과와 운영 준비 완료는 다르다. 운영 네 날짜의 읽기 전용
감사, live rules/환경변수/실통보가 검증되지 않았다. 아래는 승인 단위별 GUI 절차이며 이번 작업에서
어느 운영 단계도 실행하지 않았다. 체크리스트를 읽는 것만으로 승인된 것이 아니다.

### 승인 단위 A — 읽기 전용 사전 확인 (쓰기·실행 없음)

| 서비스 | 클릭 경로와 확인할 내용 | 통과 조건 |
|---|---|---|
| GitHub | 저장소 → Pull requests → **#14** → Files changed / Checks | 새 commit의 모든 CI 성공, main 대상, merge하지 않음 |
| GitHub | Settings → Secrets and variables → Actions | 기존 `FIREBASE_SERVICE_ACCOUNT`, `TELEGRAM_BOT_TOKEN`, variable `FIREBASE_PROJECT_ID` 존재 확인; 값 복사/교체 금지 |
| Firebase | Firestore Database → Data → `users/{uid}/alertRules` / `notificationLogs` / `testPushRequests` | 아래 감사 표를 실제 문서로 작성; 수정/삭제 버튼 사용 금지 |
| Firebase | Firestore Database → Rules / Indexes | 기존 live rules와 PR diff 비교, enabled/status COLLECTION_GROUP index READY; Publish하지 않음 |
| Vercel | goralert 프로젝트 → Settings → Environment Variables | Production의 기존 Firebase public config, `GITHUB_DISPATCH_TOKEN`, `GITHUB_REPO`, `GITHUB_WORKFLOW_FILE`, `GITHUB_WORKFLOW_REF` 존재/대상/권한 확인; 저장/교체하지 않음 |
| Cloudflare | Workers & Pages / 계정 요금제 | Free 및 사용 가능한 cron slot 확인; 아직 배포하지 않음 |
| Healthchecks | 프로젝트 / Integrations / 요금제 | 무료 check 여유와 관리자 email 수신 경로 확인; 기존 활성 check를 임의 pause하지 않음 |

Vercel의 기본 대상은 `GITHUB_REPO=cococute88/goralert`, `GITHUB_WORKFLOW_FILE=alert-test-push.yml`,
`GITHUB_WORKFLOW_REF=main`이다. `GITHUB_DISPATCH_TOKEN`은 서버 전용 repo 한정 Actions read/write,
Contents read 권한/유효기간을 사용자가 확인한다. 비밀 값을 문서·채팅·Git에 붙여 넣지 않는다.

운영 데이터 감사는 다음 표를 실제 값으로 채운 뒤 24시간 정책을 승인한다. **현재 미작성/NOT VERIFIED**다.
GUI로 일정이 복잡해 정확한 recurrence를 판정하기 어렵다면 앞의 read-only audit를 자격 증명이 있는
승인된 환경에서 실행한다. 대시보드 표시만 보고 미발송/완료로 단정하지 않는다.

| 사례 | 확인 필드 | 승인 전 확인할 결과 |
|---|---|---|
| 10/4 가격 조건 | trigger.recurrence 유무, lastValue/lastTriggeredAt, 기존 eventId/채널 결과 | recurrence 없는 조건의 과거 참/거짓은 복원하지 않음; 현재 가격 의미 유지 |
| 10/17 VR / 10/31 날짜 / 11/1 예약 | nextScheduledAt, trigger.mode/recurrence/time/tz, lastOccurrenceId/scheduleStatus | 24시간 초과 여부, once/selector 여부, 보류 범위와 다음 cadence 확인 |
| 기존 processing | occurrence channels/leaseExpiresAt | sent 재사용, sending은 unknown, active lease 보존 |
| 테스트 큐 | requestedAt/status/channels/logId | 24시간 초과·시각 불명 요청 보류, 최근 요청의 실발송 가능성 별도 승인 |

### 승인 단위 B — 병합·앱 배포·기존 PR의 집계 읽기 규칙 반영

1. **GitHub → PR #14 → Checks**가 모두 성공한 것을 확인한 뒤 승인된 경우에만 **Merge pull request**.
   Vercel 자동 Production 배포가 연결돼 있을 수 있으므로 이 단위는 앱 배포 승인도 포함한다.
2. **Firebase → Firestore Database → Rules**: PR #14에서 이미 검토한 `systemHealth/alertEngine` singleton
   get-only 추가분과 live rules를 비교한다. 기존 사용자 규칙을 전체 파일 복사로 덮어쓰지 않는다.
   추가분 반영도 별도 승인 후 Publish한다. 이번 사전 점검은 `firestore.rules`를 변경하지 않았다.
3. **Vercel → Deployments → 해당 main commit**의 Ready/Production과 기존 도메인을 확인한다.
   변수는 기존 값을 유지한다. 필요한 변경/재배포가 발견되면 그 변경을 별도로 승인받는다.
4. 아직 두 운영 workflow는 켜지 않는다. Worker의 두 flag도 false다.

### 승인 단위 C — Cloudflare·Healthchecks 준비 (실발송·감시 시작 없음)

로컬 `scripts/prepare-monitor-bundle.mjs`가 `verification/monitor-bundle/`에 **worker.js**, **configuration.txt**,
체크섬 README를 미리 생성한다. 배포/secret 취득/외부 호출을 하지 않는다. 사용자는 생성된 파일을
에디터에서 열어 복사하면 되며 터미널 작업은 필요 없다. Worker는 단일 파일이며 npm 설치/빌드도 필요 없다.

1. **Cloudflare → Workers & Pages → Create application → Worker / Hello World → Deploy**로 빈 Worker를
   만든다. 이름 예: `goralert-watchdog`. 생성 직후 **Edit code**에서 준비된 worker.js 내용 전체로 교체 →
   **Deploy**. 기본 flag가 없으면 setup이라 외부 호출하지 않는다. 계정 UI 문구가 다르면 공식 Dashboard 안내를 따른다.
   같은 Worker가 이미 있다면 새 clock을 중복 생성하지 말고 기존 설정/실행을 먼저 검토한다.
2. Worker → **Settings → Variables and Secrets → Add**에서 다음을 설정한다.

   | 이름 | 종류 | 준비 단계 값 |
   |---|---|---|
   | `MONITORING_ACTIVE` | Text | `false` |
   | `RECOVERY_ARMED` | Text | `false` |
   | `MIN_SAFE_COMMIT` | Text | merge 후 main에서 검토한 40자리 SHA |
   | `GITHUB_ACTIONS_TOKEN` | Secret/encrypted | repo 한정 토큰을 사용자가 직접 입력; Actions read/write, Contents read |
   | `HEALTHCHECK_PING_URL` | Secret/encrypted | 다음 단계에서 얻은 check의 기본 HTTPS ping URL; `/fail` 접미사 제외 |

   main SHA는 **GitHub → Code → main → 최신 commit → Copy full SHA**로 얻는다. PR head SHA를
   squash merge 이후 main SHA라고 가정하지 않는다. 토큰 신규 발급이 필요하면 **GitHub 사용자 Settings →
   Developer settings → Personal access tokens → Fine-grained tokens**에서 해당 저장소만 선택한다.
   새 토큰 생성/영구 접근 부여는 사용자가 승인하고 직접 수행한다. 기존 Secret을 교체하지 않는다.
3. **Healthchecks → Add Check**: 이름 `goralert-engine-watchdog`, Schedule **Simple**, **Period 5분 / Grace 10분**.
   **Integrations → Email**에 관리자 수신 경로를 연결한다. 새 check는 **New**로 유지한다. ping URL을
   브라우저에서 열거나 success/test ping을 보내 감시를 미리 시작하지 않는다. 관리 API key는 Worker에 필요 없다.
   이미 존재하는 check를 재사용한다면 준비 중 pause와 재개는 의도적인 유지관리로 승인받아 수행한다.
   기본 paused check는 ping을 받으면 자동 재개할 수 있으므로 pause만으로 준비 오탐을 막는다고 가정하지 않는다.
4. Cloudflare의 `HEALTHCHECK_PING_URL` encrypted secret 입력을 완료한다. Worker → **Settings →
   Triggers → Cron Triggers → Add**에서 UTC expression을 한 개만 등록한다.

   ```text
   3,8,13,18,23,28,33,38,43,48,53,58 * * * *
   ```

5. Cron 변경은 공식적으로 최대 15분 전파 시간이 있다. **Observability / Cron Events**에서 setup 실행을 확인한다.
   Worker URL을 브라우저로 열면 `mode: setup`, `healthy: null`, `configured` boolean만 나온다.
   토큰 값/핑 URL은 나오지 않고 이 GET 자체가 작업·ping을 실행하지 않는다. 이것은 연결 성공 증명이 아니다.
   `configured` 세 항목이 모두 true인지 확인하고, false가 있으면 다음 승인 단계로 진행하지 않는다.
   flags false에서 Healthchecks는 New를 유지해야 한다. 이미 Up이던 check는 ping 누락을 감지하므로
   감시를 중단하려면 별도의 의도적인 maintenance 절차를 따라야 한다.

### 승인 단위 D — 운영 workflow 활성화와 최초 수동 검증

이 단계부터 운영 쓰기와 실제 채널 전송이 가능하다. 단위 A의 데이터 감사 및 정책 동의가 선행되어야 한다.
앱 사용자에게 테스트 버튼을 누르지 않도록 안내하고 기존 queued/in-progress 실행을 먼저 검토한다.

1. **GitHub → Actions → Goralert Alert Engine → Enable workflow**, 이어서 **Goralert Test Push Drain →
   Enable workflow**. Keepalive/Diagnostics는 켤 필요 없다. main에 schedule이 없는지 다시 확인한다.
   활성화 후 앱 테스트 버튼에 의한 직접 dispatch는 가능해지므로 승인 범위에 포함한다.
2. 브라우저에서 **goralert.vercel.app/api/engine-health**를 연다. `dispatchReady: true`와
   `preflight: ready` 또는 `heartbeat_missing`을 확인한다. `firestore_forbidden`, `configuration_unavailable`,
   `target_mismatch`, `github_unavailable`, `workflow_inactive`, `read_failed`는 중지 조건이다.
   기존 endpoint여서 이 필드가 없거나 404이면 새 앱 배포를 먼저 확인한다.
3. **Actions → Goralert Alert Engine → Run workflow → main / all / dry-run=true → Run workflow**.
   Actions 실행 로그에서 보류 예정 및 오류를 확인한다. dry-run은 heartbeat도 쓰지 않으므로 첫 상태가
   unknown인 것은 정상이다. dry-run 성공을 실제 발송 성공이라고 하지 않는다.
4. 별도 실발송/운영 쓰기 승인 후 같은 화면에서 **dry-run=false**로 1회 실행한다. 과거 구간은 보류하고,
   24시간 내 due/현재 가격 조건은 실제 발송할 수 있다. Firebase Data에서 hold 기록·미래 cursor가 함께
   저장됐는지 확인한다. `/api/engine-health`의 healthy/최근 완료를 확인한다(서버 cache 최대 60초).
5. **Goralert Test Push Drain → Run workflow → main**은 기존 최근 요청까지 전송할 수 있으므로 승인 후만
   실행한다. 오래된 요청은 50건씩 보류하며 job exit 1일 수 있다. 원인을 확인해 과거 요청을 삭제/완료
   위조하지 않는다. pending이 정리된 뒤 다음 빈 실행은 processed=0/sent=0, job 성공인지 확인한다.
6. 승인된 사용자만 앱에서 Push/Telegram/둘 다 테스트를 수행하고 실제 수신을 확인한다. FCM 공급자 접수와
   기기 수신은 구분한다. worker test drain job 성공 기록이 45분 이내인지도 확인한다.

### 승인 단위 E — 자동 clock·독립 감시 동시 시작

1. 새 Healthchecks check는 여전히 **New**, 이메일 연결 완료, Cloudflare cron 전파 완료인지 확인한다.
   초기 수동 engine/Drain 결과가 정상일 때 진행한다. 기존 paused check는 기본 ping 자동 재개 설정을
   확인하고, 수동 재개 sticky 설정을 사용했다면 승인된 유지관리 종료 시 GUI에서 Resume한다.
2. **Cloudflare → Worker → Settings → Variables and Secrets**에서 먼저 `RECOVERY_ARMED=approved-v1`을
   저장한다. `MONITORING_ACTIVE=false` 동안에는 이 값만으로 dispatch하거나 ping하지 않는다.
   마지막으로 `MONITORING_ACTIVE=true`를 저장한다. 다음 cron부터 검증과 자동 실행·감시가 함께 시작된다.
3. **Healthchecks → 해당 check**에서 첫 정상 cron의 ping으로 **Up** 전환을 확인한다. 준비 중 임의 success
   ping은 필요 없다. :03/:33 UTC main, 5분 Drain, 최신 heartbeat, 관리자 email 경로를 확인한다.
4. 초기 heartbeat가 없거나 오래돼도 **dispatchReady=true**인 경우 승인된 clock은 다음 :03/:33에 복구 실행을
   요청한다. 그동안 상태를 healthy로 위장하거나 장애 ping을 유예하지 않는다. 첫 완료 후 다음 cron에서 정상
   ping으로 회복한다. 최초 오탐 없는 설정을 원하면 단위 D의 수동 최초 실행을 먼저 완료한다.
5. 승인된 통보 테스트는 운영 clock을 함부로 멈추기보다 별도의 임시 check에서 실패/누락 ping을 검증한다.
   실제 운영 check의 end-to-end 통보 검증은 별도 유지관리 승인으로 실행한다. 이번 작업에서는 실통보하지 않았다.

### 장애·자동 실행 중단 시 복구 경로

| 상황 | 자동 동작 | GUI 조치 |
|---|---|---|
| GitHub disabled/401/403/429/5xx, 안전 코드 변경 | dispatch 차단, active monitor `/fail` | GitHub Actions 상태/토큰 유효기간/비교 기준 검토; 무조건 enable/토큰 교체하지 않음 |
| Firestore 권한/서버 env/old endpoint | dispatch 차단, `/fail` | Firebase Rules와 Vercel 배포·변수 확인 후 재점검 |
| heartbeat 누락/지연, 연결 사전 확인 통과 | clock 정상 cadence 유지, `/fail` | 보호 코드의 수동 engine 실행으로 복구 가능; 승인 없는 과거 replay 금지 |
| Cloudflare exception/CPU 한도/Worker 중단 | ping 미도착 → 독립 check deadline | Cloudflare Observability의 실패와 cron 존재/전파 확인; 승인 후 재개 |
| Healthchecks non-2xx/timeout | cron task 실패, 성공 기록 안 함 | Healthchecks 상태·ping URL·Email 설정과 Cloudflare log 확인; 실제 통보 도착은 미확정 |
| 활성 감시 중 clock 해제 | dispatch 0, `/fail` 유지 | 의도적 중지인지 확인; 임의 성공 ping/pause로 숨기지 않음 |

Worker가 멈췄을 때도 Healthchecks는 Worker와 별도로 누락을 판정한다. Healthchecks 자체가 장애이면
관리자 통보를 보장할 수 없으므로 해당 서비스 상태와 이메일 수신 경로를 확인한다.

### 중지 및 rollback

Worker `RECOVERY_ARMED=false`(활성 감시는 `/fail` 유지) → GitHub 두 운영 workflow Disable → 진행 중 run/provider 상태 확인 후
필요한 코드만 revert한다. 데이터/cursor/history/hold record를 되돌리거나 삭제하지 않는다. 의도적인 유지관리 중에만 승인 후 Healthchecks를 pause하고 `MONITORING_ACTIVE=false`로 바꾼다. 재개 시 위 단위 D/E를 따른다. 구버전은 held
once를 이해하지 못하므로 old engine을 다시 켜지 않는다. 수정본 검토·감사·새 `MIN_SAFE_COMMIT` 설정 후
위 절차로 재가동한다. 기존 commit보다 이전 main 및 보호 파일 변경 rollback은 Worker 테스트에서 차단한다.
공급자 timeout 및 발송 후 DB 기록 실패에는 사용자 확인 없는 resend를 하지 않는다.

## 비용·제약·운영 책임

- Worker 288회/일, 메인 Actions 48회/일, fallback Drain 최대 288회/일. 기존 5분 Drain 수준을 유지한다.
  독립 clock의 새 월 비용 목표는 무료다. Cloudflare Free의 100,000 requests/day, cron CPU 10ms 제한을
  확인했다. cron 1개로 Free 계정 5개 한도 중 1개, invocation당 최대 8개 subrequest로 50개 한도 이내다. cron 전파에 최대 15분이 걸릴 수 있다. 실제 배포 CPU/프로젝트 전체 사용량은 **NOT VERIFIED**이며 Triggers/Observability에서 확인한다.
- Healthchecks Hobbyist는 20 checks/$0. 5분 ping의 최근 100개 로그 보존은 약 8시간이다. email
  통보를 사용하며 유료 SMS/전화는 추가하지 않는다. 계정 휴면·서비스 정책은 운영 시 계속 확인한다. 이번 공식 문서 재확인에서도 20 checks/100 logs이며, 실제 계정 잔여량과 수신 설정은 NOT VERIFIED다.
- public GitHub 표준 runner는 무료이나 artifact/cache, 다른 프로젝트 사용량 및 정책은 별도다.
- heartbeat 48 writes/day, Worker에 의한 health REST 조회 약 288 reads/day + UI. process cache는
  전역 cache가 아니며 Vercel 여러 인스턴스에서 읽기가 더 생길 수 있다. 큐 조회는 기존 pending/processing
  indexed query이고 과거 완료 전체를 scan하지 않는다. 초기 대량 pending 조회 비용은 큐 크기에 비례한다.
- 외부 clock은 GitHub의 scheduled inactivity와 분리되고, dead-man은 Worker와 분리된다. Healthchecks
  자체 장애까지 완벽하게 통보하는 것은 보장하지 않는다. 공급자 상태 페이지와 관리자 email 수신 설정을 확인한다.
- UI 컴포넌트는 390px/1440px, light/dark에서 오버플로/상세 펼치기를 검증했다. 로그인한 운영 브라우저,
  실기기 수신, Vercel 실제 env, 외부 배포/실통보는 승인 전 **NOT VERIFIED**다.
- 기존 npm 설치에서 29개 취약점(12 moderate/16 high/1 critical)이 보고됐다. 알림 수정과 무관한
  framework 일괄 업그레이드는 이 PR에 섞지 않았다. 별도 보안 업데이트가 필요하다.

## 공식 근거 (2026-11-03 확인)

- [GitHub schedule, inactivity, 지연 및 actor](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [workflow 비활성화 및 활성화](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)
- [workflow_dispatch 기본 branch 요건](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Cloudflare Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
- [Cloudflare cron](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
- [Healthchecks 가격/보존 한도](https://healthchecks.io/pricing/)
- [Healthchecks New/Paused 상태](https://healthchecks.io/docs/)
- [Healthchecks pause 후 ping 재개 동작](https://healthchecks.io/docs/configuring_checks/)
- [Cloudflare Dashboard 시작](https://developers.cloudflare.com/workers/get-started/dashboard/)
- [익명 Firestore REST 요청은 Security Rules 적용](https://firebase.google.com/docs/firestore/use-rest-api)
