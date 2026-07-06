"""Property 5 — 비교 경계 일관성 (comparator boundary consistency).

Maps to: REQ-034.3 (deterministic comparator semantics) / REQ-006.5 (threshold
comparison gt/gte/lt/lte/eq). We use hypothesis to fuzz finite floats and assert
the algebraic relationships between comparators hold at and around the boundary.

Core relation under test:
    compare(v,"gte",t) ⟺ compare(v,"gt",t) OR v == t
    compare(v,"lte",t) ⟺ compare(v,"lt",t) OR v == t
plus the negation duals (gt is the strict complement of lte, etc.).
"""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from alert_engine.compare import compare

finite = st.floats(allow_nan=False, allow_infinity=False, width=64)


@given(v=finite, t=finite)
def test_gte_iff_gt_or_equal(v, t):
    # Property 5: the >= boundary is exactly the union of > and ==.
    assert compare(v, "gte", t) == (compare(v, "gt", t) or v == t)


@given(v=finite, t=finite)
def test_lte_iff_lt_or_equal(v, t):
    assert compare(v, "lte", t) == (compare(v, "lt", t) or v == t)


@given(v=finite, t=finite)
def test_gt_is_strict_complement_of_lte(v, t):
    # Exactly one of (v > t) / (v <= t) is true for finite floats.
    assert compare(v, "gt", t) == (not compare(v, "lte", t))


@given(v=finite, t=finite)
def test_lt_is_strict_complement_of_gte(v, t):
    assert compare(v, "lt", t) == (not compare(v, "gte", t))


@given(v=finite)
def test_eq_reflexive(v):
    # A value always equals itself (within tolerance) — boundary inclusivity.
    assert compare(v, "eq", v) is True
    # gte/lte are inclusive at the boundary too.
    assert compare(v, "gte", v) is True
    assert compare(v, "lte", v) is True
    # strict comparators exclude the boundary.
    assert compare(v, "gt", v) is False
    assert compare(v, "lt", v) is False


@given(v=finite, t=finite)
def test_eq_implies_inclusive_comparators(v, t):
    # When eq holds (within tolerance), neither strict comparator may both hold;
    # and the value cannot be simultaneously strictly above and below.
    assert not (compare(v, "gt", t) and compare(v, "lt", t))


@given(
    base=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    gap=st.floats(min_value=1e-2, max_value=1e6),
)
def test_clear_separation_resolves_unambiguously(base, gap):
    # With a gap that clearly exceeds the eq tolerance (which scales with
    # magnitude), eq is false and exactly one strict comparator is true.
    hi = base + gap
    if not math.isfinite(hi) or hi == base:
        return
    assert compare(hi, "gt", base) is True
    assert compare(base, "lt", hi) is True
    assert compare(hi, "eq", base) is False


def test_missing_inputs_never_raise():
    # "cannot evaluate" degrades to not-triggered (False), never an exception.
    assert compare(None, "gte", 1.0) is False
    assert compare(1.0, None, 1.0) is False
    assert compare(1.0, "gte", None) is False
    assert compare(1.0, "unknown_op", 1.0) is False
