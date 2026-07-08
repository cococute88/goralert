"""Engine entrypoint — run on GitHub Actions (or locally).

Usage::

    python -m alert_engine.main --job-scope market [--uid UID] [--dry-run]

Job scopes split the cron workload so each schedule only loads the rule kinds it
needs. Overlapping scopes are safe because of reserve-before-send idempotency:
before delivering, the engine atomically creates ``notificationLogs/{eventId}``
(``create()`` fails if it already exists), so a fired event is reserved by
exactly one run and never double-sent regardless of which job processed it:

- daily    : schedule/calendar-driven rules (date, dividend, custom, composite)
- market   : market-data threshold rules (rsi, vix, price, fx, gold, bitcoin,
             koreanEtf, ratio, custom, composite)
- calendar : calendar-event-driven rules (date, dividend)

``custom``/``composite`` appear in multiple scopes because they may wrap any
kind; reserve-before-send idempotency prevents double-firing.

The run is resilient: one rule's failure is caught and logged, never aborting
the run. ``globalEnabled=false`` for a user skips all of that user's rules.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .config import load_config
from .engine import AlertEngine, STATUS_DELIVERED, STATUS_ERROR
from .models import AlertRule, AlertSettings

logger = logging.getLogger("alert_engine.main")

JOB_SCOPE_KINDS: Dict[str, set] = {
    "daily": {"date", "dividend", "custom", "composite"},
    "market": {"rsi", "vix", "price", "fx", "gold", "bitcoin", "koreanEtf", "ratio", "custom", "composite"},
    "calendar": {"date", "dividend"},
    "all": set(),  # empty -> no filtering
}


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        stream=sys.stdout,
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="alert_engine", description="GORALERT alert engine")
    parser.add_argument(
        "--job-scope", default="all", choices=sorted(JOB_SCOPE_KINDS.keys()),
        help="which rule kinds to process this run (default: all)",
    )
    parser.add_argument("--uid", default=None, help="restrict to a single user (default: all users)")
    parser.add_argument(
        "--test-push", action="store_true",
        help="drain pending users/{uid}/testPushRequests via the production delivery path "
             "(send_test_alert -> PushChannel) instead of processing rules",
    )
    parser.add_argument("--dry-run", action="store_true", help="evaluate + log decisions but do not deliver or write")
    parser.add_argument("--window-minutes", type=int, default=None, help="override evaluation window")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def _filter_by_scope(rules: List[AlertRule], scope: str) -> List[AlertRule]:
    kinds = JOB_SCOPE_KINDS.get(scope, set())
    if not kinds:
        return rules
    return [r for r in rules if r.kind in kinds]


def run(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.verbose)
    cfg = load_config()
    if args.window_minutes is not None:
        # EngineConfig is frozen; pass the override through the engine instead.
        object.__setattr__(cfg, "eval_window_minutes", args.window_minutes)

    started = datetime.now(timezone.utc)
    logger.info(
        "engine start :: version=%s scope=%s uid=%s dry_run=%s tz=%s",
        cfg.engine_version, args.job_scope, args.uid or "<all>", args.dry_run, cfg.default_tz,
    )

    # Lazy import so --help works without firebase-admin installed.
    from . import firestore_client

    if not cfg.has_firebase_credentials:
        logger.error("No Firebase credentials configured (FIREBASE_SERVICE_ACCOUNT or GOOGLE_APPLICATION_CREDENTIALS). Aborting.")
        return 2

    # Test-push mode: drain the browser-enqueued testPushRequests queue through
    # the SAME production delivery path (send_test_alert -> deliver -> PushChannel).
    # This is what the web "테스트 Push/Telegram" buttons trigger, so test and
    # production share one send implementation.
    if args.test_push:
        from . import test_push
        counts = test_push.process_test_requests(args.uid, dry_run=args.dry_run)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        logger.info("engine done (test-push) :: %s elapsed=%.2fs", counts, elapsed)
        return 0

    try:
        rules = firestore_client.list_enabled_rules(args.uid)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to list rules: %s", exc)
        return 3

    rules = _filter_by_scope(rules, args.job_scope)
    logger.info("loaded %d enabled rule(s) in scope=%s", len(rules), args.job_scope)

    engine = AlertEngine(config=cfg, firestore=firestore_client)

    # Per-user settings cache + globalEnabled gate.
    settings_cache: Dict[str, AlertSettings] = {}
    by_uid: Dict[str, List[AlertRule]] = defaultdict(list)
    for rule in rules:
        by_uid[rule.uid].append(rule)

    status_counts: Counter = Counter()
    delivered = 0
    errors = 0

    for uid, uid_rules in by_uid.items():
        try:
            settings = firestore_client.load_alert_settings(uid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("settings load failed for uid=%s (%s); using defaults", uid, exc)
            settings = AlertSettings()
        settings_cache[uid] = settings

        if not settings.globalEnabled:
            logger.info("uid=%s globalEnabled=false -> skipping %d rule(s)", uid, len(uid_rules))
            status_counts["skipped_global_disabled"] += len(uid_rules)
            continue

        for rule in uid_rules:
            try:
                result = engine.process_rule(rule, now=started, settings=settings, dry_run=args.dry_run)
                status_counts[result.status] += 1
                if result.status == STATUS_DELIVERED:
                    delivered += 1
                if result.status == STATUS_ERROR:
                    errors += 1
                logger.info(
                    "rule=%s uid=%s -> %s%s",
                    rule.id, uid, result.status,
                    f" ({result.detail})" if result.detail else "",
                )
            except Exception as exc:  # noqa: BLE001 - isolation: never abort the run
                errors += 1
                status_counts[STATUS_ERROR] += 1
                logger.exception("rule=%s uid=%s crashed: %s", rule.id, uid, exc)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    logger.info(
        "engine done :: processed=%d delivered=%d errors=%d elapsed=%.2fs breakdown=%s",
        len(rules), delivered, errors, elapsed, dict(status_counts),
    )
    # Non-zero exit only on hard infra errors, not on individual rule failures.
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
