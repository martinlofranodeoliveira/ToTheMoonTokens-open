import unittest
from tothemoon_api.community.offline_api_key_bucket_check import run_feature


class TestOfflineApiKeyBucketCheck(unittest.TestCase):
    def test_non_dict_payload(self):
        for bad in (None, [], "x", 0, 1.5, True, (1,)):
            with self.subTest(payload=bad):
                self.assertEqual(
                    run_feature(bad),
                    {
                        "ok": False,
                        "selector_bucket": 0,
                        "legacy_bucket": 0,
                        "include_legacy": False,
                        "hash_checks": 0,
                        "selector_authenticates": False,
                        "issues": ["INVALID_PAYLOAD"],
                    },
                )

    def test_empty_dict_defaults(self):
        self.assertEqual(
            run_feature({}),
            {
                "ok": True,
                "selector_bucket": 0,
                "legacy_bucket": 0,
                "include_legacy": False,
                "hash_checks": 0,
                "selector_authenticates": False,
                "issues": [],
            },
        )

    def test_contract_example_include_true(self):
        self.assertEqual(
            run_feature({"selector_bucket": 2, "legacy_bucket": 1, "include_legacy": True}),
            {
                "ok": True,
                "selector_bucket": 2,
                "legacy_bucket": 1,
                "include_legacy": True,
                "hash_checks": 3,
                "selector_authenticates": False,
                "issues": [],
            },
        )

    def test_contract_example_include_false(self):
        self.assertEqual(
            run_feature({"selector_bucket": 0, "legacy_bucket": 5, "include_legacy": False}),
            {
                "ok": True,
                "selector_bucket": 0,
                "legacy_bucket": 5,
                "include_legacy": False,
                "hash_checks": 0,
                "selector_authenticates": False,
                "issues": [],
            },
        )

    def test_contract_example_all_invalid(self):
        self.assertEqual(
            run_feature({"selector_bucket": "2", "legacy_bucket": -1, "include_legacy": 1}),
            {
                "ok": False,
                "selector_bucket": 0,
                "legacy_bucket": 0,
                "include_legacy": False,
                "hash_checks": 0,
                "selector_authenticates": False,
                "issues": [
                    "INVALID_SELECTOR_BUCKET",
                    "INVALID_LEGACY_BUCKET",
                    "INVALID_INCLUDE_LEGACY",
                ],
            },
        )

    def test_hash_checks_include_legacy(self):
        with_true = run_feature({"selector_bucket": 10, "legacy_bucket": 7, "include_legacy": True})
        self.assertEqual(with_true["hash_checks"], 17)
        self.assertTrue(with_true["ok"])
        without = run_feature({"selector_bucket": 10, "legacy_bucket": 7, "include_legacy": False})
        self.assertEqual(without["hash_checks"], 10)
        self.assertTrue(without["ok"])

    def test_boundaries_valid(self):
        result = run_feature({"selector_bucket": 0, "legacy_bucket": 1000000, "include_legacy": True})
        self.assertEqual(result["issues"], [])
        self.assertTrue(result["ok"])
        self.assertEqual(result["hash_checks"], 1000000)
        top = run_feature({"selector_bucket": 1000000, "legacy_bucket": 1000000, "include_legacy": True})
        self.assertEqual(top["hash_checks"], 2000000)
        self.assertTrue(top["ok"])

    def test_boundaries_invalid(self):
        result = run_feature({"selector_bucket": 1000001, "legacy_bucket": -1})
        self.assertEqual(result["issues"], ["INVALID_SELECTOR_BUCKET", "INVALID_LEGACY_BUCKET"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["selector_bucket"], 0)
        self.assertEqual(result["legacy_bucket"], 0)
        self.assertEqual(result["hash_checks"], 0)

    def test_bool_excluded_from_buckets(self):
        result = run_feature({"selector_bucket": True, "legacy_bucket": False})
        self.assertEqual(result["issues"], ["INVALID_SELECTOR_BUCKET", "INVALID_LEGACY_BUCKET"])
        self.assertFalse(result["ok"])

    def test_float_and_none_invalid(self):
        result = run_feature({"selector_bucket": 2.0, "legacy_bucket": None})
        self.assertEqual(result["issues"], ["INVALID_SELECTOR_BUCKET", "INVALID_LEGACY_BUCKET"])

    def test_extra_keys_ignored_and_missing_defaults(self):
        result = run_feature({"selector_bucket": 4, "extra": 999})
        self.assertEqual(result["selector_bucket"], 4)
        self.assertEqual(result["legacy_bucket"], 0)
        self.assertEqual(result["include_legacy"], False)
        self.assertEqual(result["hash_checks"], 4)
        self.assertEqual(result["issues"], [])
        self.assertTrue(result["ok"])

    def test_selector_authenticates_always_false(self):
        for payload in ({}, {"selector_bucket": 5}, {"selector_bucket": 5, "legacy_bucket": 5, "include_legacy": True}, {"selector_bucket": "bad"}):
            self.assertFalse(run_feature(payload)["selector_authenticates"])

    def test_preserves_input_and_deterministic(self):
        payload = {"selector_bucket": 3, "legacy_bucket": 4, "include_legacy": True}
        snapshot = dict(payload)
        first = run_feature(payload)
        second = run_feature(payload)
        self.assertEqual(payload, snapshot)
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertIsNot(first["issues"], second["issues"])


if __name__ == "__main__":
    unittest.main()
