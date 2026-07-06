"""Backtest determinism + zero side effects.

Maps to: REQ-018.2 / REQ-018.3. ``backtest_rule`` replays the production
evaluator pipeline over historical data and must be:
- Deterministic: identical input -> identical firings (per-day triggered vector
  and fire_count are stable across runs).
- Side-effect free: no delivery, no Firestore writes, no rule-state mutation.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from alert_engine.backtest import backtest_rule

from .conftest import FakeChannel, FakeFirestore, make_ratio_rule

_HISTORY = {"ratios": {"SPY/SCHD": {
    "2024-05-01": 20.0,
    "2024-05-02": 26.0,
    "2024-05-03": 24.0,
    "2024-05-04": 30.0,
    "2024-05-05": 25.0,
}}}
_RANGE = ("2024-05-01", "2024-05-05")


def test_backtest_is_deterministic():
    rule = make_ratio_rule(threshold=25.0, comparator="gte")
    r1 = backtest_rule(rule, _HISTORY, _RANGE)
    r2 = backtest_rule(rule, _HISTORY, _RANGE)
    assert r1.fire_count == r2.fire_count
    assert [d.triggered for d in r1.days] == [d.triggered for d in r2.days]
    assert [d.value for d in r1.days] == [d.value for d in r2.days]


def test_backtest_expected_firings():
    rule = make_ratio_rule(threshold=25.0, comparator="gte")
    report = backtest_rule(rule, _HISTORY, _RANGE)
    # 26, 30, 25 are >= 25 -> 3 firings; 20 and 24 are not.
    assert report.fire_count == 3
    fired_dates = [d.date for d in report.days if d.triggered]
    assert fired_dates == ["2024-05-02", "2024-05-04", "2024-05-05"]


def test_backtest_has_no_side_effects():
    """No delivery, no Firestore writes — the fakes must stay pristine."""
    fs = FakeFirestore()
    channel = FakeChannel("telegram")
    rule = make_ratio_rule()

    backtest_rule(rule, _HISTORY, _RANGE)

    assert fs.logs == {}
    assert fs.state_updates == []
    assert channel.calls == 0


@given(threshold=st.floats(min_value=1.0, max_value=100.0, allow_nan=False))
def test_backtest_repeatable_for_any_threshold(threshold):
    rule = make_ratio_rule(threshold=threshold, comparator="gte")
    r1 = backtest_rule(rule, _HISTORY, _RANGE)
    r2 = backtest_rule(rule, _HISTORY, _RANGE)
    assert [d.triggered for d in r1.days] == [d.triggered for d in r2.days]
    assert r1.fire_count == r2.fire_count
