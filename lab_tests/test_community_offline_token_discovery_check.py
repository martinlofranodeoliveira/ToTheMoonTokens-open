import copy
import unittest
from tothemoon_api.community.offline_token_discovery_check import run_feature
class OfflineTokenDiscoveryCheckTest(unittest.TestCase):
    def test_non_dict_payload(self):
        for bad in (None, [], "weth", 123, 12.5, True):
            with self.subTest(bad=bad):
                res = run_feature(bad)
                self.assertEqual(res, {"ok": False, "symbol": "", "chain": "unknown", "risk_score": 0, "flagged": False, "issues": ["INVALID_PAYLOAD"]})
        res = run_feature(({"symbol": "WETH"},))
        self.assertEqual(res, {"ok": False, "symbol": "", "chain": "unknown", "risk_score": 0, "flagged": False, "issues": ["INVALID_PAYLOAD"]})
    def test_valid_dict_normalized(self):
        payload = {"symbol": "weth", "chain": "ethereum", "risk_score": 12.5, "flagged": False}
        res = run_feature(payload)
        self.assertEqual(res, {"ok": True, "symbol": "WETH", "chain": "ethereum", "risk_score": 12.5, "flagged": False, "issues": []})
    def test_valid_with_whitespace_symbol(self):
        res = run_feature({"symbol": " weth ", "chain": "ethereum", "risk_score": 12.5, "flagged": False})
        self.assertEqual(res["symbol"], "WETH")
        self.assertTrue(res["ok"])
        self.assertEqual(res["issues"], [])
    def test_missing_optionals_default_without_issues(self):
        res = run_feature({"symbol": "SOL"})
        self.assertEqual(res, {"ok": True, "symbol": "SOL", "chain": "unknown", "risk_score": 0, "flagged": False, "issues": []})
    def test_all_invalid_fixed_order(self):
        payload = {"symbol": "", "chain": "unknown-chain", "risk_score": 999, "flagged": 1}
        res = run_feature(payload)
        self.assertEqual(res, {"ok": False, "symbol": "", "chain": "unknown", "risk_score": 0, "flagged": False, "issues": ["INVALID_SYMBOL", "INVALID_CHAIN", "INVALID_RISK_SCORE", "INVALID_FLAGGED"]})
    def test_extra_keys_ignored(self):
        payload = {"symbol": "weth", "chain": "base", "risk_score": 10, "flagged": True, "note": 123}
        res = run_feature(payload)
        self.assertEqual(res, {"ok": True, "symbol": "WETH", "chain": "base", "risk_score": 10, "flagged": True, "issues": []})
    def test_symbol_boundaries(self):
        res = run_feature({"symbol": "a"})
        self.assertEqual(res["symbol"], "A")
        self.assertTrue(res["ok"])
        res = run_feature({"symbol": " abc_def-1.2 "})
        self.assertEqual(res["symbol"], "ABC_DEF-1.2")
        self.assertTrue(res["ok"])
        for bad in ("", "   ", "a b", "a!", "aaaaaaaaaaaaaaaaaaaaa"):
            with self.subTest(bad=bad):
                res = run_feature({"symbol": bad})
                self.assertFalse(res["ok"])
                self.assertIn("INVALID_SYMBOL", res["issues"])
                self.assertEqual(res["symbol"], "")
    def test_risk_score_types(self):
        res = run_feature({"symbol": "WETH", "risk_score": 0})
        self.assertTrue(res["ok"])
        self.assertEqual(res["risk_score"], 0)
        res = run_feature({"symbol": "WETH", "risk_score": 100})
        self.assertTrue(res["ok"])
        for bad in (True, False, "12", None, -1, 101, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                res = run_feature({"symbol": "WETH", "chain": "solana", "risk_score": bad, "flagged": False})
                self.assertFalse(res["ok"])
                self.assertIn("INVALID_RISK_SCORE", res["issues"])
                self.assertEqual(res["risk_score"], 0)
    def test_deterministic_pure_and_preserves_input(self):
        payload = {"symbol": " weth ", "chain": "ethereum", "risk_score": 12.5, "flagged": False, "note": [1, 2]}
        snapshot = copy.deepcopy(payload)
        first = run_feature(payload)
        second = run_feature(payload)
        self.assertEqual(first, second)
        self.assertEqual(payload, snapshot)
        only_symbol = {"symbol": "SOL"}
        snap2 = copy.deepcopy(only_symbol)
        run_feature(only_symbol)
        self.assertEqual(only_symbol, snap2)
if __name__ == "__main__":
    unittest.main()
