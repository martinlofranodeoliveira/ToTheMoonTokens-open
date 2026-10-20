import unittest
from tothemoon_api.community.offline_quota_guard import run_feature


class TestOfflineQuotaGuard(unittest.TestCase):
    def test_non_object_payload(self):
        expected = {"ok": False, "allowed": False, "action": "unknown", "limit": 0, "used": 0, "requested": 1, "remaining": 0, "issues": ["INVALID_PAYLOAD"]}
        for bad in (None, True, False, 0, 42, 3.14, "payload", ["a"], ("a",)):
            with self.subTest(payload=bad):
                self.assertEqual(run_feature(bad), expected)

    def test_valid_within_limit(self):
        payload = {"action": "simulate_order", "quota": {"active_api_keys": 2, "simulate_order": 5, "token_audit": 5}, "requested": 1, "usage": {"active_api_keys": 0, "simulate_order": 2, "token_audit": 0}}
        expected = {"action": "simulate_order", "allowed": True, "issues": [], "limit": 5, "ok": True, "remaining": 3, "requested": 1, "used": 2}
        self.assertEqual(run_feature(payload), expected)

    def test_valid_exceeding_limit(self):
        payload = {"action": "token_audit", "quota": {"active_api_keys": 2, "simulate_order": 5, "token_audit": 1}, "requested": 1, "usage": {"active_api_keys": 0, "simulate_order": 0, "token_audit": 1}}
        expected = {"action": "token_audit", "allowed": False, "issues": [], "limit": 1, "ok": True, "remaining": 0, "requested": 1, "used": 1}
        self.assertEqual(run_feature(payload), expected)

    def test_unknown_action_example(self):
        payload = {"action": "unknown_action", "quota": {}, "usage": {}}
        expected = {"action": "unknown", "allowed": False, "issues": ["INVALID_ACTION", "INVALID_QUOTA_LIMIT", "INVALID_USAGE_VALUE"], "limit": 0, "ok": False, "remaining": 0, "requested": 1, "used": 0}
        self.assertEqual(run_feature(payload), expected)

    def test_invalid_action_forces_allowed_false(self):
        payload = {"action": "bad", "quota": {"bad": 10}, "usage": {"bad": 2}, "requested": 1}
        out = run_feature(payload)
        self.assertEqual(out["action"], "unknown")
        self.assertEqual(out["issues"], ["INVALID_ACTION"])
        self.assertFalse(out["ok"])
        self.assertFalse(out["allowed"])
        self.assertEqual(out["limit"], 10)
        self.assertEqual(out["used"], 2)
        self.assertEqual(out["remaining"], 8)

    def test_invalid_quota_limit(self):
        for bad_limit in (-1, 1000001, 1.5, "5", None, True, False):
            with self.subTest(limit=bad_limit):
                payload = {"action": "simulate_order", "quota": {"simulate_order": bad_limit}, "usage": {"simulate_order": 0}, "requested": 1}
                out = run_feature(payload)
                self.assertIn("INVALID_QUOTA_LIMIT", out["issues"])
                self.assertEqual(out["limit"], 0)
                self.assertEqual(out["remaining"], 0)
                self.assertFalse(out["allowed"])
                self.assertFalse(out["ok"])

    def test_invalid_quota_not_object(self):
        payload = {"action": "simulate_order", "quota": None, "usage": {"simulate_order": 0}}
        out = run_feature(payload)
        self.assertEqual(out["issues"], ["INVALID_QUOTA"])
        self.assertEqual(out["limit"], 0)
        self.assertEqual(out["remaining"], 0)
        self.assertFalse(out["allowed"])

    def test_invalid_usage_value(self):
        payload = {"action": "active_api_keys", "quota": {"active_api_keys": 10}, "usage": {"active_api_keys": -5}}
        out = run_feature(payload)
        self.assertIn("INVALID_USAGE_VALUE", out["issues"])
        self.assertEqual(out["used"], 0)
        self.assertEqual(out["remaining"], 0)
        self.assertFalse(out["allowed"])

    def test_invalid_usage_not_object(self):
        payload = {"action": "simulate_order", "quota": {"simulate_order": 5}, "usage": None}
        out = run_feature(payload)
        self.assertEqual(out["issues"], ["INVALID_USAGE"])
        self.assertFalse(out["allowed"])

    def test_invalid_requested(self):
        for bad_req in (0, -1, 1000001, 1.5, "1", None, True):
            with self.subTest(requested=bad_req):
                payload = {"action": "simulate_order", "quota": {"simulate_order": 5}, "usage": {"simulate_order": 0}, "requested": bad_req}
                out = run_feature(payload)
                self.assertIn("INVALID_REQUESTED", out["issues"])
                self.assertEqual(out["requested"], 1)
                self.assertFalse(out["allowed"])
                self.assertFalse(out["ok"])

    def test_requested_defaults_to_one(self):
        payload = {"action": "simulate_order", "quota": {"simulate_order": 5}, "usage": {"simulate_order": 0}}
        out = run_feature(payload)
        self.assertEqual(out["requested"], 1)
        self.assertEqual(out["issues"], [])
        self.assertTrue(out["ok"])
        self.assertTrue(out["allowed"])

    def test_fixed_order_multiple_issues(self):
        payload = {"action": "bad", "quota": {}, "usage": {}, "requested": 0}
        out = run_feature(payload)
        self.assertEqual(out["issues"], ["INVALID_ACTION", "INVALID_QUOTA_LIMIT", "INVALID_USAGE_VALUE", "INVALID_REQUESTED"])
        self.assertFalse(out["allowed"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["requested"], 1)
        self.assertEqual(out["remaining"], 0)

    def test_deterministic_and_preserves_input(self):
        payload = {"action": "simulate_order", "quota": {"simulate_order": 5}, "usage": {"simulate_order": 2}, "requested": 1}
        first = run_feature(payload)
        second = run_feature({"action": "simulate_order", "quota": {"simulate_order": 5}, "usage": {"simulate_order": 2}, "requested": 1})
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertIsNot(first["issues"], second["issues"])
        self.assertEqual(payload, {"action": "simulate_order", "quota": {"simulate_order": 5}, "usage": {"simulate_order": 2}, "requested": 1})


if __name__ == "__main__":
    unittest.main()
