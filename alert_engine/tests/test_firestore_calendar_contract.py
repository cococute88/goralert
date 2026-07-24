"""Firestore path coverage for the read-only calendar contract adapter."""

from __future__ import annotations

from alert_engine import firestore_client


class Snapshot:
    def __init__(self, doc_id: str, data: dict | None):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data or {})


class DocumentRef:
    def __init__(self, store: dict[tuple[str, ...], dict], path: tuple[str, ...]):
        self.store = store
        self.path = path

    def collection(self, name: str):
        return CollectionRef(self.store, (*self.path, name))

    def get(self):
        return Snapshot(self.path[-1], self.store.get(self.path))


class CollectionRef:
    def __init__(self, store: dict[tuple[str, ...], dict], path: tuple[str, ...]):
        self.store = store
        self.path = path

    def document(self, doc_id: str):
        return DocumentRef(self.store, (*self.path, doc_id))

    def stream(self):
        depth = len(self.path) + 1
        return [
            Snapshot(path[-1], data)
            for path, data in self.store.items()
            if len(path) == depth and path[:-1] == self.path
        ]


class FakeDb:
    def __init__(self, store: dict[tuple[str, ...], dict]):
        self.store = store

    def collection(self, name: str):
        return CollectionRef(self.store, (name,))


def _cache_event():
    return {
        "id": "dividend:TEST:buy:2026-08-10",
        "canonicalEventId": "dividend:TEST:buy:2026-08-10",
        "ticker": "TEST",
        "type": "buy_by",
        "date": "2026-08-10",
        "title": "TEST Deadline",
    }


def test_default_portfolio_reads_cache_and_joins_calendar_events_metadata(monkeypatch):
    store = {
        ("users", "u1", "calendarSettings", "default"): {"activePortfolioId": "default"},
        ("users", "u1", "calendarEvents", "meta-doc"): {
            "canonicalEventId": "dividend:TEST:buy:2026-08-10",
            "heart": True,
        },
        ("users", "u1", "calendarCache", "TEST"): {
            "ticker": "TEST",
            "events": [_cache_event()],
        },
    }
    monkeypatch.setattr(firestore_client, "get_db", lambda: FakeDb(store))

    events = firestore_client.read_calendar_events("u1")

    assert len(events) == 1
    assert events[0]["date"] == "2026-08-10"
    assert events[0]["heart"] is True


def test_named_portfolio_uses_namespaced_cache_metadata_and_custom_paths(monkeypatch):
    base = ("users", "u1", "calendarPortfolios", "income")
    store = {
        ("users", "u1", "calendarSettings", "default"): {"activePortfolioId": "income"},
        (*base, "calendarEventMetas", "meta-doc"): {
            "canonicalEventId": "dividend:TEST:buy:2026-08-10",
            "star": True,
        },
        (*base, "calendarCache", "TEST"): {
            "ticker": "TEST",
            "events": [_cache_event()],
        },
        (*base, "calendarCustomEvents", "custom:test"): {
            "id": "custom:test",
            "date": "2026-08-11",
            "type": "custom",
            "title": "사용자 일정",
        },
        ("users", "u1", "calendarCache", "IGNORED"): {
            "ticker": "IGNORED",
            "events": [{**_cache_event(), "ticker": "IGNORED"}],
        },
    }
    monkeypatch.setattr(firestore_client, "get_db", lambda: FakeDb(store))

    generated = firestore_client.read_calendar_events("u1")
    custom = firestore_client.read_calendar_custom_events("u1")

    assert [(event["ticker"], event["star"]) for event in generated] == [("TEST", True)]
    assert [(event["id"], event["date"], event["type"]) for event in custom] == [
        ("custom:test", "2026-08-11", "custom")
    ]
