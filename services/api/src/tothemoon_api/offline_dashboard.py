"""Synthetic offline dashboard fixtures for local mocks (stdlib only)."""
from __future__ import annotations
import argparse
import json
from typing import Any, Dict, Optional, Sequence

_SCENARIO_EMPTY = "empty"
_SCENARIO_DEGRADED = "degraded"


def build_offline_dashboard(scenario: str = "empty") -> Dict[str, Any]:
    """Build a new deterministic synthetic offline dashboard snapshot."""
    if type(scenario) is not str:
        raise ValueError("INVALID_OFFLINE_DASHBOARD_SCENARIO")
    if scenario != _SCENARIO_EMPTY and scenario != _SCENARIO_DEGRADED:
        raise ValueError("INVALID_OFFLINE_DASHBOARD_SCENARIO")
    if scenario == _SCENARIO_EMPTY:
        ok = True
        status = "offline"
        last_error = None
    else:
        ok = False
        status = "degraded"
        last_error = "SYNTHETIC_DATA_UNAVAILABLE"
    health: Dict[str, Any] = {
        "ok": ok,
        "app": "ToTheMoonTokens offline fixture",
        "mode": "synthetic",
        "orderSubmissionEnabled": False,
        "autonomous_payments_enabled": False,
        "wallets_loaded": False,
        "providers": {
            "synthetic-data": {
                "status": status,
                "latency_ms": None,
                "last_error": last_error,
            }
        },
    }
    dashboard: Dict[str, Any] = {
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
    }
    usage: Dict[str, Any] = {
        "requests_today": 0,
        "requests_month": 0,
        "plan": "offline-demo",
        "quota": {
            "simulate_order": 0,
            "token_audit": 0,
            "active_api_keys": 0,
        },
    }
    collections: Dict[str, Any] = {
        "api_keys": [],
        "token_discoveries": [],
        "copilot_proposals": [],
        "audit_events": [],
    }
    snapshot: Dict[str, Any] = {
        "schema_version": 1,
        "synthetic": True,
        "scenario": scenario,
        "health": health,
        "dashboard": dashboard,
        "usage": usage,
        "collections": collections,
    }
    return snapshot


class _OfflineDashboardParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(2, "INVALID_OFFLINE_DASHBOARD_ARGUMENTS\n")


def _create_parser() -> argparse.ArgumentParser:
    parser = _OfflineDashboardParser(
        prog="tothemoon-offline-dashboard",
        description="Print synthetic offline dashboard fixture as JSON.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--scenario",
        default="empty",
        help="synthetic scenario: empty or degraded (default: empty)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print one JSON line for the requested scenario and return 0."""
    parser = _create_parser()
    args = parser.parse_args(argv)
    scenario = args.scenario
    if type(scenario) is not str:
        parser.exit(2, "INVALID_OFFLINE_DASHBOARD_ARGUMENTS\n")
    if scenario != _SCENARIO_EMPTY and scenario != _SCENARIO_DEGRADED:
        parser.exit(2, "INVALID_OFFLINE_DASHBOARD_ARGUMENTS\n")
    snapshot = build_offline_dashboard(scenario)
    print(json.dumps(snapshot, allow_nan=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
