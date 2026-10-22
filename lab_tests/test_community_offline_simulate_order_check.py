"""Public regression tests for offline simulate order checker."""

import unittest

try:
    from tothemoon_api.community.offline_simulate_order_check import run_feature
except ImportError:
    from services.api.src.tothemoon_api.community.offline_simulate_order_check import run_feature


class TestOfflineSimulateOrderCheck(unittest.TestCase):
    def test_example_full_valid(self):
        out = run_feature({"price_usd": 12.5, "quantity": 10, "side": "buy", "symbol": " weth "})
        self.assertEqual(out, {"symbol": "WETH", "side": "buy", "quantity": 10, "price_usd": 12.5, "issues": [], "ok": True})

    def test_example_defaults(self):
        out = run_feature({"symbol": "SOL"})
        self.assertEqual(out, {"symbol": "SOL", "side": "buy", "quantity": 0, "price_usd": 0, "issues": [], "ok": True})

    def test_example_all_invalid(self):
        out = run_feature({"price_usd": "free", "quantity": -5, "side": "hold", "symbol": ""})
        self.assertEqual(out, {"symbol": "", "side": "buy", "quantity": 0, "price_usd": 0, "issues": ["INVALID_SYMBOL", "INVALID_SIDE", "INVALID_QUANTITY", "INVALID_PRICE"], "ok": False})

    def test_non_dict_payloads(self):
        expected = {"symbol": "", "side": "buy", "quantity": 0, "price_usd": 0, "issues": ["INVALID_PAYLOAD"], "ok": False}
        for bad in [None, [], [1], "x", "", 0, 12.5, True, False]:
            with self.subTest(bad=bad):
                self.assertEqual(run_feature(bad), expected)
        self.assertEqual(run_feature((1, 2)), expected)

    def test_empty_dict_missing_symbol(self):
        out = run_feature({})
        self.assertEqual(out, {"symbol": "", "side": "buy", "quantity": 0, "price_usd": 0, "issues": ["INVALID_SYMBOL"], "ok": False})

    def test_symbol_normalization(self):
        out = run_feature({"symbol": " a.b_c-d "})
        self.assertEqual(out["symbol"], "A.B_C-D")
        self.assertEqual(out["issues"], [])
        self.assertTrue(out["ok"])
        out2 = run_feature({"symbol": "Ab12.-_"})
        self.assertEqual(out2["symbol"], "AB12.-_")
        self.assertTrue(out2["ok"])

    def test_symbol_boundaries(self):
        self.assertTrue(run_feature({"symbol": "A"})["ok"])
        self.assertTrue(run_feature({"symbol": "12345678901234567890"})["ok"])
        self.assertEqual(run_feature({"symbol": "123456789012345678901"})["issues"], ["INVALID_SYMBOL"])
        self.assertEqual(run_feature({"symbol": "   "})["issues"], ["INVALID_SYMBOL"])
        self.assertEqual(run_feature({"symbol": "A B"})["issues"], ["INVALID_SYMBOL"])
        self.assertEqual(run_feature({"symbol": "A/B"})["issues"], ["INVALID_SYMBOL"])
        self.assertEqual(run_feature({"symbol": 123})["issues"], ["INVALID_SYMBOL"])
        self.assertEqual(run_feature({"symbol": None})["issues"], ["INVALID_SYMBOL"])

    def test_side_defaults_and_validation(self):
        self.assertEqual(run_feature({"symbol": "BTC"})["side"], "buy")
        self.assertTrue(run_feature({"symbol": "BTC"})["ok"])
        self.assertEqual(run_feature({"symbol": "BTC", "side": "sell"})["side"], "sell")
        self.assertTrue(run_feature({"symbol": "BTC", "side": "sell"})["ok"])
        for bad in ["Buy", "BUY", "SELL", "", "hold", None, 0, True]:
            with self.subTest(bad=bad):
                out = run_feature({"symbol": "BTC", "side": bad})
                self.assertEqual(out["side"], "buy")
                self.assertEqual(out["issues"], ["INVALID_SIDE"])
                self.assertFalse(out["ok"])

    def test_quantity_validation(self):
        self.assertEqual(run_feature({"symbol": "BTC"})["quantity"], 0)
        self.assertEqual(run_feature({"symbol": "BTC", "quantity": 0})["quantity"], 0)
        self.assertEqual(run_feature({"symbol": "BTC", "quantity": 1000000})["quantity"], 1000000)
        for bad in [-1, 1000001, 10.0, 10.5, "10", None, True, False]:
            with self.subTest(bad=bad):
                out = run_feature({"symbol": "BTC", "quantity": bad})
                self.assertEqual(out["quantity"], 0)
                self.assertIn("INVALID_QUANTITY", out["issues"])

    def test_price_validation(self):
        self.assertEqual(run_feature({"symbol": "BTC"})["price_usd"], 0)
        self.assertEqual(run_feature({"symbol": "BTC", "price_usd": 0})["price_usd"], 0)
        self.assertEqual(run_feature({"symbol": "BTC", "price_usd": 1000000})["price_usd"], 1000000)
        self.assertEqual(run_feature({"symbol": "BTC", "price_usd": 0.0})["price_usd"], 0.0)
        for bad in [-1, -0.1, 1000001, 1000000.01, "free", None, True, False]:
            with self.subTest(bad=bad):
                out = run_feature({"symbol": "BTC", "price_usd": bad})
                self.assertEqual(out["price_usd"], 0)
                self.assertIn("INVALID_PRICE", out["issues"])
        for bad in [float("nan"), float("inf"), float("-inf")]:
            with self.subTest(bad=bad):
                out = run_feature({"symbol": "BTC", "price_usd": bad})
                self.assertEqual(out["price_usd"], 0)
                self.assertIn("INVALID_PRICE", out["issues"])

    def test_extra_keys_ignored(self):
        out = run_feature({"symbol": "BTC", "foo": "bar", "extra": 123})
        self.assertEqual(out, {"symbol": "BTC", "side": "buy", "quantity": 0, "price_usd": 0, "issues": [], "ok": True})

    def test_issues_fixed_order(self):
        out = run_feature({"symbol": "", "side": "x", "quantity": -1, "price_usd": "y"})
        self.assertEqual(out["issues"], ["INVALID_SYMBOL", "INVALID_SIDE", "INVALID_QUANTITY", "INVALID_PRICE"])
        out2 = run_feature({"symbol": "BTC", "side": "x", "price_usd": -1})
        self.assertEqual(out2["issues"], ["INVALID_SIDE", "INVALID_PRICE"])

    def test_deterministic_and_preserves_input(self):
        payload = {"symbol": " weth ", "side": "buy", "quantity": 5, "price_usd": 1.5, "extra": [1, 2]}
        snapshot = {"symbol": " weth ", "side": "buy", "quantity": 5, "price_usd": 1.5, "extra": [1, 2]}
        first = run_feature(payload)
        second = run_feature(payload)
        self.assertEqual(first, second)
        self.assertEqual(payload, snapshot)
        bad = {"symbol": "", "side": "x"}
        snap2 = {"symbol": "", "side": "x"}
        run_feature(bad)
        self.assertEqual(bad, snap2)

    def test_total_for_json_domain(self):
        samples = [{}, {"symbol": "BTC"}, [], None, 0, "s", True, {"symbol": None, "side": None, "quantity": None, "price_usd": None}]
        for s in samples:
            with self.subTest(s=s):
                out = run_feature(s)
                self.assertIn("symbol", out)
                self.assertIn("side", out)
                self.assertIn("quantity", out)
                self.assertIn("price_usd", out)
                self.assertIn("issues", out)
                self.assertIn("ok", out)
                self.assertEqual(out["ok"], len(out["issues"]) == 0)
