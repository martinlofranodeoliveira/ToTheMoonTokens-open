"""Independent public tests for synthetic offline health checker."""
import copy
import json
import unittest

from tothemoon_api.community.offline_health_check import run_feature


class TestOfflineHealthCheck(unittest.TestCase):
    def test_non_dict_payloads(self):
        expected = {"ok": False, "healthy": False, "mode": "synthetic", "provider_status": "offline", "issues": ["INVALID_PAYLOAD"]}
        for payload in (None, [], [1], [{"healthy": True}], "x", "", 0, 1, 3.14, True, False):
            before = copy.deepcopy(payload)
            result = run_feature(payload)
            self.assertEqual(result, expected)
            self.assertEqual(payload, before)

    def test_empty_dict(self):
        result = run_feature({})
        self.assertEqual(result, {"ok": True, "healthy": True, "mode": "synthetic", "provider_status": "offline", "issues": []})

    def test_healthy_false_degraded_missing_mode(self):
        payload = {"healthy": False, "provider_status": "degraded"}
        before = copy.deepcopy(payload)
        result = run_feature(payload)
        self.assertEqual(result, {"ok": True, "healthy": False, "mode": "synthetic", "provider_status": "degraded", "issues": []})
        self.assertEqual(payload, before)

    def test_all_invalid_fixed_order(self):
        payload = {"healthy": 1, "mode": "live", "provider_status": "online"}
        before = copy.deepcopy(payload)
        result = run_feature(payload)
        self.assertEqual(result, {"ok": False, "healthy": False, "mode": "synthetic", "provider_status": "offline", "issues": ["INVALID_HEALTHY", "INVALID_MODE", "INVALID_PROVIDER_STATUS"]})
        self.assertEqual(payload, before)

    def test_contract_examples(self):
        self.assertEqual(run_feature({"healthy": True, "mode": "synthetic", "provider_status": "offline"}), {"ok": True, "healthy": True, "mode": "synthetic", "provider_status": "offline", "issues": []})
        self.assertEqual(run_feature({"healthy": False, "provider_status": "degraded"}), {"ok": True, "healthy": False, "mode": "synthetic", "provider_status": "degraded", "issues": []})
        self.assertEqual(run_feature({"healthy": 1, "mode": "live", "provider_status": "online"}), {"ok": False, "healthy": False, "mode": "synthetic", "provider_status": "offline", "issues": ["INVALID_HEALTHY", "INVALID_MODE", "INVALID_PROVIDER_STATUS"]})

    def test_extra_keys_ignored(self):
        payload = {"healthy": True, "mode": "synthetic", "provider_status": "offline", "extra": 123, "other": {"a": 1}}
        before = copy.deepcopy(payload)
        result = run_feature(payload)
        self.assertEqual(result, {"ok": True, "healthy": True, "mode": "synthetic", "provider_status": "offline", "issues": []})
        self.assertEqual(payload, before)

    def test_deterministic_pure_preserves_input(self):
        payload = {"healthy": 1, "mode": "live", "provider_status": "online", "extra": "keep"}
        before = copy.deepcopy(payload)
        first = run_feature(payload)
        second = run_feature(payload)
        self.assertEqual(first, second)
        self.assertEqual(payload, before)
        empty = {}
        run_feature(empty)
        self.assertEqual(empty, {})

    def test_safe_json_limits(self):
        payloads = [{}, {"healthy": False, "provider_status": "degraded"}, {"healthy": 1, "mode": "live", "provider_status": "online"}]
        for payload in payloads:
            result = run_feature(payload)
            text = json.dumps(result)
            self.assertLess(len(text), 10000)
            parsed = json.loads(text)
            self.assertEqual(parsed, result)
            self.assertIsInstance(result["ok"], bool)
            self.assertIsInstance(result["healthy"], bool)
            self.assertIsInstance(result["mode"], str)
            self.assertIsInstance(result["provider_status"], str)
            self.assertIsInstance(result["issues"], list)


if __name__ == "__main__":
    unittest.main()
