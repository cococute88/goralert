"""Evaluator protocol + shared result/context types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol, Union

from ..models import AlertRule, Condition


@dataclass
class EvalResult:
    """Outcome of evaluating a single condition.

    - triggered: did the condition fire?
    - value: observed value (RSI, ratio, dividend, matched-event count, ...)
    - detail: human-readable explanation (logged, useful for backtests)
    """

    triggered: bool
    value: Optional[Union[float, str]] = None
    detail: Optional[str] = None
    status: Optional[str] = None
    failure_code: Optional[str] = None
    observed_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.status is None:
            self.status = "triggered" if self.triggered else "condition_false"


@dataclass
class EvalContext:
    """Per-evaluation context shared with evaluators.

    ``now`` is the evaluation instant. ``prev_value`` carries the rule's
    ``lastValue`` so cross-up/cross-down comparators can detect transitions.
    """

    uid: str
    now: datetime
    prev_value: Optional[float] = None
    settings: Any = None
    extra: dict = field(default_factory=dict)


class ConditionEvaluator(Protocol):
    """Structural protocol implemented by every evaluator."""

    def evaluate(self, rule: AlertRule, condition: Condition, ctx: EvalContext) -> EvalResult:
        ...
