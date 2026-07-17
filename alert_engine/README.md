# GORALERT Alert Engine (Python)

The **real** alert engine for GORALERT. It evaluates alert rules, gates them
(recurrence / quiet-hours / cooldown / idempotency), fans out delivery to
Telegram + push, and persists exactly one `NotificationLog` per fired event.

It runs as a scheduled **GitHub Actions** job (see
`.github/workflows/alert-engine.yml`) and reads/writes **Firestore** as the
single source of truth. The Next.js web app and this engine interoperate on the
**same** Firestore documents (camelCase shapes mirror `lib/alerts/types.ts`).

---

## Architecture

```
GitHub Actions cron ──> python -m alert_engine.main --job-scope <scope>
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ AlertEngine.process_rule (per rule)           │
        │  1. enabled + settings.globalEnabled gate     │
        │  2. recurrence "due now" gate                 │
        │  3. evaluate condition (evaluator registry)   │
        │  4. not triggered -> NO write (cross=lastValue)│
        │  5. quiet-hours gate                          │
        │  6. cooldown gate (lastTriggeredAt)           │
        │  7. reserve-before-send (atomic eventId create)│
        │  8. render + fan-out delivery (isolated)      │
        │  9. finalize EXACTLY ONE NotificationLog      │
        │ 10. update rule state; once -> disable        │
        └─────────────────────────────────────────────┘
                              │
                              ▼
                Firestore (users/{uid}/...)  ← single SoT
```

Key design properties:

- **Firestore is the single source of truth.** The engine is **stateless** —
  all durable state (lastTriggeredAt, lastValue, enabled, logs) lives in
  Firestore. Any run can be dropped/replayed safely.
- **Idempotent.** Every fire maps to a stable `eventId = ruleId:bucketTime`. A
  `NotificationLog` keyed by that id means the same event is never re-sent or
  re-logged, even across overlapping cron runs / job scopes.
- **Delivery isolation.** One channel failing (or raising) never blocks the
  others; the engine always writes exactly one log with one result per channel.
- **Calendar is read-only.** Evaluation may READ calendar collections but never
  writes to them.
- **Dependency-injected.** `datasource`, evaluator/channel registries, and the
  Firestore client are all injectable, which is how the test suite runs fully
  offline with in-memory fakes.

### Module map

| Module | Responsibility |
| --- | --- |
| `engine.py` | `AlertEngine.process_rule` / `send_test_alert` pipeline |
| `evaluators/` | one evaluator per condition `kind` (metric/ratio/dividend/date/composite/custom) |
| `channels/` | `telegram`, `push` delivery channels |
| `compare.py` | comparator semantics (gt/gte/lt/lte/eq/crossUp/crossDown) |
| `recurrence.py` | "due now", `next_occurrence`, `bucket_time` (Asia/Seoul) |
| `delivery.py` | fan-out with retry/backoff + isolation |
| `event.py` | eventId, message rendering, event building |
| `backtest.py` | deterministic, side-effect-free historical replay |
| `datasource.py` | market + calendar reads (**reuses** `original/logic/market.py`) |
| `firestore_client.py` | the only module that talks to Firestore |
| `models.py` | dataclasses mirroring the TS `lib/alerts/types.ts` shapes |
| `config.py` / `main.py` | env/config + CLI entrypoint |

### Reuse of `original/logic/market.py`

RSI is **not** re-implemented. `datasource.py` imports
`original.logic.market.compute_rsi` (Wilder method, pandas-only) and feeds it a
yfinance close series. Drawdown/MDD helpers from the same module are available
for future conditions. This is verified by `tests/test_rsi_reuse.py`, which
asserts the metric path returns exactly `market.compute_rsi(...).iloc[-1]`.

---

## Run locally

From the repo root:

```bash
# install deps (firebase-admin, yfinance, pandas, requests + test tooling)
pip install -r requirements.txt

# dry-run: evaluate + log decisions, but DO NOT deliver or write to Firestore
python -m alert_engine.main --dry-run

# scope the workload (default "all")
python -m alert_engine.main --job-scope market --dry-run
python -m alert_engine.main --job-scope daily  --uid <UID> --dry-run
```

`--dry-run` still requires Firebase credentials to *read* rules. To explore the
pipeline with zero credentials, run the test suite (it uses fakes).

### Job scopes

| Scope | Rule kinds processed |
| --- | --- |
| `daily` | date, dividend, custom, composite |
| `market` | rsi, vix, price, fx, gold, bitcoin, koreanEtf, ratio, custom, composite |
| `calendar` | date, dividend |
| `all` | (no filter) |

`custom`/`composite` appear in multiple scopes because they may wrap any kind;
eventId idempotency prevents double-firing across overlapping scopes.

### Test-push drain (single source of truth)

