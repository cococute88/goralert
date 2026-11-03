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

외부 Worker는 초기 `RECOVERY_ARMED=false`다. `approved-v1`과 `MIN_SAFE_COMMIT`이 설정돼야
dispatch한다. main이 검토된 commit을 포함하며 보호 대상 engine/workflow 파일이 그 commit 이후
변하지 않았는지 확인한다. 보호 파일 변경, 되돌리기 commit, 비교 결과 누락/300개 이상 파일,
inactive workflow, 인증 오류는 차단·통보한다. 향후 engine 변경도 검토 후 기준 commit을 갱신해야 한다.
UI/문서만 바뀌면 갱신할 필요가 없다. 자동 workflow enable 코드는 없다.

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
6. 독립적인 미래 회차가 없는 once 규칙은 원래 cursor를 유지한 채 보류한다. 반복 발송하지 않는다.
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

1. **사전 감사**: 운영 자격 증명을 안전하게 제공해 위 read-only audit를 실행한다. Firebase Console →
   Firestore Database → Data → `users/{uid}/alertRules`와 `notificationLogs`에서 네 날짜 사례를
   대조한다. 과거 예약을 24시간 기준으로 보류하고 미래 회차를 이어가는 정책에 동의한다.
2. **GitHub**: 저장소 → Pull requests → 이번 PR → Files changed/Checks 검토 → 모든 필수 CI 통과 확인 →
   사용자 승인 후 Merge pull request. main을 재작성하거나 보호 데이터를 삭제하지 않는다.
   Merge 시 Vercel 자동 Production 배포가 연동되어 있을 수 있으므로 merge도 배포 승인에 포함한다.
3. **Firebase**: Console → Firestore Database → Indexes에서 기존 alertRules.enabled 및
   testPushRequests.status COLLECTION_GROUP index가 READY인지 확인한다. Rules → 검토된
   `firestore.rules`의 전체 규칙을 반영 → Publish. 새 집계 get 규칙 외 기존 계정/데이터를 변경하지 않는다.
4. **Vercel**: 프로젝트 → Settings → Environment Variables → Production에서 기존 Firebase public config,
   `GITHUB_DISPATCH_TOKEN` 존재/만료/Actions 권한 및 `GITHUB_REPO`, `GITHUB_WORKFLOW_FILE`,
   `GITHUB_WORKFLOW_REF`를 확인한다. 기본 대상은 위 저장소/Drain/main이다. Secret 값을 출력하지 않는다.
   Deployments → merge commit의 배포 성공 확인. 필요 시 승인 후 Redeploy. 도메인은 유지한다.
5. **독립 통보**: Healthchecks → Add Check → 5분 period + 10분 grace → Integrations에서 관리자 email
   활성화. 이 계정/통보 설정은 사용자가 한다. Ping URL을 공개하지 않는다. 먼저 성공 ping을 보내
   감시를 시작한 뒤 ping을 중단하거나 `/fail`로 **승인된 감시 통보 테스트**를 수행한다.
6. **외부 clock 준비**: Cloudflare → Workers & Pages → Create Worker → `infra/monitor/worker.mjs` 배포.
   Settings → Variables and Secrets에 repo 한정 `GITHUB_ACTIONS_TOKEN`(Actions read/write,
   Contents read)과 `HEALTHCHECK_PING_URL`을 encrypted secret으로 저장한다. Firebase/Telegram
   secret을 복사하지 않는다. `MIN_SAFE_COMMIT`은 merge 후 main의 검토된 전체 SHA로 설정한다.
   `RECOVERY_ARMED=false`를 유지한다. Triggers → Cron → wrangler.toml의 5분 cron 등록.
7. **GitHub 최초 활성화**: Actions → Goralert Alert Engine → Enable workflow. 이어서 Goralert Test Push
   Drain → Enable workflow. Keepalive/Diagnostics 활성화는 필요 없다. 기존 실행 큐가 있다면 inspect한 뒤
   실발송 승인 범위 안에서 처리한다. 엔진은 dispatch-only이므로 이 이후 실행 요청이 있어야 실행된다.
8. **승인된 최초 실행**: Alert Engine → Run workflow → main, all, dry-run=true로 확인 →
   보호·감사 결과 확인 → 별도 실발송/운영 쓰기 승인 후 dry-run=false로 1회 실행. 보류 기록과
   미래 cursor, 정상 heartbeat를 확인한다. 이 단계는 최근 due/현재 가격 조건을 실제 발송할 수 있다.
9. **실제 채널 검증**: 사용자 승인 후 앱 설정 → 테스트 Push → 기기 foreground/background 수신 확인 →
   테스트 Telegram → 수신 확인 → 둘 다 테스트 → 각 채널 영속 결과/기록 확인. 실발송 테스트의
   성공은 이번 로컬 회귀 PASS와 별개다. 두 Android 브라우저 검증은 android-push-verification.md 참조.
10. **장기 자동 실행 시작**: Cloudflare Worker Settings → `RECOVERY_ARMED=approved-v1` 저장.
    :03/:33 UTC 메인 실행과 매 5분 Drain을 확인한다. 앱 엔진 상태와 Healthchecks 상태가 정상인지 확인한다.
    한 번의 HTTP 204를 전달 성공으로 승인하지 않는다. disabled workflow에는 자동 재활성화가 없다.

### 중지 및 rollback

Worker `RECOVERY_ARMED=false` → GitHub 두 운영 workflow Disable → 진행 중 run/provider 상태 확인 후
필요한 코드만 revert한다. 데이터/cursor/history/hold record를 되돌리거나 삭제하지 않는다. 구버전은 held
once를 이해하지 못하므로 old engine을 다시 켜지 않는다. 수정본 검토·감사·새 `MIN_SAFE_COMMIT` 설정 후
위 절차로 재가동한다. 기존 commit보다 이전 main 및 보호 파일 변경 rollback은 Worker 테스트에서 차단한다.
공급자 timeout 및 발송 후 DB 기록 실패에는 사용자 확인 없는 resend를 하지 않는다.

## 비용·제약·운영 책임

- Worker 288회/일, 메인 Actions 48회/일, fallback Drain 최대 288회/일. 기존 5분 Drain 수준을 유지한다.
  독립 clock의 새 월 비용 목표는 무료다. Cloudflare Free의 100,000 requests/day, cron CPU 10ms 제한을
  확인했다. 실제 배포 CPU/프로젝트 전체 사용량은 **NOT VERIFIED**이며 Triggers/Observability에서 확인한다.
- Healthchecks Hobbyist는 20 checks/$0. 5분 ping의 최근 100개 로그 보존은 약 8시간이다. email
  통보를 사용하며 유료 SMS/전화는 추가하지 않는다. 계정 휴면·서비스 정책은 운영 시 계속 확인한다.
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
