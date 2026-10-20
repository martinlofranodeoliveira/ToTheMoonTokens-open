"""Public regression tests for synthetic offline dashboard fixtures."""
import contextlib
import io
import json
import unittest
from tothemoon_api.offline_dashboard import build_offline_dashboard, main

_SCENARIO_ERROR = "INVALID_OFFLINE_DASHBOARD_SCENARIO"
_ARGS_ERROR = "INVALID_OFFLINE_DASHBOARD_ARGUMENTS"


def _run(argv):
    out = io.StringIO()
    err = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        with contextlib.redirect_stderr(err):
            try:
                ret = main(argv)
            except SystemExit as exc:
                code = exc.code
            else:
                code = ret
    return code, out.getvalue(), err.getvalue()


class TestOfflineDashboard(unittest.TestCase):
    def test_empty_exact(self):
        snap = build_offline_dashboard("empty")
        self.assertEqual(
            sorted(snap.keys()),
            sorted(["schema_version", "synthetic", "scenario", "health", "dashboard", "usage", "collections"]),
        )
        self.assertIs(type(snap["schema_version"]), int)
        self.assertEqual(snap["schema_version"], 1)
        self.assertIs(snap["synthetic"], True)
        self.assertEqual(snap["scenario"], "empty")
        self.assertEqual(
            snap["health"],
            {
                "ok": True,
                "app": "ToTheMoonTokens offline fixture",
                "mode": "synthetic",
                "orderSubmissionEnabled": False,
                "autonomous_payments_enabled": False,
                "wallets_loaded": False,
                "providers": {"synthetic-data": {"status": "offline", "latency_ms": None, "last_error": None}},
            },
        )
        self.assertEqual(
            snap["dashboard"],
            {
                "email": "synthetic@example.invalid",
                "org_id": 0,
                "plan": "offline-demo",
                "requests_this_month": 0,
                "simulated_volume_usd": 0.0,
                "active_api_keys": 0,
                "total_simulated_trades": 0,
                "total_volume_usd": 0.0,
                "total_fees_usd": 0.0,
                "realized_pnl_usd": 0.0,
                "unrealized_pnl_usd": 0.0,
                "last_30_days_chart_points": [],
            },
        )
        self.assertEqual(
            snap["usage"],
            {
                "requests_today": 0,
                "requests_month": 0,
                "plan": "offline-demo",
                "quota": {"simulate_order": 0, "token_audit": 0, "active_api_keys": 0},
            },
        )
        self.assertEqual(
            snap["collections"],
            {"api_keys": [], "token_discoveries": [], "copilot_proposals": [], "audit_events": []},
        )

    def test_degraded_exact(self):
        snap = build_offline_dashboard("degraded")
        self.assertEqual(snap["scenario"], "degraded")
        self.assertIs(snap["health"]["ok"], False)
        self.assertEqual(
            snap["health"]["providers"],
            {"synthetic-data": {"status": "degraded", "latency_ms": None, "last_error": "SYNTHETIC_DATA_UNAVAILABLE"}},
        )
        empty = build_offline_dashboard("empty")
        self.assertEqual(snap["dashboard"], empty["dashboard"])
        self.assertEqual(snap["usage"], empty["usage"])
        self.assertEqual(snap["collections"], empty["collections"])
        self.assertEqual(snap["schema_version"], 1)
        self.assertIs(snap["synthetic"], True)

    def test_default_is_empty(self):
        self.assertEqual(build_offline_dashboard(), build_offline_dashboard("empty"))

    def test_invalid_scenarios_raise_exact_without_echo(self):
        bad_values = ["EMPTY", "Empty", "", " unknown ", "empty ", "degraded\n", "null", "None"]
        for bad in bad_values:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    build_offline_dashboard(bad)
                self.assertEqual(str(ctx.exception), _SCENARIO_ERROR)
                if bad:
                    self.assertNotIn(bad, str(ctx.exception))

    def test_invalid_types_raise_without_coercion(self):
        for bad in [None, True, False, 0, 1, 0.0, b"empty", ["empty"], ("empty",), {}]:
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValueError) as ctx:
                    build_offline_dashboard(bad)
                self.assertEqual(str(ctx.exception), _SCENARIO_ERROR)

    def test_counters_are_int_not_bool(self):
        snap = build_offline_dashboard("empty")
        for val in [
            snap["dashboard"]["org_id"],
            snap["dashboard"]["requests_this_month"],
            snap["dashboard"]["active_api_keys"],
            snap["dashboard"]["total_simulated_trades"],
            snap["usage"]["requests_today"],
            snap["usage"]["requests_month"],
            snap["usage"]["quota"]["simulate_order"],
            snap["usage"]["quota"]["token_audit"],
            snap["usage"]["quota"]["active_api_keys"],
        ]:
            self.assertIs(type(val), int)
        for val in [
            snap["dashboard"]["simulated_volume_usd"],
            snap["dashboard"]["total_volume_usd"],
            snap["dashboard"]["total_fees_usd"],
            snap["dashboard"]["realized_pnl_usd"],
            snap["dashboard"]["unrealized_pnl_usd"],
        ]:
            self.assertIs(type(val), float)

    def test_containers_independent(self):
        first = build_offline_dashboard("empty")
        first["dashboard"]["org_id"] = 999
        first["dashboard"]["last_30_days_chart_points"].append({"x": 1})
        first["usage"]["quota"]["simulate_order"] = 999
        first["collections"]["api_keys"].append({"x": 1})
        first["health"]["providers"]["synthetic-data"]["status"] = "mutated"
        second = build_offline_dashboard("empty")
        self.assertEqual(second["dashboard"]["org_id"], 0)
        self.assertEqual(second["dashboard"]["last_30_days_chart_points"], [])
        self.assertEqual(second["usage"]["quota"]["simulate_order"], 0)
        self.assertEqual(second["collections"]["api_keys"], [])
        self.assertEqual(second["health"]["providers"]["synthetic-data"]["status"], "offline")
        third = build_offline_dashboard("degraded")
        self.assertEqual(third["health"]["providers"]["synthetic-data"]["status"], "degraded")

    def test_json_finite_and_small(self):
        for scenario in ("empty", "degraded"):
            snap = build_offline_dashboard(scenario)
            text = json.dumps(snap, allow_nan=False)
            self.assertLess(len(text.encode("utf-8")), 12000)
            parsed = json.loads(text)
            self.assertEqual(parsed, snap)
            self.assertNotIn("NaN", text)
            self.assertNotIn("Infinity", text)

    def test_main_default_empty(self):
        code, out, err = _run([])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertTrue(out.endswith("\n"))
        self.assertEqual(out.count("\n"), 1)
        self.assertEqual(json.loads(out), build_offline_dashboard("empty"))

    def test_main_empty_explicit(self):
        code, out, err = _run(["--scenario", "empty"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out), build_offline_dashboard("empty"))

    def test_main_degraded(self):
        code, out, err = _run(["--scenario", "degraded"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out), build_offline_dashboard("degraded"))

    def test_main_help(self):
        code, out, err = _run(["--help"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("tothemoon-offline-dashboard", out)
        self.assertIn("--scenario", out)

    def test_main_invalid_value(self):
        code, out, err = _run(["--scenario", "bogus-value-xyz"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, _ARGS_ERROR + "\n")
        self.assertNotIn("bogus-value-xyz", err)

    def test_main_unknown_flag(self):
        code, out, err = _run(["--unknown-flag"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, _ARGS_ERROR + "\n")

    def test_main_abbreviation_rejected(self):
        code, out, err = _run(["--sc", "empty"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, _ARGS_ERROR + "\n")

    def test_main_missing_value(self):
        code, out, err = _run(["--scenario"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, _ARGS_ERROR + "\n")

    def test_main_extra_positional_rejected(self):
        code, out, err = _run(["empty"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, _ARGS_ERROR + "\n")


if __name__ == "__main__":
    unittest.main()