The web "테스트 Push / Telegram" buttons do **not** deliver in the browser. They
enqueue a request at `users/{uid}/testPushRequests/{id}`; the engine drains it
through the **same** production delivery path as scheduled alerts:

```
python -m alert_engine.main --test-push [--uid UID]
  -> test_push.process_test_requests
    -> AlertEngine.send_test_alert
      -> deliver()                    # retry / backoff / isolation
        -> channels["push"]  == PushChannel   (build_default_channels)
          -> messaging.send_each_for_multicast -> FCM
```

There is exactly one push implementation (`channels/push.py`); test and
production share it, so a change to `PushChannel` changes both.

Trigger: the web button enqueues the request, then calls a thin server bridge
(`app/api/test-push`) that fires `alert-test-push.yml` via **workflow_dispatch**
immediately (scoped to that `uid`) — no waiting for cron. The bridge holds no
push logic; it only dispatches. The 5-min cron on the same workflow is a
fallback for requests whose immediate dispatch failed. Production alerts keep
their own untouched cron in `alert-engine.yml`. Verified by
`tests/test_test_push_drain.py`.

---

## Run tests

The suite is **offline** — no network, no firebase-admin init, no live keys.

```bash
pytest alert_engine/tests          # full suite
pytest alert_engine/tests -q       # quiet (CI default)
```

CI runs the same command on every push/PR via
`.github/workflows/engine-tests.yml`. Property-based tests use
[hypothesis](https://hypothesis.readthedocs.io/). The RSI reuse test
`importorskip`s pandas, so it is skipped automatically when pandas is absent.

Tests are mapped to the design's correctness properties:

| Test file | Property |
| --- | --- |
| `test_compare_boundary.py` | P5 comparator boundary consistency |
| `test_idempotency.py` | P1 cooldown idempotency + P9 eventId idempotency |
| `test_delivery_isolation.py` | P2 delivery isolation + P3 log preservation |
| `test_once_termination.py` | P7 once-mode termination |
| `test_test_alert_noop.py` | P8 test send has no side effects |
| `test_calendar_readonly.py` | P6 calendar read-only |
| `test_composite_monotonicity.py` | P4 AND/OR monotonicity |
| `test_backtest_determinism.py` | backtest determinism + no side effects |
| `test_recurrence.py` | recurrence cadence + eventId determinism |
| `test_rsi_reuse.py` | reuse of `original/logic/market.compute_rsi` |
| `test_regression.py` | recurrence stability, import surface, global kill-switch |

---

## Environment variables

| Var | Purpose | Required |
| --- | --- | --- |
| `FIREBASE_SERVICE_ACCOUNT` | service-account JSON (or base64) for Firestore + FCM | yes (engine) |
| `FIREBASE_SERVICE_ACCOUNT_KEY` | alias accepted from the Next.js server env | alt |
| `GOOGLE_APPLICATION_CREDENTIALS` | path to a service-account JSON file | alt |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token | for Telegram |
| `DEFAULT_TZ` | default IANA tz (default `Asia/Seoul`) | no |
| `ALERT_EVAL_WINDOW_MINUTES` | eval window for due/bucket (default 30; keep >= cron cadence) | no |
| `ALERT_DELIVERY_MAX_RETRIES` | per-channel retries (default 3) | no |
| `ALERT_DELIVERY_BACKOFF_BASE` / `ALERT_DELIVERY_BACKOFF_MAX` | backoff seconds | no |

> FCM push uses the **same** firebase-admin credential as Firestore — there is
> no separate FCM server key to configure.

Importing the package never raises when secrets are absent; credentials are
validated lazily, only when a value is actually used.

---

## "실제 키만 넣으면 동작" checklist

The engine + tests are complete; only real secrets/data are outstanding:

- [ ] **`FIREBASE_SERVICE_ACCOUNT`** — add as a GitHub Actions secret (Settings
      → Secrets and variables → Actions). Enables Firestore reads/writes + FCM.
- [ ] **`TELEGRAM_BOT_TOKEN`** — add as a GitHub Actions secret to enable
      Telegram delivery. Each user's `telegramChatId` is set via the web app
      (`alertSettings`).
- [ ] **Push devices** — real FCM client registration in the PWA writes
      `alertSettings.pushDevices` and the compatible `pushTokens` array. The
      engine merges both sources and de-duplicates tokens; until registration
      succeeds, push fails gracefully.
- [ ] **Firestore index** — composite/`collection_group` query on `alertRules`
      `enabled == true` (already declared in `firestore.indexes.json`; deploy it
      with `firebase deploy --only firestore:indexes`).
- [ ] Verify cron cadence in `.github/workflows/alert-engine.yml` (UTC; KST = UTC+9).

With those in place the scheduled workflow processes live rules end to end. No
code changes are required — the engine reads everything from Firestore.
