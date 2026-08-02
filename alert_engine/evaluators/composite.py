"""Composite evaluator: AND/OR over child conditions (recursive).

Delegates each child back into the shared registry, so nested composites and
any mix of metric/ratio/date/etc. children are supported. Evaluation retains
three-valued semantics: unavailable/error children are not silently converted
to false when they could change the composite result.
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
            return EvalResult(
                False,
                0.0,
                detail="composite: no child conditions",
                status="evaluation_error",
                failure_code="empty_composite",
            )

        results = []
        triggered_count = 0
        child_results = []
        for child in children:
            evaluator = self._registry.get(child.kind)
            if evaluator is None:
                results.append(f"[{child.kind}: no evaluator]")
                child_result = EvalResult(
                    False,
                    None,
                    detail=f"{child.kind}: no evaluator",
                    status="evaluation_error",
                    failure_code="missing_child_evaluator",
                )
            else:
                child_result = evaluator.evaluate(rule, child, ctx)
                results.append(f"[{child.kind}: {child_result.status}]")
            child_results.append(child_result)
            if child_result.triggered:
                triggered_count += 1

        unavailable = [result for result in child_results if result.status not in {"triggered", "condition_false"}]
        if operator == "or" and triggered_count > 0:
            triggered = True
        elif operator == "and" and any(result.status == "condition_false" for result in child_results):
            triggered = False
        elif unavailable:
            priority = {"evaluation_error": 4, "provider_error": 3, "stale_data": 2, "no_data": 1}
            failure = max(unavailable, key=lambda result: priority.get(result.status or "", 0))
            return EvalResult(
                False,
                float(triggered_count),
                detail=f"composite {operator.upper()} -> {failure.status} {' '.join(results)}",
                status=failure.status,
                failure_code=failure.failure_code,
                observed_at=failure.observed_at,
            )
        else:
            triggered = operator == "and"
        return EvalResult(
            triggered, float(triggered_count),
            detail=f"composite {operator.upper()} -> {triggered} {' '.join(results)}",
        )
