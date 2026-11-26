"""Celina AI - Router Module: Gear 0 (Reflex) logic.

Contains:
- Gear 0 classify result cache (bounded LRU, 512 entries)
- Safe AST math evaluator (eval-free, 0 LLM tokens)
- try_gear0_math() — arithmetic evaluator for Gear 0 reflex queries
"""

from __future__ import annotations

import ast
import math
import operator as _op
import re
import threading
from collections import OrderedDict
from typing import Any

# ---------------------------------------------------------------------------
# Gear 0 classify result cache (Update 25)
#
# Caches (clean_query) -> (mod_name, handler) so repeated queries skip the
# sequential classify chain. Bounded to 512 entries with LRU eviction to
# prevent unbounded memory growth.
# ---------------------------------------------------------------------------

_GEAR0_CLASSIFY_CACHE: OrderedDict[str, tuple[str, str]] = OrderedDict()
_GEAR0_CLASSIFY_CACHE_MAX = 512
_GEAR0_CLASSIFY_CACHE_LOCK = threading.Lock()


def _gear0_classify_cached(clean: str) -> tuple[str, str] | None:
    """Return cached (mod_name, handler) for clean, or None if not cached."""
    with _GEAR0_CLASSIFY_CACHE_LOCK:
        return _GEAR0_CLASSIFY_CACHE.get(clean)


def _gear0_classify_store(clean: str, mod_name: str, handler: str) -> None:
    """Store (mod_name, handler) in the classify cache with LRU eviction."""
    with _GEAR0_CLASSIFY_CACHE_LOCK:
        if clean in _GEAR0_CLASSIFY_CACHE:
            _GEAR0_CLASSIFY_CACHE.move_to_end(clean)
            return
        _GEAR0_CLASSIFY_CACHE[clean] = (mod_name, handler)
        if len(_GEAR0_CLASSIFY_CACHE) > _GEAR0_CLASSIFY_CACHE_MAX:
            _GEAR0_CLASSIFY_CACHE.popitem(last=False)


# ---------------------------------------------------------------------------
# Safe AST math evaluator
# ---------------------------------------------------------------------------

_GEAR0_MATH_BINOPS: dict[type, Any] = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv,
    ast.Mod: _op.mod,
    ast.Pow: _op.pow,
}
_GEAR0_MATH_UNARYOPS: dict[type, Any] = {ast.UAdd: _op.pos, ast.USub: _op.neg}
_GEAR0_MATH_FUNCS: dict[str, Any] = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
}
_GEAR0_MATH_CONSTS: dict[str, Any] = {"pi": math.pi, "e": math.e}
_GEAR0_MATH_PREFIX_RE = re.compile(
    r"^(?:(?:what(?:'s|\s+is)|calculate|compute|eval(?:uate)?|solve)\s+)+",
    re.IGNORECASE,
)


def _safe_math_eval(node: ast.AST) -> int | float:
    """Safely evaluate an AST math node without using eval()."""
    if isinstance(node, ast.Expression):
        return _safe_math_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _GEAR0_MATH_BINOPS:
        left = _safe_math_eval(node.left)
        right = _safe_math_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("Exponent too large")
        return _GEAR0_MATH_BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _GEAR0_MATH_UNARYOPS:
        return _GEAR0_MATH_UNARYOPS[type(node.op)](_safe_math_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        fn = _GEAR0_MATH_FUNCS.get(node.func.id)
        if fn is not None and 1 <= len(node.args) <= 2:
            return fn(*(_safe_math_eval(a) for a in node.args))
    if isinstance(node, ast.Name) and node.id in _GEAR0_MATH_CONSTS:
        return _GEAR0_MATH_CONSTS[node.id]
    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


def try_gear0_math(query: str) -> str | None:
    """Eval-free AST arithmetic evaluator for Gear 0 (Reflex, 0 LLM tokens).
    Evaluates expressions like `sqrt(144)`, `what is 12 * 12?`, `2^8`, `sin(0)`.
    Returns None for non-math or bare single-number inputs.
    """
    raw = (query or "").strip()
    if not raw or len(raw) > 120:
        return None
    stripped = _GEAR0_MATH_PREFIX_RE.sub("", raw).strip().rstrip("=?").strip()
    if not stripped:
        return None
    expr = stripped.replace("^", "**")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    if isinstance(tree.body, ast.Constant):
        return None
    try:
        result = _safe_math_eval(tree)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(result, float):
        if math.isnan(result) or math.isinf(result):
            return None
        if result.is_integer():
            return str(int(result))
        return f"{result:.10g}"
    return str(result)
