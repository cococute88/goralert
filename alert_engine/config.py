"""Environment / configuration loading for the alert engine.

Everything is OPTIONAL at import time — importing this module never raises even
when secrets are absent. Validation happens lazily, only when a value is
actually used (e.g. when initializing Firestore or sending Telegram).

Recognized environment variables
---------------------------------
- FIREBASE_SERVICE_ACCOUNT          full service-account JSON as a string
- FIREBASE_SERVICE_ACCOUNT_KEY      alias (raw JSON or base64) — reused from the
                                    Next.js app's server env if present
- GOOGLE_APPLICATION_CREDENTIALS    path to a service-account JSON file
- TELEGRAM_BOT_TOKEN                Telegram Bot API token
- DEFAULT_TZ                        default IANA tz (default "Asia/Seoul")
- FCM                               uses the SAME service account via firebase-admin

NOTE: FCM (push) uses the same firebase-admin credential as Firestore, so there
is no separate FCM server key to configure.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Optional

# Bumped whenever evaluation/delivery semantics change. Stamped onto rules and
# notification logs so history + backtests are reproducible.
ENGINE_VERSION = "2.0.0"

# Default timezone for recurrence/quiet-hours interpretation (US requirement).
DEFAULT_TZ = os.environ.get("DEFAULT_TZ", "Asia/Seoul")

# Delivery retry/backoff knobs (overridable via env for ops tuning).
DELIVERY_MAX_RETRIES = int(os.environ.get("ALERT_DELIVERY_MAX_RETRIES", "3"))
DELIVERY_BACKOFF_BASE_SECONDS = float(os.environ.get("ALERT_DELIVERY_BACKOFF_BASE", "1.0"))
DELIVERY_BACKOFF_MAX_SECONDS = float(os.environ.get("ALERT_DELIVERY_BACKOFF_MAX", "30.0"))

# Evaluation window (minutes) used to bucket eventIds and decide "due now".
# MUST be >= the cron cadence in .github/workflows/alert-engine.yml (currently
# */30) so consecutive runs' due-windows TILE the timeline with no gap and a
# delayed/skipped run is caught up by the next run. A smaller window than the
# cadence can MISS scheduled alerts whose time falls in the gap. Scheduled rules
# bucket by occurrence time, so a larger window never causes duplicate sends.
DEFAULT_EVAL_WINDOW_MINUTES = int(os.environ.get("ALERT_EVAL_WINDOW_MINUTES", "30"))


def _read_service_account_raw() -> Optional[str]:
    """Return the raw service-account JSON string from env, if provided.

    Accepts either the dedicated FIREBASE_SERVICE_ACCOUNT var or the Next.js
    app's FIREBASE_SERVICE_ACCOUNT_KEY (which may be base64-encoded).
    """
    for key in ("FIREBASE_SERVICE_ACCOUNT", "FIREBASE_SERVICE_ACCOUNT_KEY"):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    return None


def load_service_account_dict() -> Optional[dict]:
    """Parse the service-account credential into a dict, or return None.

    Tolerates both raw JSON and base64-encoded JSON. Returns None when no
    inline credential is configured (caller may then fall back to
    GOOGLE_APPLICATION_CREDENTIALS).
    """
    raw = _read_service_account_raw()
    if not raw:
        return None

    # Try raw JSON first.
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass

    # Then try base64-encoded JSON.
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        return json.loads(decoded)
    except Exception:  # noqa: BLE001 - any decode failure => treat as not provided
        return None


@dataclass(frozen=True)
class EngineConfig:
    """Snapshot of engine configuration resolved from the environment."""

    engine_version: str = ENGINE_VERSION
    default_tz: str = DEFAULT_TZ
    telegram_bot_token: Optional[str] = None
    google_application_credentials: Optional[str] = None
    has_inline_service_account: bool = False
    delivery_max_retries: int = DELIVERY_MAX_RETRIES
    delivery_backoff_base_seconds: float = DELIVERY_BACKOFF_BASE_SECONDS
    delivery_backoff_max_seconds: float = DELIVERY_BACKOFF_MAX_SECONDS
    eval_window_minutes: int = DEFAULT_EVAL_WINDOW_MINUTES

    @property
    def has_firebase_credentials(self) -> bool:
        return self.has_inline_service_account or bool(self.google_application_credentials)


def load_config() -> EngineConfig:
    """Resolve the current environment into an EngineConfig (never raises)."""
    return EngineConfig(
        engine_version=ENGINE_VERSION,
        default_tz=os.environ.get("DEFAULT_TZ", "Asia/Seoul"),
        telegram_bot_token=(os.environ.get("TELEGRAM_BOT_TOKEN") or None),
        google_application_credentials=(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or None),
        has_inline_service_account=_read_service_account_raw() is not None,
        delivery_max_retries=DELIVERY_MAX_RETRIES,
        delivery_backoff_base_seconds=DELIVERY_BACKOFF_BASE_SECONDS,
        delivery_backoff_max_seconds=DELIVERY_BACKOFF_MAX_SECONDS,
        eval_window_minutes=DEFAULT_EVAL_WINDOW_MINUTES,
    )
