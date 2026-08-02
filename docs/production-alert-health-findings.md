# Production alert health findings

조사 기준은 production main `c8aa7030736136013c42c4610a95c4107203a000`과 2026-08-02 자연
scheduled run이다. 이 조사와 회귀 검증에서는 production Firestore write, Telegram/FCM 호출,
workflow dispatch, index/rules 배포를 수행하지 않는다.

## `globalEnabled`

브라우저와 Python worker의 정식 경로는 모두 `users/{uid}/alertSettings/default`이다. Firestore
Rules는 owner의 boolean `globalEnabled` 저장을 허용한다. 저장된 `false`는 양쪽 모델에서 그대로
보존된다.

이전 UI는 문서를 한 번만 읽어 다른 탭의 저장을 반영하지 않았고, 조회 실패 시 기본 ON 화면을
표시했다. worker runner도 설정 조회 예외에서 ON 기본값으로 계속 평가했다. 설정 화면은 문서
listener를 source of truth로 사용하고, write acknowledgement 뒤 listener 값으로 표시하며, 조회
오류에서는 값을 추정하지 않는다. worker는 문서가 없거나 legacy 문서에 boolean 필드가 없을 때만
기존 ON 기본 계약을 유지하고, 설정 조회 자체가 실패하면 해당 사용자의 규칙을 fail-closed로 건너뛴다.

FCM service worker에는 fetch/cache handler가 없으므로 Firestore 설정 응답을 캐시하지 않는다.
동일 필드를 여러 탭에서 저장할 때 Firestore의 last-write-wins는 유지되지만 모든 열린 설정 화면은
listener를 통해 최종 서버 값으로 수렴한다.

## MSFT/SCHD ratio

Alert Engine Run 30739290808의 안전하게 가린 운영 규칙은 `ratio MSFT/SCHD`였다. 분자 provider
symbol `MSFT`에서 yfinance 0.2.40이 `JSONDecodeError` (`Expecting value: line 1 column 1`)를
기록했고, 컬럼은 `Open/High/Low/Close/Adj Close/Volume`이지만 행이 없는 DataFrame을 반환했다.
worker는 이를 `no_data / numerator_symbol_no_data`, provider timestamp `None`으로 분류했다.

동일 Python 3.11과 pandas 2.2.2의 read-only 최소 재현에서 0.2.40은 MSFT와 SCHD 모두 같은
실패를 냈다. yfinance 1.5.2의 download 응답은 두 symbol 모두 125개 daily row와
`Price/Ticker` MultiIndex를 반환했다. 예외를 보존하는 수정된 단일-symbol history 경로에서도 둘 다
`Close`, timezone-aware 최신 timestamp `2026-07-31T04:00:00Z`로 정상 처리됐고 ratio가 계산됐다.
따라서 ticker 또는 ratio 계산 결함이 아니라 구버전 provider client의 결정적 호환성 문제다.

의존성을 1.5.2로 갱신하고 단일-symbol history 호출에서 provider 예외를 보존한다. 파서는
flat/MultiIndex `Close`, `Adj Close` fallback, timezone-aware timestamp와 혼합 숫자 입력을
정규화한다. JSON 응답 파싱 예외는 `provider_error / provider_response_parse_error`이고, provider
오류 기록 없는 진짜 빈 응답만 `no_data / symbol_no_data`이다. 한쪽 leg 실패, timestamp skew,
stale bar와 유한하지 않은 값은 조건 미충족으로 바꾸지 않는다.

## `testPushRequests.status` index

Test Push Run 30738630169에서 uid 없는 자연 cron은 모든 사용자의
`collection_group(testPushRequests).where(status == "pending")`를 실행했다. Firestore는
`testPushRequests.status` COLLECTION_GROUP_ASC single-field index를 요구했다. 기존 fallback은 전체
과거 test request 문서를 읽은 뒤 메모리에서 pending만 골라 사용자 수와 기록 수에 비례하는 read
비용과 latency를 만들었다.

fallback은 Admin SDK 내부에서만 실행되어 client에 다른 사용자의 문서를 노출하지는 않았고,
pending 문자열을 다시 검사해 결과 정확성도 유지했다. 다만 필요 이상의 privileged read 범위와
비용/지연을 만들므로 운영 경로로 유지할 이유가 없다.

`firestore.indexes.json`에 COLLECTION과 COLLECTION_GROUP ascending field override를 선언한다.
인덱스 누락 시 전체 scan을 하지 않고 필요한 manifest 배포를 명시한 오류를 내도록 한다. uid가 있는
즉시 dispatch 경로는 기존처럼 한 사용자의 collection query만 사용한다. 이 PR은 manifest만 바꾸며
production index는 별도 cutover에서 READY 상태를 확인한 뒤 worker보다 먼저 배포한다.

## Durable scheduler 보호

이번 변경은 `nextScheduledAt`, `durableSchedulerVersion`, `schedulerMigration`, `scheduleStatus`,
`lastTriggeredAt`, `lastValue`, rule payload, occurrence/history 쓰기 경로를 수정하지 않는다. 기존
6개 future-only migrated cursor는 저장소 조사와 로컬 테스트에서 읽거나 변경하지 않았다.
