"""GORALERT Alert Engine (Sprint 2).

Real Python alert engine that runs on GitHub Actions and reads/writes the SAME
Firestore camelCase shapes the Next.js web app (Sprint 1) persists under
``users/{uid}``. This package REPLACES the conceptual ``MockAlertProvider``.

Design goals:
- Stateless: all durable state lives in Firestore.
- Defensive: a single rule / data-fetch failure never aborts the whole run.
- Market math: Wilder RSI is implemented once in ``alert_engine/rsi.py`` and
  reused by every RSI evaluation path.
- Interop: field names mirror ``lib/alerts/types.ts`` EXACTLY (camelCase).
"""

from .config import ENGINE_VERSION

__all__ = ["ENGINE_VERSION"]
__version__ = ENGINE_VERSION
