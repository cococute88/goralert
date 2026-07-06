"""Read-only, deterministic backtesting of a rule over historical data.

``backtest_rule`` replays the SAME evaluator pipeline used in production against
a supplied ``history`` (no network, no Firestore, no delivery — zero side
effects). It carries ``prev_value`` forward between days so crossUp/crossDown
comparators behave exactly as they would live.

``history`` shape (all keys optional)::

    {
      "metrics":  { "<metric-key>": { "YYYY-MM-DD": value, ... } },
      "ratios":   { "NUM/DEN":      { "YYYY-MM-DD": value, ... } },
      "dividends":{ "TICKER":       { "YYYY-MM-DD": value, ... } },
      "calendar": { "YYYY-MM-DD": [ {event}, ... ] },
    }

metric-key is derived by ``HistoricalDataSource.metric_key`` (e.g.
"rsi:KOSPI:14", "vix", "price:SPY", "fx:USDKRW", "gold", "bitcoin",
"koreanEtf:069500").

Reproducibility: results echo the rule's ``ruleVersion``/``engineVersion`` so a
recorded backtest can be tied to the exact evaluation semantics that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .config import ENGINE_VERSION
from .evaluators import build_default_registry
from .evaluators.base import EvalContext
from .models import AlertRule, DateEventSelector, MetricId


class HistoricalDataSource:
    """Deterministic data source backed by a static ``history`` mapping.

    ``set_current_date`` selects which day's values are returned. Lookups fall
    back to the most recent prior date present in the series (forward-fill), so
    sparse history still resolves a value.
    """

    def __init__(self, history: Optional[Dict[str, Any]] = None):
        self._history = history or {}
        self._current: Optional[date] = None

    # --- harness ----
    def set_current_date(self, d: date) -> None:
        self._current = d

    def now(self) -> datetime:
        if self._current is None:
            return datetime.now(timezone.utc)
        return datetime(self._current.year, self._current.month, self._current.day, 12, 0, tzinfo=timezone.utc)

    # --- key helpers ----
    @staticmethod
    def metric_key(metric: MetricId) -> str:
        if metric.metric == "rsi":
            return f"rsi:{metric.ticker}:{metric.period}"
        if metric.metric == "price":
            return f"price:{metric.ticker}"
        if metric.metric == "fx":
            return f"fx:{metric.pair}"
        if metric.metric == "koreanEtf":
            return f"koreanEtf:{metric.code}"
        return metric.metric  # vix / gold / bitcoin

    def _ffill_lookup(self, series: Dict[str, Any]) -> Optional[float]:
        if not series or self._current is None:
            return None
        cur_iso = self._current.isoformat()
        # Exact match first, else most recent prior date.
        if cur_iso in series:
            return _coerce(series[cur_iso])
        prior = [d for d in series.keys() if d <= cur_iso]
        if not prior:
            return None
        return _coerce(series[max(prior)])

    # --- AlertDataSource-compatible surface ----
    def get_metric(self, metric: Optional[MetricId]) -> Optional[float]:
        if metric is None:
            return None
        return self._ffill_lookup(self._history.get("metrics", {}).get(self.metric_key(metric), {}))

    def get_ratio(self, numerator: str, denominator: str) -> Optional[float]:
        return self._ffill_lookup(self._history.get("ratios", {}).get(f"{numerator}/{denominator}", {}))

    def get_dividend_metric(self, ticker: str) -> Optional[float]:
        return self._ffill_lookup(self._history.get("dividends", {}).get(ticker, {}))

    def get_calendar_events(self, uid: str, selector: Optional[DateEventSelector], firestore=None) -> List[Dict[str, Any]]:
        if self._current is None:
            return []
        events = self._history.get("calendar", {}).get(self._current.isoformat(), [])
        return list(events) if isinstance(events, list) else []


def _coerce(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@dataclass
class BacktestDay:
    date: str
    triggered: bool
    value: Optional[Any] = None
    detail: Optional[str] = None


@dataclass
class BacktestReport:
    rule_id: str
    rule_version: Optional[int]
    engine_version: str
    fire_count: int
    days: List[BacktestDay] = field(default_factory=list)
    notes: str = ""


def _date_range(start: date, end: date) -> List[date]:
    days: List[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur = cur + timedelta(days=1)
    return days


def backtest_rule(
    rule: AlertRule,
    history: Dict[str, Any],
    date_range: tuple,
    evaluator_registry: Optional[dict] = None,
) -> BacktestReport:
    """Replay ``rule`` over ``date_range`` (inclusive) against ``history``.

    ``date_range`` is a ``(start, end)`` tuple of ``datetime.date`` or
    ``YYYY-MM-DD`` strings. Returns a deterministic BacktestReport with no side
    effects (no delivery, no Firestore writes, no rule-state mutation).
    """
    start, end = date_range
    if isinstance(start, str):
        start = date.fromisoformat(start)
    if isinstance(end, str):
        end = date.fromisoformat(end)

    ds = HistoricalDataSource(history)
    registry = evaluator_registry or build_default_registry(ds)

    if rule.condition is None:
        return BacktestReport(rule.id, rule.ruleVersion, ENGINE_VERSION, 0, notes="rule has no condition")
    evaluator = registry.get(rule.condition.kind)
    if evaluator is None:
        return BacktestReport(
            rule.id, rule.ruleVersion, ENGINE_VERSION, 0,
            notes=f"no evaluator for kind={rule.condition.kind}",
        )

    days: List[BacktestDay] = []
    fire_count = 0
    prev_value: Optional[float] = None

    for d in _date_range(start, end):
        ds.set_current_date(d)
        ctx = EvalContext(uid=rule.uid, now=ds.now(), prev_value=prev_value)
        result = evaluator.evaluate(rule, rule.condition, ctx)
        days.append(BacktestDay(d.isoformat(), result.triggered, result.value, result.detail))
        if result.triggered:
            fire_count += 1
        if isinstance(result.value, (int, float)):
            prev_value = float(result.value)

    notes = (
        f"Deterministic replay over {start.isoformat()}..{end.isoformat()} "
        f"(ruleVersion={rule.ruleVersion}, engineVersion={ENGINE_VERSION}). "
        "Read-only: no delivery, no Firestore writes."
    )
    return BacktestReport(rule.id, rule.ruleVersion, ENGINE_VERSION, fire_count, days, notes)
