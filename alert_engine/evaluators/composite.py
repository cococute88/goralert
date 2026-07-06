"""Composite evaluator: AND/OR over child conditions (recursive).

Delegates each child back into the shared registry, so nested composites and
any mix of metric/ratio/date/etc. children are supported. Short-circuits like
boolean logic. The composite's observed value is the count of triggered
children (useful for backtest detail); the first triggered child's detail is
bubbled up via the detail string.
"""

from __future__ import annotations

from typing import Dict

from ..models import AlertRule, Condition
from .base import ConditionEvaluator, EvalContext, EvalResult


class CompositeEvaluator:
    def __init__(self, registry: Dict[str, ConditionEvaluator]):
        # Shared reference to the engine registry (mutated to include composite).
        self._registry = registry

    def evaluate(self, rule: AlertRule, condition: Condition, ctx: EvalContext) -> EvalResult:
        operator = (condition.operator or "and").lower()
        children = condition.conditions or []
        if not children:
            return EvalResult(False, 0.0, detail="composite: no child conditions")

        results = []
        triggered_count = 0
        for child in children:
            evaluator = self._registry.get(child.kind)
            if evaluator is None:
                results.append(f"[{child.kind}: no evaluator]")
                child_result = EvalResult(False, None, detail=f"{child.kind}: no evaluator")
            else:
                child_result = evaluator.evaluate(rule, child, ctx)
                results.append(f"[{child.kind}: {child_result.triggered}]")
            if child_result.triggered:
                triggered_count += 1
            # Short-circuit.
            if operator == "or" and child_result.triggered:
                return EvalResult(
                    True, float(triggered_count),
                    detail=f"composite OR -> True {' '.join(results)}",
                )
            if operator == "and" and not child_result.triggered:
                return EvalResult(
                    False, float(triggered_count),
                    detail=f"composite AND -> False {' '.join(results)}",
                )

        triggered = (operator == "and") or (operator == "or" and triggered_count > 0)
        return EvalResult(
            triggered, float(triggered_count),
            detail=f"composite {operator.upper()} -> {triggered} {' '.join(results)}",
        )
