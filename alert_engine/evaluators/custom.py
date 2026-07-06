"""Custom expression evaluator (escape hatch).

Evaluates a user-supplied boolean ``expression`` against a restricted namespace
built from ``condition.params`` plus a few safe helpers. The expression is
parsed with ``ast`` and only a whitelist of node types is permitted — no
attribute access, no calls to arbitrary names, no imports, no comprehensions
over external data.

SECURITY TODO(sandboxing): this is a *restricted* evaluator, NOT a hardened
sandbox. Before exposing arbitrary user expressions in a multi-tenant context,
move evaluation into a proper sandbox (e.g. a separate process with seccomp, or
a vetted expression library like ``asteval``/``simpleeval`` with resource
limits). For the MVP we whitelist AST nodes and a tiny function set.
"""

from __future__ import annotations

import ast
import operator as _op
from typing import Any, Dict, Optional

from ..models import AlertRule, Condition
from .base import EvalContext, EvalResult

# Allowed binary / comparison / boolean / unary operators.
_BIN_OPS = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.Mod: _op.mod,
    ast.Pow: _op.pow,
    ast.FloorDiv: _op.floordiv,
}
_CMP_OPS = {
    ast.Eq: _op.eq,
    ast.NotEq: _op.ne,
    ast.Lt: _op.lt,
    ast.LtE: _op.le,
    ast.Gt: _op.gt,
    ast.GtE: _op.ge,
}
_UNARY_OPS = {
    ast.UAdd: _op.pos,
    ast.USub: _op.neg,
    ast.Not: _op.not_,
}

# Safe helper functions exposed to expressions.
_SAFE_FUNCS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "float": float,
    "int": int,
    "len": len,
}


class _SafeEval(ast.NodeVisitor):
    def __init__(self, names: Dict[str, Any]):
        self._names = names

    def visit(self, node):  # noqa: D401 - dispatch
        method = "visit_" + type(node).__name__
        visitor = getattr(self, method, None)
        if visitor is None:
            raise ValueError(f"Disallowed expression element: {type(node).__name__}")
        return visitor(node)

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_Constant(self, node):
        if isinstance(node.value, (int, float, bool, str)) or node.value is None:
            return node.value
        raise ValueError("Disallowed constant type")

    def visit_Name(self, node):
        if node.id in self._names:
            return self._names[node.id]
        if node.id in ("True", "False", "None"):
            return {"True": True, "False": False, "None": None}[node.id]
        raise ValueError(f"Unknown name: {node.id}")

    def visit_BinOp(self, node):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError("Disallowed binary operator")
        return op(self.visit(node.left), self.visit(node.right))

    def visit_UnaryOp(self, node):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError("Disallowed unary operator")
        return op(self.visit(node.operand))

    def visit_BoolOp(self, node):
        values = [self.visit(v) for v in node.values]
        if isinstance(node.op, ast.And):
            result = True
            for v in values:
                result = result and v
            return result
        result = False
        for v in values:
            result = result or v
        return result

    def visit_Compare(self, node):
        left = self.visit(node.left)
        for op_node, comparator in zip(node.ops, node.comparators):
            op = _CMP_OPS.get(type(op_node))
            if op is None:
                raise ValueError("Disallowed comparison operator")
            right = self.visit(comparator)
            if not op(left, right):
                return False
            left = right
        return True

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCS:
            raise ValueError("Only whitelisted function calls are allowed")
        args = [self.visit(a) for a in node.args]
        if node.keywords:
            raise ValueError("Keyword arguments are not allowed")
        return _SAFE_FUNCS[node.func.id](*args)


def safe_eval(expression: str, names: Dict[str, Any]) -> Any:
    """Evaluate a restricted boolean/arithmetic expression. Raises on anything
    outside the whitelist."""
    tree = ast.parse(expression, mode="eval")
    return _SafeEval(names).visit(tree)


class CustomEvaluator:
    def __init__(self, datasource):
        self._ds = datasource

    def evaluate(self, rule: AlertRule, condition: Condition, ctx: EvalContext) -> EvalResult:
        expression: Optional[str] = condition.expression
        if not expression or not expression.strip():
            return EvalResult(False, None, detail="custom: empty expression")

        names: Dict[str, Any] = {}
        if isinstance(condition.params, dict):
            # Only primitive params are exposed to the expression namespace.
            for key, value in condition.params.items():
                if isinstance(value, (int, float, bool, str)):
                    names[key] = value
        names.setdefault("prev", ctx.prev_value)

        try:
            result = safe_eval(expression, names)
        except Exception as exc:  # noqa: BLE001
            return EvalResult(False, None, detail=f"custom: eval error ({exc})")

        triggered = bool(result)
        value = result if isinstance(result, (int, float)) else None
        return EvalResult(triggered, value, detail=f"custom '{expression}' -> {triggered}")
