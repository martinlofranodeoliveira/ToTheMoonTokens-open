import unittest

from tothemoon_api.community.offline_usage_check import run_feature


def _default_quota():
    return {"active_api_keys": 0, "simulate_order": 0, "token_audit": 0}


class TestOfflineUsageCheck(unittest.TestCase):
    def test_non_dict_payload(self):
        for bad in (None, 0, "x", [], [1], True, False, 3.5):
            with self.subTest(payload=bad):
                self.assertEqual(run_feature(bad), {
                    "ok": False,
                    "requests_today": 0,
                    "requests_month": 0,
                    "plan": "offline-demo",
                    "quota": _default_quota(),
                    "issues": ["INVALID_PAYLOAD"],
                })

    def test_missing_fields_default(self):
        self.assertEqual(run_feature({}), {
            "ok": True,
            "requests_today": 0,
            "requests_month": 0,
            "plan": "offline-demo",
            "quota": _default_quota(),
            "issues": [],
        })

    def test_valid_full_usage(self):
        payload = {
            "requests_today": 5,
            "requests_month": 20,
            "plan": "offline-demo",
            "quota": {"active_api_keys": 2, "simulate_order": 5, "token_audit": 5},
        }
        self.assertEqual(run_feature(payload), {
            "ok": True,
            "requests_today": 5,
            "requests_month": 20,
            "plan": "offline-demo",
            "quota": {"active_api_keys": 2, "simulate_order": 5, "token_audit": 5},
            "issues": [],
        })

    def test_boundary_counts_valid(self):
        result = run_feature({
            "requests_today": 1000000,
            "requests_month": 0,
            "quota": {"active_api_keys": 0, "simulate_order": 1000000, "token_audit": 1000000},
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["requests_today"], 1000000)
        self.assertEqual(result["quota"]["simulate_order"], 1000000)

    def test_invalid_requests_today(self):
        for bad in ("5", 1.0, True, False, None, -1, 1000001, [], {}):
            with self.subTest(value=bad):
                result = run_feature({"requests_today": bad})
                self.assertEqual(result["issues"], ["INVALID_REQUESTS_TODAY"])
                self.assertFalse(result["ok"])
                self.assertEqual(result["requests_today"], 0)
                self.assertEqual(result["requests_month"], 0)

    def test_invalid_requests_month(self):
        for bad in ("20", 2.5, True, False, None, -1, 1000001, [], {}):
            with self.subTest(value=bad):
                result = run_feature({"requests_month": bad})
                self.assertEqual(result["issues"], ["INVALID_REQUESTS_MONTH"])
                self.assertFalse(result["ok"])
                self.assertEqual(result["requests_month"], 0)

    def test_invalid_plan(self):
        for bad in ("pro", "OFFLINE-DEMO", "", None, 0, True):
            with self.subTest(plan=bad):
                result = run_feature({"plan": bad})
                self.assertEqual(result["issues"], ["INVALID_PLAN"])
                self.assertFalse(result["ok"])
                self.assertEqual(result["plan"], "offline-demo")

    def test_non_dict_quota(self):
        for bad in ([], [1], "x", 0, None, True, 5):
            with self.subTest(quota=bad):
                result = run_feature({"quota": bad})
                self.assertEqual(result["issues"], ["INVALID_QUOTA"])
                self.assertFalse(result["ok"])
                self.assertEqual(result["quota"], _default_quota())

    def test_bad_quota_values(self):
        bad_quotas = [
            {"active_api_keys": True, "simulate_order": 0, "token_audit": 0},
            {"active_api_keys": 0, "simulate_order": -1, "token_audit": 0},
            {"active_api_keys": 0, "simulate_order": 0, "token_audit": 1000001},
            {"active_api_keys": "0", "simulate_order": 0, "token_audit": 0},
            {"active_api_keys": 0, "simulate_order": 1.5, "token_audit": 0},
            {"active_api_keys": 0, "simulate_order": 0},
            {},
        ]
        for bad in bad_quotas:
            with self.subTest(quota=bad):
                result = run_feature({"quota": bad})
                self.assertEqual(result["issues"], ["INVALID_QUOTA_LIMIT"])
                self.assertFalse(result["ok"])
                self.assertEqual(result["quota"], _default_quota())

    def test_fixed_order_multiple_issues(self):
        result = run_feature({
            "requests_today": "5",
            "requests_month": -1,
            "plan": "pro",
            "quota": [],
        })
        self.assertEqual(result["issues"], [
            "INVALID_REQUESTS_TODAY",
            "INVALID_REQUESTS_MONTH",
            "INVALID_PLAN",
            "INVALID_QUOTA",
        ])
        self.assertFalse(result["ok"])
        self.assertEqual(result["requests_today"], 0)
        self.assertEqual(result["requests_month"], 0)
        self.assertEqual(result["plan"], "offline-demo")
        self.assertEqual(result["quota"], _default_quota())

    def test_fixed_order_with_quota_limit(self):
        result = run_feature({
            "requests_today": True,
            "requests_month": 1000001,
            "plan": "pro",
            "quota": {"active_api_keys": 0, "simulate_order": -1, "token_audit": 0},
        })
        self.assertEqual(result["issues"], [
            "INVALID_REQUESTS_TODAY",
            "INVALID_REQUESTS_MONTH",
            "INVALID_PLAN",
            "INVALID_QUOTA_LIMIT",
        ])
        self.assertFalse(result["ok"])

    def test_extra_keys_ignored_and_quota_normalized(self):
        payload = {
            "requests_today": 3,
            "requests_month": 4,
            "plan": "offline-demo",
            "quota": {
                "active_api_keys": 1,
                "simulate_order": 2,
                "token_audit": 3,
                "extra": 99,
            },
            "extra_top": "ignored",
        }
        result = run_feature(payload)
        self.assertTrue(result["ok"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["quota"], {"active_api_keys": 1, "simulate_order": 2, "token_audit": 3})
        self.assertEqual(payload, {
            "requests_today": 3,
            "requests_month": 4,
            "plan": "offline-demo",
            "quota": {
                "active_api_keys": 1,
                "simulate_order": 2,
                "token_audit": 3,
                "extra": 99,
            },
            "extra_top": "ignored",
        })

    def test_input_preserved_on_error(self):
        payload = {
            "requests_today": "bad",
            "requests_month": -5,
            "plan": "pro",
            "quota": {"active_api_keys": True, "simulate_order": 0, "token_audit": 0},
        }
        run_feature(payload)
        self.assertEqual(payload, {
            "requests_today": "bad",
            "requests_month": -5,
            "plan": "pro",
            "quota": {"active_api_keys": True, "simulate_order": 0, "token_audit": 0},
        })
        non_dict = [1, 2]
        run_feature(non_dict)
        self.assertEqual(non_dict, [1, 2])


if __name__ == "__main__":
    unittest.main()
