import json
from pathlib import Path

from alert_engine.datasource import AlertDataSource
from alert_engine.models import DateEventSelector


TARGETING_FIXTURE = json.loads(
    (
        Path(__file__).parents[2]
        / "tests"
        / "fixtures"
        / "calendar-alert-targeting.json"
    ).read_text(encoding="utf-8")
)


def test_calendar_selector_accepts_multiple_canonical_event_types():
    event = {"type": "ex_div", "title": "SCHD Ex-Dividend Date"}
    assert AlertDataSource._event_matches(event, {"type": ["buy_by", "ex_div"]})
    assert not AlertDataSource._event_matches(event, {"type": ["buy_by", "pay"]})


def test_calendar_selector_keeps_legacy_type_aliases_compatible():
    assert AlertDataSource._event_matches({"type": "buy_by"}, {"type": "buy-deadline"})
    assert AlertDataSource._event_matches({"type": "ex_div"}, {"type": ["ex-dividend"]})


def test_calendar_selector_maps_buy_by_minus_one_to_its_read_only_buy_by_source_event():
    event = {"type": "buy_by", "title": "SCHD 매수 마감일", "star": True}
    assert AlertDataSource._event_matches(event, {"type": "buy_by_minus_1", "titleContains": "매수 마감"})
    assert AlertDataSource._event_has_mark(event, ["star"])
    assert not AlertDataSource._event_matches({"type": "pay"}, {"type": "buy_by_minus_1"})


def test_calendar_selector_title_contains_is_trimmed_and_case_insensitive_for_korean_and_english():
    assert AlertDataSource._event_matches({"title": "삼성전자 실적 발표"}, {"titleContains": " 실적 "})
    assert AlertDataSource._event_matches({"title": "SCHD Ex-Dividend Date"}, {"titleContains": "ex-dividend"})
    assert AlertDataSource._event_matches({"title": "anything"}, {"titleContains": "   "})


def test_shared_ui_engine_targeting_fixture_preserves_generic_and_direct_rule_semantics():
    for fixture_rule in TARGETING_FIXTURE["rules"]:
        selector = DateEventSelector.from_dict(fixture_rule["selector"])
        matched = []
        for event in TARGETING_FIXTURE["events"]:
            source_matches = selector.source == event["source"]
            fields_match = AlertDataSource._event_matches(event, selector.match or {})
            marks = [] if selector.source == "calendarCustomEvents" else selector.markFilter or []
            marks_match = not marks or AlertDataSource._event_has_mark(event, marks)
            if fixture_rule["enabled"] and source_matches and fields_match and marks_match:
                matched.append(event["key"])

        assert matched == fixture_rule["expectedEventKeys"], fixture_rule["id"]


def test_legacy_title_type_compatibility_does_not_disable_real_title_filters():
    event = {
        "type": "buy_by",
        "title": "OMF 매수 마감",
        "heart": True,
    }
    assert AlertDataSource._event_matches(
        event,
        {"type": "buy_by", "titleContains": "buy-deadline"},
    )
    assert not AlertDataSource._event_matches(
        event,
        {"type": "buy_by", "titleContains": "OWL"},
    )
    assert not AlertDataSource._event_matches(
        event,
        {"type": "ex_div", "titleContains": "buy-deadline"},
    )
    assert AlertDataSource._event_matches(
        event,
        {"type": "buy_by_minus_1", "titleContains": "buy-deadline"},
    )
    assert AlertDataSource._event_matches(
        event,
        {"type": ["buy_by", "buy_by_minus_1"], "titleContains": "buy-deadline"},
    )
    assert not AlertDataSource._event_matches(
        event,
        {"titleContains": "buy-deadline"},
    )


def test_direct_selector_passes_its_pinned_portfolio_to_calendar_reader():
    class Reader:
        def __init__(self):
            self.portfolio_id = None

        def read_calendar_events(self, uid, portfolio_id=None):
            self.portfolio_id = portfolio_id
            return []

    reader = Reader()
    selector = DateEventSelector.from_dict({
        "source": "calendarEvents",
        "portfolioId": "income",
        "match": {"eventId": "selected"},
    })

    AlertDataSource(firestore=reader).get_calendar_events("user-1", selector)

    assert reader.portfolio_id == "income"
    assert selector.to_dict()["portfolioId"] == "income"


def test_calendar_selector_matches_direct_event_by_compatible_identity_and_date():
    event = {
        "id": "payload-id",
        "canonicalEventId": "dividend:CAG:buy:2026-07-30",
        "date": "2026-07-30",
        "ticker": "CAG",
        "type": "buy_by",
    }
    assert AlertDataSource._event_matches(
        event,
        {"eventId": "dividend:CAG:buy:2026-07-30", "date": "2026-07-30"},
    )
    assert not AlertDataSource._event_matches(
        event,
        {"eventId": "another-event", "date": "2026-07-30"},
    )
    assert not AlertDataSource._event_matches(
        event,
        {"eventId": "dividend:CAG:buy:2026-07-30", "date": "2026-07-31"},
    )
