import json
import unittest
from tothemoon_api.community.offline_dashboard_check import run_feature


class TestOfflineDashboardCheck(unittest.TestCase):
    def test_non_dict_payload(self):
        for bad in (None, [], "x", 42, 3.5, True):
            with self.subTest(payload=bad):
                out = run_feature(bad)
                self.assertEqual(out, {
                    "ok": False,
                    "email": "",
                    "org_id": 0,
                    "requests_this_month": 0,
                    "issues": ["INVALID_PAYLOAD"],
                })

    def test_missing_fields_default_without_issues(self):
        out = run_feature({})
        self.assertEqual(out, {
            "ok": True,
            "email": "synthetic@example.invalid",
            "org_id": 0,
            "requests_this_month": 0,
            "issues": [],
        })
        out2 = run_feature({"org_id": 5})
        self.assertEqual(out2["email"], "synthetic@example.invalid")
        self.assertEqual(out2["org_id"], 5)
        self.assertEqual(out2["requests_this_month"], 0)
        self.assertEqual(out2["issues"], [])
        self.assertTrue(out2["ok"])

    def test_valid_full_input_echoes(self):
        payload = {
            "email": "synthetic@example.invalid",
            "org_id": 0,
            "requests_this_month": 0,
        }
        out = run_feature(payload)
        self.assertEqual(out, {
            "ok": True,
            "email": "synthetic@example.invalid",
            "org_id": 0,
            "requests_this_month": 0,
            "issues": [],
        })
        payload2 = {"email": "  dev@example.com  ", "org_id": 7, "requests_this_month": 12}
        out2 = run_feature(payload2)
        self.assertEqual(out2["email"], "dev@example.com")
        self.assertEqual(out2["org_id"], 7)
        self.assertEqual(out2["requests_this_month"], 12)
        self.assertEqual(out2["issues"], [])
        self.assertTrue(out2["ok"])

    def test_invalid_all_fields_order_and_zeroed(self):
        out = run_feature({"email": "bad", "org_id": -1, "requests_this_month": "many"})
        self.assertEqual(out, {
            "ok": False,
            "email": "",
            "org_id": 0,
            "requests_this_month": 0,
            "issues": ["INVALID_EMAIL", "INVALID_ORG_ID", "INVALID_REQUESTS_THIS_MONTH"],
        })

    def test_each_invalid_code_individually(self):
        bad_email = run_feature({"email": "bad", "org_id": 1, "requests_this_month": 1})
        self.assertEqual(bad_email["issues"], ["INVALID_EMAIL"])
        self.assertEqual(bad_email["email"], "")
        self.assertFalse(bad_email["ok"])
        bad_org = run_feature({"email": "a@b.cd", "org_id": True, "requests_this_month": 1})
        self.assertEqual(bad_org["issues"], ["INVALID_ORG_ID"])
        self.assertEqual(bad_org["org_id"], 0)
        bad_req = run_feature({"email": "a@b.cd", "org_id": 1, "requests_this_month": 1000001})
        self.assertEqual(bad_req["issues"], ["INVALID_REQUESTS_THIS_MONTH"])
        self.assertEqual(bad_req["requests_this_month"], 0)
        self.assertEqual(run_feature({"email": "a@b@c.cd"})["issues"], ["INVALID_EMAIL"])
        self.assertEqual(run_feature({"email": "user@example"})["issues"], ["INVALID_EMAIL"])
        self.assertEqual(run_feature({"email": 123})["issues"], ["INVALID_EMAIL"])

    def test_extra_keys_ignored_and_not_mutated_deterministic(self):
        payload = {
            "email": "dev@example.com",
            "org_id": 3,
            "requests_this_month": 4,
            "extra": "ignore-me",
        }
        snapshot = json.loads(json.dumps(payload))
        first = run_feature(payload)
        second = run_feature(payload)
        self.assertEqual(payload, snapshot)
        self.assertEqual(first, second)
        self.assertNotIn("extra", first)
        self.assertEqual(first["email"], "dev@example.com")
        self.assertEqual(first["org_id"], 3)
        self.assertEqual(first["requests_this_month"], 4)
        self.assertEqual(first["issues"], [])
        self.assertTrue(first["ok"])

    def test_safe_json_values(self):
        cases = [
            {},
            {"email": "dev@example.com", "org_id": 1000000, "requests_this_month": 1000000},
            {"email": "bad", "org_id": -5, "requests_this_month": None},
            "not-a-dict",
        ]
        for case in cases:
            with self.subTest(case=case):
                out = run_feature(case)
                back = json.loads(json.dumps(out))
                self.assertEqual(back, out)
                self.assertIsInstance(out["email"], str)
                self.assertLessEqual(len(out["email"]), 80)
                self.assertIsInstance(out["org_id"], int)
                self.assertIsInstance(out["requests_this_month"], int)
                self.assertGreaterEqual(out["org_id"], 0)
                self.assertLessEqual(out["org_id"], 1000000)
                self.assertGreaterEqual(out["requests_this_month"], 0)
                self.assertLessEqual(out["requests_this_month"], 1000000)
                self.assertEqual(out["ok"], out["issues"] == [])
