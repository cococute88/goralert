from alert_engine.datasource import AlertDataSource


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
