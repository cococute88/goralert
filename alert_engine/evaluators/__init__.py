"""Condition evaluators, one per condition ``kind``.

The engine wires these into a registry keyed by condition kind. Each evaluator
reads only the fields relevant to its kind from the tolerant ``Condition``
dataclass and returns an ``EvalResult`` (triggered + observed value + detail).
"""

from .base import ConditionEvaluator, EvalResult, EvalContext
from .metric import MetricEvaluator
from .ratio import RatioEvaluator
from .dividend import DividendEvaluator
from .date import DateEvaluator
from .composite import CompositeEvaluator
from .custom import CustomEvaluator

__all__ = [
    "ConditionEvaluator",
    "EvalResult",
    "EvalContext",
    "MetricEvaluator",
    "RatioEvaluator",
    "DividendEvaluator",
    "DateEvaluator",
    "CompositeEvaluator",
    "CustomEvaluator",
    "build_default_registry",
]


def build_default_registry(datasource):
    """Build the {kind: evaluator} registry used by the engine.

    Metric kinds (rsi/vix/price/fx/gold/bitcoin/koreanEtf) all share the
    MetricEvaluator. composite delegates back into the registry recursively.
    """
    metric = MetricEvaluator(datasource)
    ratio = RatioEvaluator(datasource)
    dividend = DividendEvaluator(datasource)
    date = DateEvaluator(datasource)
    custom = CustomEvaluator(datasource)

    registry = {
        "rsi": metric,
        "vix": metric,
        "price": metric,
        "fx": metric,
        "gold": metric,
        "bitcoin": metric,
        "koreanEtf": metric,
        "ratio": ratio,
        "dividend": dividend,
        "date": date,
        "custom": custom,
    }
    registry["composite"] = CompositeEvaluator(registry)
    return registry
