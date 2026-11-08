import json
import unittest
from tothemoon_api.community.offline_trace_link_check import run_feature
def _valid_payload():
    return {"requirement": {"objective": "offline dashboard fixture check", "version": "v1"}, "test_path": "lab_tests/test_offline_dashboard.py", "declared_result": {"passed": 10, "failed": 0}}
class TestOfflineTraceLinkCheck(unittest.TestCase):
    def test_non_dict_payload(self):
        for bad in (None, [], "x", 123, 4.5, True, (1,)):
            with self.subTest(bad=repr(bad)):
                got = run_feature(bad)
                self.assertEqual(got, {"ok": False, "linked": False, "objective": "", "test_path": "", "passed": 0, "failed": 0, "issues": ["INVALID_PAYLOAD"]})
    def test_valid_example(self):
        got = run_feature(_valid_payload())
        self.assertEqual(got, {"ok": True, "linked": True, "objective": "offline dashboard fixture check", "test_path": "lab_tests/test_offline_dashboard.py", "passed": 10, "failed": 0, "issues": []})
    def test_unexpected_failure_example(self):
        payload = {"requirement": {"objective": "quota guard check", "version": "v2"}, "test_path": "lab_tests/test_community_offline_quota_guard.py", "declared_result": {"passed": 5, "failed": 1}}
        got = run_feature(payload)
        self.assertEqual(got["issues"], ["UNEXPECTED_FAILURE"])
        self.assertFalse(got["ok"])
        self.assertFalse(got["linked"])
        self.assertEqual(got["passed"], 5)
        self.assertEqual(got["failed"], 1)
        self.assertEqual(got["objective"], "quota guard check")
    def test_multiple_invalid_example(self):
        payload = {"requirement": {}, "test_path": "bad", "declared_result": {}}
        got = run_feature(payload)
        self.assertEqual(got, {"ok": False, "linked": False, "objective": "", "test_path": "bad", "passed": 0, "failed": 0, "issues": ["INVALID_OBJECTIVE", "INVALID_VERSION", "INVALID_TEST_PATH", "INVALID_RESULT_COUNTS"]})
    def test_extra_keys_ignored(self):
        payload = _valid_payload()
        payload["extra"] = 123
        payload["requirement"]["extra"] = "x"
        payload["declared_result"]["extra"] = 1
        got = run_feature(payload)
        self.assertTrue(got["ok"])
        self.assertTrue(got["linked"])
        self.assertEqual(got["issues"], [])
    def test_objective_stripped_and_bounds(self):
        base = _valid_payload()
        base["requirement"]["objective"] = "  hello  "
        self.assertEqual(run_feature(base)["objective"], "hello")
        bad = _valid_payload()
        bad["requirement"]["objective"] = "   "
        self.assertIn("INVALID_OBJECTIVE", run_feature(bad)["issues"])
        bad2 = _valid_payload()
        bad2["requirement"]["objective"] = "x" * 201
        self.assertIn("INVALID_OBJECTIVE", run_feature(bad2)["issues"])
        ok2 = _valid_payload()
        ok2["requirement"]["objective"] = "x" * 200
        self.assertEqual(run_feature(ok2)["issues"], [])
    def test_version_bounds(self):
        bad = _valid_payload()
        bad["requirement"]["version"] = "  "
        self.assertIn("INVALID_VERSION", run_feature(bad)["issues"])
        bad2 = _valid_payload()
        bad2["requirement"]["version"] = "v" * 33
        self.assertIn("INVALID_VERSION", run_feature(bad2)["issues"])
        okv = _valid_payload()
        okv["requirement"]["version"] = "v" * 32
        self.assertEqual(run_feature(okv)["issues"], [])
    def test_requirement_not_dict(self):
        payload = {"requirement": "bad", "test_path": "lab_tests/test_a.py", "declared_result": {"passed": 1, "failed": 0}}
        got = run_feature(payload)
        self.assertEqual(got["issues"], ["INVALID_REQUIREMENT"])
        self.assertEqual(got["objective"], "")
    def test_path_middle_rules(self):
        good = _valid_payload()
        good["test_path"] = "lab_tests/test_a.py"
        self.assertEqual(run_feature(good)["issues"], [])
        for bad_path in ("bad", "lab_tests/test_A.py", "lab_tests/test_a-b.py", "lab_tests/test_.py", "lab_tests/test_a.txt", "tests/test_a.py", 123, None):
            with self.subTest(bad_path=repr(bad_path)):
                p = _valid_payload()
                p["test_path"] = bad_path
                got = run_feature(p)
                self.assertIn("INVALID_TEST_PATH", got["issues"])
        p2 = _valid_payload()
        p2["test_path"] = "lab_tests/test_" + "a" * 61 + ".py"
        self.assertIn("INVALID_TEST_PATH", run_feature(p2)["issues"])
    def test_bool_and_range_rejected_once(self):
        for passed, failed in ((True, 0), (0, False), ("1", 0), (0, -1), (0, 1000001), (1.0, 0)):
            with self.subTest(passed=repr(passed), failed=repr(failed)):
                p = _valid_payload()
                p["declared_result"] = {"passed": passed, "failed": failed}
                got = run_feature(p)
                self.assertEqual(got["issues"].count("INVALID_RESULT_COUNTS"), 1)
                self.assertNotIn("UNEXPECTED_FAILURE", got["issues"])
    def test_fixed_order(self):
        payload = {"requirement": "bad", "test_path": "bad", "declared_result": "bad"}
        self.assertEqual(run_feature(payload)["issues"], ["INVALID_REQUIREMENT", "INVALID_TEST_PATH", "INVALID_DECLARED_RESULT"])
        payload2 = {"requirement": {}, "test_path": "bad", "declared_result": {"passed": "x", "failed": "y"}}
        self.assertEqual(run_feature(payload2)["issues"], ["INVALID_OBJECTIVE", "INVALID_VERSION", "INVALID_TEST_PATH", "INVALID_RESULT_COUNTS"])
    def test_partial_valid_counts_echo(self):
        p = _valid_payload()
        p["declared_result"] = {"passed": 7, "failed": "bad"}
        got = run_feature(p)
        self.assertEqual(got["passed"], 7)
        self.assertEqual(got["failed"], 0)
        self.assertEqual(got["issues"], ["INVALID_RESULT_COUNTS"])
    def test_non_str_echo(self):
        p = {"requirement": {"objective": 123, "version": 456}, "test_path": 789, "declared_result": {"passed": 0, "failed": 0}}
        got = run_feature(p)
        self.assertEqual(got["objective"], "")
        self.assertEqual(got["test_path"], "")
    def test_preserves_input_and_deterministic(self):
        payload = _valid_payload()
        snapshot = {"requirement": {"objective": "offline dashboard fixture check", "version": "v1"}, "test_path": "lab_tests/test_offline_dashboard.py", "declared_result": {"passed": 10, "failed": 0}}
        first = run_feature(payload)
        second = run_feature(payload)
        self.assertEqual(payload, snapshot)
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        json.dumps(first)
        self.assertEqual(set(first.keys()), {"ok", "linked", "objective", "test_path", "passed", "failed", "issues"})
if __name__ == "__main__":
    unittest.main()
