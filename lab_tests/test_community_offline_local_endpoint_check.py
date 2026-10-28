import json
import math
import unittest
from tothemoon_api.community.offline_local_endpoint_check import run_feature


def _depth(value):
    if isinstance(value, dict):
        if len(value) == 0:
            return 1
        best = 0
        for key in value:
            nested = _depth(value[key])
            if nested > best:
                best = nested
        return best + 1
    if isinstance(value, list):
        if len(value) == 0:
            return 1
        best = 0
        for item in value:
            nested = _depth(item)
            if nested > best:
                best = nested
        return best + 1
    return 1


class TestOfflineLocalEndpointCheck(unittest.TestCase):
    def test_non_dict_single_issue(self):
        result = run_feature("offline")
        self.assertEqual(result, {"api_host": "", "api_port": 0, "web_port": 0, "issues": ["INVALID_PAYLOAD"], "ok": False})

    def test_non_dict_variants(self):
        cases = [None, 0, 8010, True, False, 3.14, "", [], [1]]
        for payload in cases:
            with self.subTest(payload=payload):
                before = json.dumps(payload)
                result = run_feature(payload)
                self.assertEqual(result["api_host"], "")
                self.assertEqual(result["api_port"], 0)
                self.assertEqual(result["web_port"], 0)
                self.assertEqual(result["issues"], ["INVALID_PAYLOAD"])
                self.assertFalse(result["ok"])
                self.assertEqual(json.dumps(payload), before)

    def test_valid(self):
        payload = {"api_host": "127.0.0.1", "api_port": 8010, "web_port": 4173}
        before = json.dumps(payload, sort_keys=True)
        result = run_feature(payload)
        self.assertEqual(result, {"api_host": "127.0.0.1", "api_port": 8010, "web_port": 4173, "issues": [], "ok": True})
        self.assertEqual(json.dumps(payload, sort_keys=True), before)

    def test_all_invalid(self):
        result = run_feature({"api_host": "0.0.0.0", "api_port": 8000, "web_port": 3000})
        self.assertEqual(result, {"api_host": "", "api_port": 0, "web_port": 0, "issues": ["INVALID_API_HOST", "INVALID_API_PORT", "INVALID_WEB_PORT"], "ok": False})

    def test_missing_fields(self):
        result = run_feature({})
        self.assertEqual(result["api_host"], "")
        self.assertEqual(result["api_port"], 0)
        self.assertEqual(result["web_port"], 0)
        self.assertEqual(result["issues"], ["INVALID_API_HOST", "INVALID_API_PORT", "INVALID_WEB_PORT"])
        self.assertFalse(result["ok"])

    def test_invalid_host_variants(self):
        bad_hosts = ["localhost", "", "127.0.0.2", " 127.0.0.1", 123, None, True, False, 8010]
        for bad in bad_hosts:
            with self.subTest(bad=bad):
                result = run_feature({"api_host": bad, "api_port": 8010, "web_port": 4173})
                self.assertEqual(result["api_host"], "")
                self.assertEqual(result["api_port"], 8010)
                self.assertEqual(result["web_port"], 4173)
                self.assertEqual(result["issues"], ["INVALID_API_HOST"])
                self.assertFalse(result["ok"])

    def test_invalid_port_variants(self):
        bad_api = [True, False, "8010", 8010.0, None, 8009, 8011, 0]
        for bad in bad_api:
            with self.subTest(bad=bad):
                result = run_feature({"api_host": "127.0.0.1", "api_port": bad, "web_port": 4173})
                self.assertEqual(result["api_port"], 0)
                self.assertIn("INVALID_API_PORT", result["issues"])
                self.assertFalse(result["ok"])
        bad_web = [True, False, "4173", 4173.0, None, 4172, 4174, 80]
        for bad in bad_web:
            with self.subTest(bad=bad):
                result = run_feature({"api_host": "127.0.0.1", "api_port": 8010, "web_port": bad})
                self.assertEqual(result["web_port"], 0)
                self.assertIn("INVALID_WEB_PORT", result["issues"])
                self.assertFalse(result["ok"])

    def test_extra_keys_ignored_and_order(self):
        payload = {"api_host": "example.com", "api_port": "x", "web_port": None, "extra": 1}
        result = run_feature(payload)
        self.assertEqual(result["issues"], ["INVALID_API_HOST", "INVALID_API_PORT", "INVALID_WEB_PORT"])
        self.assertFalse(result["ok"])
        valid_extra = {"api_host": "127.0.0.1", "api_port": 8010, "web_port": 4173, "extra": [1, 2]}
        result2 = run_feature(valid_extra)
        self.assertEqual(result2["issues"], [])
        self.assertTrue(result2["ok"])
        self.assertEqual(result2["api_host"], "127.0.0.1")
        self.assertEqual(result2["api_port"], 8010)
        self.assertEqual(result2["web_port"], 4173)

    def test_pure_deterministic_no_mutation(self):
        payload = {"api_host": "127.0.0.1", "api_port": 8010, "web_port": 4173, "extra": {"a": 1}}
        before = json.dumps(payload, sort_keys=True)
        first = run_feature(payload)
        second = run_feature(payload)
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(payload, sort_keys=True), before)

    def test_safe_json_domain(self):
        examples = [{"api_host": "127.0.0.1", "api_port": 8010, "web_port": 4173}, {"api_host": "0.0.0.0", "api_port": 8000, "web_port": 3000}, "offline"]
        for payload in examples:
            with self.subTest(payload=payload):
                result = run_feature(payload)
                blob_in = json.dumps(payload)
                blob_out = json.dumps(result)
                self.assertLessEqual(len(blob_in.encode("utf-8")), 64 * 1024)
                self.assertLessEqual(len(blob_out.encode("utf-8")), 64 * 1024)
                self.assertLessEqual(_depth(payload), 12)
                self.assertLessEqual(_depth(result), 12)
                self.assertTrue(math.isfinite(float(result["api_port"])))
                self.assertTrue(math.isfinite(float(result["web_port"])))


if __name__ == "__main__":
    unittest.main()
