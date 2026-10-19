"""Safe calculators.

* ``evaluate(expr)``: arithmetic expressions parsed with ``ast`` (numbers, + - * / ** %, parentheses,
  and a small whitelist of functions). No names, attributes, calls outside the whitelist or comprehensions:
  never ``eval``.
* ``run_program(program, table)``: executor of the FinQA program language (add, subtract, multiply, divide,
  exp, greater, table_sum/average/max/min with #n step references and const_ values), used to verify the
  calculator against expert-annotated programs."""

from __future__ import annotations

import ast
import math
import operator
import re
from typing import Any

MAX_ABS = 1e15
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow,
        ast.Mod: operator.mod}
_FUN = {"abs": abs, "round": lambda x, n=0: round(x, int(n)), "min": min, "max": max, "sqrt": math.sqrt, "log": math.log, "exp": math.exp}


class CalcError(ValueError):
    pass


def evaluate(expr: str) -> float:
    if len(expr) > 300:
        raise CalcError("Expression too long")
    # Decimal comma only between digits ("4412,45"); ", " (comma + space) separates function arguments.
    cleaned = re.sub(r"(?<=\d),(?=\d)", ".", expr.replace("\u2212", "-").replace("×", "*").replace("÷", "/"))
    cleaned = re.sub(r"(\d)\s+(?=\d{3}\b)", r"\1", cleaned)  # "1 234" → "1234"
    cleaned = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", cleaned)
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise CalcError("Invalid expression") from exc
    return _eval(tree.body, depth=0)


def _eval(node: ast.AST, depth: int) -> float:
    if depth > 40:
        raise CalcError("Expression too deep")
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval(node.operand, depth + 1)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        a, b = _eval(node.left, depth + 1), _eval(node.right, depth + 1)
        if isinstance(node.op, ast.Pow) and (abs(b) > 12 or abs(a) > 1e6):
            raise CalcError("Exponent out of range")
        if isinstance(node.op, (ast.Div, ast.Mod)) and b == 0:
            raise CalcError("Division by zero")
        return _check(_BIN[type(node.op)](a, b))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUN and not node.keywords:
        args = [_eval(a, depth + 1) for a in node.args]
        if not 1 <= len(args) <= 4:
            raise CalcError("Bad arity")
        try:
            return _check(float(_FUN[node.func.id](*args)))
        except (ValueError, OverflowError, TypeError) as exc:
            raise CalcError(str(exc)) from exc
    raise CalcError("Only numbers, + - * / ** %, parentheses and abs/round/min/max/sqrt/log/exp are allowed")


def _check(v: float) -> float:
    if math.isnan(v) or math.isinf(v) or abs(v) > MAX_ABS:
        raise CalcError("Result out of range")
    return float(v)


# ------------------------------------------------------------------------------------------------
# FinQA program language
# ------------------------------------------------------------------------------------------------
STEP = re.compile(r"(\w+)\(([^()]*)\)")


def _arg(tok: str, results: list[Any]) -> float:
    tok = tok.strip()
    if tok.startswith("#"):
        return float(results[int(tok[1:])])
    if tok.startswith("const_"):
        c = tok[6:]
        return -1.0 if c == "m1" else float(c)
    pct = tok.endswith("%")
    t = tok.replace(",", "").replace("$", "").replace("%", "").strip()
    return float(t) / 100 if pct else float(t)


def _row_values(table: list[list[str]], name: str) -> list[float]:
    for row in table:
        if row and row[0].strip().lower() == name.strip().lower():
            vals = []
            for c in row[1:]:
                m = re.search(r"-?\d[\d,]*\.?\d*", c.replace("$", "").replace("(", "-"))
                if m:
                    v = float(m.group(0).replace(",", ""))
                    vals.append(v / 100 if "%" in c else v)
            return vals
    raise CalcError(f"Row not found: {name}")


def run_program(program: str, table: list[list[str]] | None = None) -> float | str:
    results: list[Any] = []
    for op, raw_args in STEP.findall(program):
        args = [a for a in raw_args.split(",")] if op.startswith("table_") else [a for a in raw_args.split(",") if a.strip()]
        if op.startswith("table_"):
            vals = _row_values(table or [], raw_args.rsplit(",", 1)[0] if raw_args.count(",") else raw_args)
            if not vals:
                raise CalcError("Empty row")
            r: Any = {"table_sum": sum(vals), "table_average": sum(vals) / len(vals), "table_max": max(vals),
                      "table_min": min(vals)}[op]
        else:
            a, b = _arg(args[0], results), _arg(args[1], results)
            if op == "add":
                r = a + b
            elif op == "subtract":
                r = a - b
            elif op == "multiply":
                r = a * b
            elif op == "divide":
                if b == 0:
                    raise CalcError("Division by zero")
                r = a / b
            elif op == "exp":
                r = a ** b
            elif op == "greater":
                r = "yes" if a > b else "no"
            else:
                raise CalcError(f"Unknown op {op}")
        results.append(r)
    if not results:
        raise CalcError("Empty program")
    return results[-1]


def matches(pred: float | str, gold: float | str, rel: float = 1e-3) -> bool:
    if isinstance(pred, str) or isinstance(gold, str):
        return str(pred).strip().lower() == str(gold).strip().lower()
    if gold == 0:
        return abs(pred) < 1e-6
    return abs(pred - gold) <= max(rel * abs(gold), 5e-3)
