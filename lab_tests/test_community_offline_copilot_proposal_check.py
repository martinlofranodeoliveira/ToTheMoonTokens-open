import math
import unittest

from tothemoon_api.community.offline_copilot_proposal_check import run_feature


class OfflineCopilotProposalCheckTest(unittest.TestCase):
    def test_non_dict_payloads(self):
        cases = [None, True, False, 0, 3.14, "x", [], ["a"], ("a",), 42]
        expected = {
            "ok": False,
            "title": "",
            "strategy": "unknown",
            "confidence": 0,
            "status": "draft",
            "issues": ["INVALID_PAYLOAD"],
        }
        for payload in cases:
            result = run_feature(payload)
            self.assertEqual(result, expected)
        self.assertEqual(run_feature(None), expected)
        self.assertEqual(run_feature([]), expected)
        lst = []
        run_feature(lst)
        self.assertEqual(lst, [])
        lst2 = ["a"]
        run_feature(lst2)
        self.assertEqual(lst2, ["a"])

    def test_valid_full_example(self):
        payload = {"confidence": 75.5, "status": "proposed", "strategy": "momentum", "title": " Momentum scan "}
        result = run_feature(payload)
        self.assertEqual(result, {
            "ok": True,
            "title": "Momentum scan",
            "strategy": "momentum",
            "confidence": 75.5,
            "status": "proposed",
            "issues": [],
        })
        self.assertEqual(payload, {"confidence": 75.5, "status": "proposed", "strategy": "momentum", "title": " Momentum scan "})

    def test_missing_optionals_default(self):
        result = run_feature({"title": "SOL breakout"})
        self.assertEqual(result, {
            "ok": True,
            "title": "SOL breakout",
            "strategy": "unknown",
            "confidence": 0,
            "status": "draft",
            "issues": [],
        })

    def test_all_invalid_fixed_order(self):
        payload = {"confidence": 999, "status": "live", "strategy": "scalping", "title": ""}
        result = run_feature(payload)
        self.assertEqual(result, {
            "ok": False,
            "title": "",
            "strategy": "unknown",
            "confidence": 0,
            "status": "draft",
            "issues": ["INVALID_TITLE", "INVALID_STRATEGY", "INVALID_CONFIDENCE", "INVALID_STATUS"],
        })
        self.assertEqual(payload, {"confidence": 999, "status": "live", "strategy": "scalping", "title": ""})

    def test_extra_keys_ignored(self):
        result = run_feature({"title": "ABC", "strategy": "breakout", "confidence": 10, "status": "approved", "extra": 1, "other": "x"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["title"], "ABC")
        self.assertEqual(result["strategy"], "breakout")
        self.assertEqual(result["confidence"], 10)
        self.assertEqual(result["status"], "approved")

    def test_title_validation(self):
        self.assertTrue(run_feature({"title": "a"})["ok"])
        self.assertTrue(run_feature({"title": "x" * 80})["ok"])
        self.assertEqual(run_feature({"title": "x" * 81})["issues"], ["INVALID_TITLE"])
        self.assertEqual(run_feature({"title": ""})["issues"], ["INVALID_TITLE"])
        self.assertEqual(run_feature({"title": "   "})["issues"], ["INVALID_TITLE"])
        self.assertEqual(run_feature({})["issues"], ["INVALID_TITLE"])
        self.assertEqual(run_feature({"title": None})["issues"], ["INVALID_TITLE"])
        self.assertEqual(run_feature({"title": 123})["issues"], ["INVALID_TITLE"])
        self.assertEqual(run_feature({"title": "bad!"})["issues"], ["INVALID_TITLE"])
        self.assertEqual(run_feature({"title": "a/b"})["issues"], ["INVALID_TITLE"])
        good = run_feature({"title": " A_9-1.2 Ok "})
        self.assertEqual(good["title"], "A_9-1.2 Ok")
        self.assertEqual(good["issues"], [])
        bad = run_feature({"title": "  ok!  "})
        self.assertEqual(bad["title"], "")
        self.assertIn("INVALID_TITLE", bad["issues"])

    def test_strategy_validation(self):
        for valid in ["momentum", "mean-reversion", "breakout"]:
            res = run_feature({"title": "Valid", "strategy": valid})
            self.assertEqual(res["strategy"], valid)
            self.assertEqual(res["issues"], [])
        res = run_feature({"title": "Valid", "strategy": "unknown"})
        self.assertEqual(res["strategy"], "unknown")
        self.assertEqual(res["issues"], ["INVALID_STRATEGY"])
        res = run_feature({"title": "Valid", "strategy": "scalping"})
        self.assertEqual(res["issues"], ["INVALID_STRATEGY"])
        self.assertEqual(res["strategy"], "unknown")
        self.assertFalse(res["ok"])
        self.assertEqual(run_feature({"title": "Valid", "strategy": None})["issues"], ["INVALID_STRATEGY"])
        self.assertEqual(run_feature({"title": "Valid", "strategy": ["x"]})["issues"], ["INVALID_STRATEGY"])

    def test_confidence_validation(self):
        for valid in [0, 100, 50, 75.5, 0.0, 100.0]:
            res = run_feature({"title": "Valid", "confidence": valid})
            self.assertEqual(res["confidence"], valid)
            self.assertEqual(res["issues"], [])
        for invalid in [-1, -0.1, 100.1, 999, "50", None, True, False, float("nan"), float("inf"), float("-inf")]:
            res = run_feature({"title": "Valid", "confidence": invalid})
            self.assertEqual(res["confidence"], 0)
            self.assertEqual(res["issues"], ["INVALID_CONFIDENCE"])
            self.assertFalse(res["ok"])
        self.assertTrue(math.isnan(float("nan")))

    def test_status_validation(self):
        for valid in ["draft", "proposed", "approved", "rejected"]:
            res = run_feature({"title": "Valid", "status": valid})
            self.assertEqual(res["status"], valid)
            self.assertEqual(res["issues"], [])
        res = run_feature({"title": "Valid", "status": "live"})
        self.assertEqual(res["status"], "draft")
        self.assertEqual(res["issues"], ["INVALID_STATUS"])
        self.assertEqual(run_feature({"title": "Valid", "status": None})["issues"], ["INVALID_STATUS"])

    def test_pure_deterministic_preserves_input(self):
        payload = {"title": " Momentum scan ", "strategy": "momentum", "confidence": 20, "status": "proposed", "extra": [1, 2]}
        first = run_feature(payload)
        second = run_feature(payload)
        self.assertEqual(payload, {"title": " Momentum scan ", "strategy": "momentum", "confidence": 20, "status": "proposed", "extra": [1, 2]})
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertIsNot(first["issues"], second["issues"])

    def test_total_no_raise(self):
        payloads = [{"title": "A", "strategy": ["x"], "confidence": {"v": 1}, "status": None}, {"title": "A\nB"}, {"title": "caf\u00e9"}]
        for payload in payloads:
            result = run_feature(payload)
            self.assertFalse(result["ok"])
            self.assertGreater(len(result["issues"]), 0)


if __name__ == "__main__":
    unittest.main()
