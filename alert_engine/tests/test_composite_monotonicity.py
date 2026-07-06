"""Property 4 — composite monotonicity (AND/OR child addition is monotone).

Maps to: REQ-020.2 / REQ-020.3. Adding a child to an OR composite can only flip
the result false->true (never true->false). Adding a child to an AND composite
can only flip the result true->false (never false->true). We fuzz boolean child
vectors with hypothesis and evaluate the real CompositeEvaluator via the default
registry (children are deterministic custom expressions).
"""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import given
from hypothesis import strategies as st

from alert_engine.evaluators import build_default_registry
from alert_engine.evaluators.base import EvalContext
from alert_engine.models import AlertRule, Condition

from .conftest import FakeDataSource

_DS = FakeDataSource()
_REGISTRY = build_default_registry(_DS)
_RULE = AlertRule.from_dict({
    "id": "r", "uid": "u1", "kind": "composite", "name": "c", "enabled": True,
    "condition": {"kind": "composite"}, "trigger": {}, "delivery": {"channels": ["telegram"]},
})


def _child(value: bool) -> Condition:
    # Deterministic custom child: "1==1" -> True, "1==2" -> False.
    return Condition(kind="custom", expression="1==1" if value else "1==2")


def _eval_composite(operator: str, vec) -> bool:
    cond = Condition(kind="composite", operator=operator, conditions=[_child(b) for b in vec])
    ctx = EvalContext(uid="u1", now=datetime(2024, 5, 1, tzinfo=timezone.utc))
    return _REGISTRY["composite"].evaluate(_RULE, cond, ctx).triggered


vecs = st.lists(st.booleans(), min_size=1, max_size=6)


@given(vec=vecs)
def test_or_equals_any(vec):
    assert _eval_composite("or", vec) == any(vec)


@given(vec=vecs)
def test_and_equals_all(vec):
    assert _eval_composite("and", vec) == all(vec)


@given(vec=vecs, new_child=st.booleans())
def test_or_adding_child_only_flips_false_to_true(vec, new_child):
    before = _eval_composite("or", vec)
    after = _eval_composite("or", list(vec) + [new_child])
    # Monotone increasing: after >= before (True can't become False).
    assert after >= before
    if before is True:
        assert after is True


@given(vec=vecs, new_child=st.booleans())
def test_and_adding_child_only_flips_true_to_false(vec, new_child):
    before = _eval_composite("and", vec)
    after = _eval_composite("and", list(vec) + [new_child])
    # Monotone decreasing: after <= before (False can't become True).
    assert after <= before
    if before is False:
        assert after is False


def test_empty_composite_is_not_triggered():
    cond = Condition(kind="composite", operator="or", conditions=[])
    ctx = EvalContext(uid="u1", now=datetime(2024, 5, 1, tzinfo=timezone.utc))
    assert _REGISTRY["composite"].evaluate(_RULE, cond, ctx).triggered is False
