import unittest

try:
    from tothemoon_api.community.offline_api_key_check import run_feature
except ImportError:
    from services.api.src.tothemoon_api.community.offline_api_key_check import run_feature


class TestOfflineApiKeyCheck(unittest.TestCase):
    def test_contract_example_full(self):
        result = run_feature({"active": True, "label": " dev-key_1 ", "scope": "write"})
        self.assertEqual(result, {"active": True, "issues": [], "label": "dev-key_1", "ok": True, "scope": "write"})

    def test_contract_example_minimal(self):
        result = run_feature({"label": "a"})
        self.assertEqual(result, {"active": False, "issues": [], "label": "a", "ok": True, "scope": "read"})

    def test_contract_example_all_invalid(self):
        result = run_feature({"active": 1, "label": "", "scope": "super"})
        self.assertEqual(result, {"active": False, "issues": ["INVALID_LABEL", "INVALID_SCOPE", "INVALID_ACTIVE"], "label": "", "ok": False, "scope": "read"})

    def test_non_dict_payload(self):
        cases = [None, True, False, 0, 123, 1.5, "label", "", [], ["a"], (), ("a",), {"a", "b"}]
        for payload in cases:
            with self.subTest(payload=repr(payload)):
                result = run_feature(payload)
                self.assertEqual(result, {"ok": False, "label": "", "scope": "read", "active": False, "issues": ["INVALID_PAYLOAD"]})
        payload = [{"label": "a"}]
        snapshot = [{"label": "a"}]
        result = run_feature(payload)
        self.assertEqual(result, {"ok": False, "label": "", "scope": "read", "active": False, "issues": ["INVALID_PAYLOAD"]})
        self.assertEqual(payload, snapshot)
        self.assertEqual(run_feature(None), {"ok": False, "label": "", "scope": "read", "active": False, "issues": ["INVALID_PAYLOAD"]})
        self.assertEqual(run_feature(123), {"ok": False, "label": "", "scope": "read", "active": False, "issues": ["INVALID_PAYLOAD"]})

    def test_empty_dict_is_dict_not_payload_error(self):
        result = run_feature({})
        self.assertEqual(result, {"ok": False, "label": "", "scope": "read", "active": False, "issues": ["INVALID_LABEL"]})

    def test_label_valid(self):
        self.assertEqual(run_feature({"label": " dev-key_1 "})["label"], "dev-key_1")
        self.assertEqual(run_feature({"label": "a"})["label"], "a")
        self.assertTrue(run_feature({"label": "A Z a z 0 9 _ -"})["ok"])
        self.assertTrue(run_feature({"label": "x" * 30})["ok"])
        self.assertTrue(run_feature({"label": "  abc  "})["ok"])

    def test_label_invalid_length(self):
        self.assertEqual(run_feature({"label": ""})["issues"], ["INVALID_LABEL"])
        self.assertEqual(run_feature({"label": "   "})["issues"], ["INVALID_LABEL"])
        self.assertEqual(run_feature({"label": "x" * 31})["issues"], ["INVALID_LABEL"])
        self.assertEqual(run_feature({})["issues"], ["INVALID_LABEL"])
        self.assertEqual(run_feature({"label": ""})["label"], "")
        self.assertEqual(run_feature({"label": "  " + "y" * 31 + "  "})["issues"], ["INVALID_LABEL"])

    def test_label_invalid_chars_and_types(self):
        for bad in ["a!", "a.b", "a@b", "key#1", "a/b", "a:b", "a\nb"]:
            with self.subTest(bad=bad):
                result = run_feature({"label": bad})
                self.assertEqual(result["label"], "")
                self.assertIn("INVALID_LABEL", result["issues"])
        self.assertIn("INVALID_LABEL", run_feature({"label": "caf\u00e9"})["issues"])
        for bad in [None, 0, 1, True, False, [], {}]:
            with self.subTest(bad=repr(bad)):
                result = run_feature({"label": bad})
                self.assertEqual(result["label"], "")
                self.assertIn("INVALID_LABEL", result["issues"])

    def test_scope_defaults_and_echo(self):
        self.assertEqual(run_feature({"label": "abc"}), {"ok": True, "label": "abc", "scope": "read", "active": False, "issues": []})
        for scope in ["read", "write", "audit"]:
            with self.subTest(scope=scope):
                result = run_feature({"label": "abc", "scope": scope})
                self.assertEqual(result["scope"], scope)
                self.assertNotIn("INVALID_SCOPE", result["issues"])

    def test_scope_invalid(self):
        for bad in ["super", "READ", "Read", "", " read", "read ", None, 0, 1, True, False, [], {}]:
            with self.subTest(bad=repr(bad)):
                result = run_feature({"label": "abc", "scope": bad})
                self.assertEqual(result["scope"], "read")
                self.assertIn("INVALID_SCOPE", result["issues"])

    def test_active_defaults_and_echo(self):
        self.assertEqual(run_feature({"label": "abc"})["active"], False)
        self.assertEqual(run_feature({"label": "abc", "active": True})["active"], True)
        self.assertEqual(run_feature({"label": "abc", "active": False})["active"], False)
        self.assertEqual(run_feature({"label": "abc", "active": True})["issues"], [])

    def test_active_invalid(self):
        for bad in [1, 0, "true", "false", None, 1.0, 0.0, [], {}, ""]:
            with self.subTest(bad=repr(bad)):
                result = run_feature({"label": "abc", "active": bad})
                self.assertEqual(result["active"], False)
                self.assertIn("INVALID_ACTIVE", result["issues"])

    def test_issues_fixed_order(self):
        self.assertEqual(run_feature({"label": "", "scope": "bad", "active": 1})["issues"], ["INVALID_LABEL", "INVALID_SCOPE", "INVALID_ACTIVE"])
        self.assertEqual(run_feature({"label": "ok_key", "scope": "bad", "active": 1})["issues"], ["INVALID_SCOPE", "INVALID_ACTIVE"])
        self.assertEqual(run_feature({"label": "", "scope": "read", "active": False})["issues"], ["INVALID_LABEL"])
        good = run_feature({"label": "ok", "scope": "read", "active": True})
        self.assertEqual(good["issues"], [])
        self.assertTrue(good["ok"])
        bad = run_feature({"label": "", "scope": "bad", "active": 1})
        self.assertFalse(bad["ok"])

    def test_extra_keys_ignored_and_preserved(self):
        payload = {"label": " abc ", "scope": "write", "active": True, "extra": 123, "other": {"x": 1}}
        result = run_feature(payload)
        self.assertEqual(payload, {"label": " abc ", "scope": "write", "active": True, "extra": 123, "other": {"x": 1}})
        self.assertEqual(result, {"ok": True, "label": "abc", "scope": "write", "active": True, "issues": []})

    def test_deterministic_and_keys(self):
        payload = {"label": " dev-key_1 ", "scope": "audit", "active": False}
        first = run_feature(payload)
        second = run_feature(payload)
        self.assertEqual(first, second)
        self.assertEqual(payload, {"label": " dev-key_1 ", "scope": "audit", "active": False})
        self.assertEqual(sorted(list(first)), ["active", "issues", "label", "ok", "scope"])


if __name__ == "__main__":
    unittest.main()
