"""Sandbox regression tests for finite provider number parsing."""
from __future__ import annotations

import ast
import math
import pathlib
import unittest


def _load_parsers():
    here = pathlib.Path(__file__).resolve()
    candidates = [
        here.parents[1] / "services/api/src/tothemoon_api/external/security.py",
        here.parents[0].parent / "services/api/src/tothemoon_api/external/security.py",
        pathlib.Path.cwd() / "services/api/src/tothemoon_api/external/security.py",
    ]
    source_path = next((p for p in candidates if p.is_file()), candidates[0])
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))
    selected: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            math_aliases = [a for a in node.names if a.name.split(".")[0] == "math"]
            if math_aliases:
                selected.append(ast.Import(names=math_aliases))
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.module.split(".")[0] == "math":
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in {"_float", "_risk_score"}:
            selected.append(node)
    names = {n.name for n in selected if isinstance(n, ast.FunctionDef)}
    assert names == {"_float", "_risk_score"}, f"expected both parsers, got {names}"
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    code = compile(module, str(source_path), "exec")
    sandbox: dict = {"math": math, "__builtins__": __builtins__}  # noqa: F821
    exec(code, sandbox)  # noqa: S102
    return sandbox["_float"], sandbox["_risk_score"]


_FLOAT, _RISK_SCORE = _load_parsers()

NAN_VALUES = [float("nan"), "nan", "NaN", "NAN", "nAn"]
INF_VALUES = [
    float("inf"),
    float("-inf"),
    "inf",
    "-inf",
    "+inf",
    "Infinity",
    "-Infinity",
    "+Infinity",
    "INFINITY",
]


class FiniteProviderNumbersTest(unittest.TestCase):
    def test_float_rejects_nan_with_explicit_default(self):
        for raw in NAN_VALUES:
            with self.subTest(raw=raw):
                result = _FLOAT(raw, 12.5)
                self.assertEqual(result, 12.5)
                self.assertTrue(math.isfinite(result))

    def test_float_rejects_infinity_with_explicit_default(self):
        for raw in INF_VALUES:
            with self.subTest(raw=raw):
                result = _FLOAT(raw, 3.25)
                self.assertEqual(result, 3.25)
                self.assertTrue(math.isfinite(result))

    def test_float_overflow_string_returns_default_without_raising(self):
        try:
            result = _FLOAT("1e1000", 7.0)
        except OverflowError:
            self.fail("_float raised OverflowError")
        self.assertEqual(result, 7.0)

    def test_float_huge_int_returns_default_without_raising(self):
        try:
            result = _FLOAT(10**1000, 8.0)
        except OverflowError:
            self.fail("_float raised OverflowError")
        self.assertEqual(result, 8.0)

    def test_float_keeps_finite_conversions(self):
        self.assertEqual(_FLOAT("12.5"), 12.5)
        self.assertEqual(_FLOAT(3), 3.0)
        self.assertEqual(_FLOAT(2.5), 2.5)
        self.assertEqual(_FLOAT(None, 9.0), 9.0)
        self.assertEqual(_FLOAT({}, 9.0), 9.0)

    def test_risk_score_rejects_non_finite_with_default(self):
        for raw in NAN_VALUES + INF_VALUES:
            with self.subTest(raw=raw):
                try:
                    result = _RISK_SCORE(False, raw, default=15)
                except OverflowError:
                    self.fail("_risk_score raised OverflowError")
                self.assertEqual(result, 15)

    def test_risk_score_overflow_string_returns_default(self):
        try:
            result = _RISK_SCORE(False, "1e1000", default=21)
        except OverflowError:
            self.fail("_risk_score raised OverflowError")
        self.assertEqual(result, 21)

    def test_risk_score_keeps_clamp_and_honeypot(self):
        self.assertEqual(_RISK_SCORE(True, float("nan")), 99)
        self.assertEqual(_RISK_SCORE(True, "inf"), 99)
        self.assertEqual(_RISK_SCORE(False, "80"), 80)
        self.assertEqual(_RISK_SCORE(False, 42.9), 42)
        self.assertEqual(_RISK_SCORE(False, -5), 0)
        self.assertEqual(_RISK_SCORE(False, 150), 99)
        self.assertEqual(_RISK_SCORE(False, None, default=15), 15)


if __name__ == "__main__":
    unittest.main()
