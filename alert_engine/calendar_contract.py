"""Read-only adapter for the Gorani Finance calendar Firestore contract.

Gorani stores generated event bodies in per-ticker ``calendarCache`` documents
and stores star/heart/memo metadata separately.  Legacy imports are the one
exception: their event body and metadata can coexist in ``calendarEvents``.

This module contains only pure normalization/join logic.  It never fabricates
dates or event types and never writes to either calendar collection.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set


DEFAULT_CALENDAR_PORTFOLIO_ID = "default"

_EVENT_TYPE_ALIASES = {
    "buy": "buy_by",
    "buyby": "buy_by",
    "buy-by": "buy_by",
    "buy_deadline": "buy_by",
    "buy-deadline": "buy_by",
    "exdiv": "ex_div",
    "ex-div": "ex_div",
    "ex-dividend": "ex_div",
    "ex_dividend": "ex_div",
    "payment": "pay",
}

_IDENTITY_EVENT_TYPE_ALIASES = {
    "buy_by": "buy",
    "pay": "payment",
}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalize_calendar_event_type(value: Any) -> str:
    """Normalize persisted/UI aliases to the event-body codes Goralert uses."""
    raw = _text(value).lower().replace(" ", "_")
    return _EVENT_TYPE_ALIASES.get(raw, raw)


def _event_date(event: Dict[str, Any]) -> str:
    raw = _text(event.get("date")) or _text(event.get("eventDate"))
    return raw[:10]


def _event_type(event: Dict[str, Any]) -> str:
    return normalize_calendar_event_type(event.get("type") or event.get("eventType"))


def _event_ticker(event: Dict[str, Any], fallback_ticker: str = "") -> str:
    return (_text(event.get("ticker")) or fallback_ticker).upper()


def canonical_generated_event_id(event: Dict[str, Any], fallback_ticker: str = "") -> Optional[str]:
    """Reproduce Gorani's generated-event identity contract when possible."""
    ticker = _event_ticker(event, fallback_ticker)
    event_type = _event_type(event)
    event_date = _event_date(event)
    if not ticker or not event_type or not event_date:
        return None
    identity_type = _IDENTITY_EVENT_TYPE_ALIASES.get(event_type, event_type)
    return f"dividend:{ticker}:{identity_type}:{event_date}"


def calendar_event_identity_keys(
    event: Dict[str, Any],
    fallback_id: str = "",
    fallback_ticker: str = "",
) -> Set[str]:
    """Return all official/current and legacy IDs that may identify an event."""
    keys = {
        _text(event.get("id")),
        _text(event.get("eventId")),
        _text(event.get("canonicalEventId")),
        _text(event.get("legacyEventId")),
        _text(event.get("firestoreDocumentId")),
        fallback_id,
    }
    canonical = canonical_generated_event_id(event, fallback_ticker)
    if canonical:
        keys.add(canonical)

    ticker = _event_ticker(event, fallback_ticker)
    event_type = _event_type(event)
    event_date = _event_date(event)
    if ticker and event_type and event_date:
        keys.add(f"{ticker}-{event_type}-{event_date}")
        legacy_type = _IDENTITY_EVENT_TYPE_ALIASES.get(event_type, event_type)
        keys.add(f"{ticker}-{legacy_type}-{event_date}")
    return {key for key in keys if key}


def normalize_authoritative_event(
    event: Dict[str, Any],
    fallback_id: str = "",
    fallback_ticker: str = "",
) -> Optional[Dict[str, Any]]:
    """Normalize a real event body, rejecting metadata-only documents."""
    source_kind = _text(event.get("sourceKind")).lower()
    source = _text(event.get("source")).lower()
    if source_kind == "sample" or source in {"sample", "mock"}:
        return None
    event_date = _event_date(event)
    event_type = _event_type(event)
    if not event_date or not event_type:
        return None

    normalized = dict(event)
    event_id = (
        _text(event.get("id"))
        or _text(event.get("canonicalEventId"))
        or _text(event.get("eventId"))
        or fallback_id
        or canonical_generated_event_id(event, fallback_ticker)
        or ""
    )
    normalized["id"] = event_id
    normalized["date"] = event_date
    normalized["type"] = event_type
    ticker = _event_ticker(event, fallback_ticker)
    if ticker:
        normalized["ticker"] = ticker
    return normalized


def _dedupe_events(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for event in events:
        keys = calendar_event_identity_keys(event)
        identity = (
            canonical_generated_event_id(event)
            or _text(event.get("canonicalEventId"))
            or _text(event.get("id"))
            or "|".join(sorted(keys))
        )
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        result.append(event)
    return result


def select_authoritative_events(
    cache_events: Iterable[Dict[str, Any]],
    legacy_events: Iterable[Dict[str, Any]],
    cache_tickers: Iterable[str] = (),
) -> List[Dict[str, Any]]:
    """Mirror Gorani's policy: a saved ticker cache supersedes legacy rows.

    ``cache_tickers`` represents cache *document existence*, independently of
    whether a document currently contains any events.  An empty saved cache is
    authoritative for its ticker and must suppress stale legacy rows.
    """
    normalized_cache = [
        normalized
        for event in cache_events
        if (normalized := normalize_authoritative_event(event)) is not None
    ]
    cache_tickers = {
        _text(ticker).upper()
        for ticker in cache_tickers
        if _text(ticker)
    } | {
        _event_ticker(event)
        for event in normalized_cache
        if _event_ticker(event)
    }
    preserved_legacy = [
        normalized
        for event in legacy_events
        if (normalized := normalize_authoritative_event(event)) is not None
        and _event_ticker(normalized) not in cache_tickers
    ]
    return _dedupe_events([*normalized_cache, *preserved_legacy])


def join_calendar_metadata(
    authoritative_events: Iterable[Dict[str, Any]],
    metadata_docs: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Join star/heart/memo metadata using canonical and compatible IDs."""
    metadata = list(metadata_docs)
    lookup: Dict[str, List[int]] = {}
    for index, meta in enumerate(metadata):
        for key in calendar_event_identity_keys(meta):
            lookup.setdefault(key, []).append(index)

    joined: List[Dict[str, Any]] = []
    for raw_event in authoritative_events:
        event = normalize_authoritative_event(raw_event)
        if event is None:
            continue
        meta_indexes: Set[int] = set()
        for key in calendar_event_identity_keys(event):
            meta_indexes.update(lookup.get(key, []))
        matches = [metadata[index] for index in sorted(meta_indexes)]

        combined = dict(event)
        combined["star"] = bool(event.get("star")) or any(bool(meta.get("star")) for meta in matches)
        combined["heart"] = bool(event.get("heart")) or any(bool(meta.get("heart")) for meta in matches)
        memo = next(
            (_text(meta.get("memo")) for meta in matches if _text(meta.get("memo"))),
            _text(event.get("memo")) or _text(event.get("note")),
        )
        if memo:
            combined["memo"] = memo
        joined.append(combined)
    return _dedupe_events(joined)


def resolve_calendar_events(
    cache_events: Iterable[Dict[str, Any]],
    legacy_events: Iterable[Dict[str, Any]],
    metadata_docs: Iterable[Dict[str, Any]],
    cache_tickers: Iterable[str] = (),
) -> List[Dict[str, Any]]:
    """Resolve generated/legacy bodies and attach their read-only metadata."""
    authoritative = select_authoritative_events(cache_events, legacy_events, cache_tickers)
    return join_calendar_metadata(authoritative, metadata_docs)
