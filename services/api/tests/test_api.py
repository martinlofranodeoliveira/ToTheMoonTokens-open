from unittest.mock import patch

from fastapi.testclient import TestClient

from tothemoon_api.main import app
from tothemoon_api.market_data import generate_sample_candles

client = TestClient(app)


def test_health_endpoint_exposes_paper_mode_by_default():
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["mode"] == "paper"
    assert payload["orderSubmissionEnabled"] is False
    assert payload["probe"] == "liveness"
    assert "providers" not in payload
    assert "provider_readiness" not in payload
    assert "external_adapter_contract" not in payload


def test_health_endpoint_does_not_depend_on_external_provider_contract():
    with patch(
        "tothemoon_api.main.get_external_adapter_contract",
        side_effect=RuntimeError("external dependency unavailable"),
    ):
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_health_endpoint_remains_live_when_database_readiness_fails():
    with patch("tothemoon_api.main._database_ready", return_value=False):
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["probe"] == "liveness"
    assert "checks" not in payload


def test_health_endpoint_reports_deployed_digest(monkeypatch):
    deployed_image = "registry.gitlab.com/owner/repo/api@sha256:" + "a" * 64
    monkeypatch.setattr("tothemoon_api.main.settings.deployment_release_id", "gitlab-123-deadbeef")
    monkeypatch.setattr("tothemoon_api.main.settings.deployment_image_digest", deployed_image)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["deployment"] == {
        "release_id": "gitlab-123-deadbeef",
        "api_image_digest": deployed_image,
    }


def test_dashboard_includes_guardrails_and_metrics():
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    payload = response.json()
    assert payload["guardrails"]["can_submit_mainnet_orders"] is False
    assert payload["guardrails"]["can_submit_testnet_orders"] is False
    assert payload["metrics"]["trade_count"] >= 0
    assert len(payload["research_snapshots"]) == 3
    assert payload["runtime_status"] is None
    assert payload["connectors"]["settlement_network"] == "arc_testnet"
    assert payload["connectors"]["wallet_provider"] == "circle_developer_controlled_wallets"
    assert payload["recent_trades"] == []
    assert payload["performance"]["total_trades"] == 0


def test_backtest_endpoint_returns_consistent_metrics():
    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_id": "ema_crossover",
            "lookback_bars": 200,
            "initial_capital": 10000,
            "position_size_pct": 20,
            "fee_bps": 10,
            "slippage_bps": 5,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["initial_capital"] == 10000
    assert payload["ending_equity"] > 0
    assert payload["edge_status"] in {"positive", "flat", "negative"}


def test_market_klines_endpoint_returns_candles():
    with patch(
        "tothemoon_api.main.BinanceMarketData.fetch_klines",
        return_value=generate_sample_candles(length=4),
    ):
        response = client.get("/api/market/klines?limit=4")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 4
    assert payload[0]["close"] > 0


def test_market_health_endpoint_returns_probe_status():
    with patch(
        "tothemoon_api.main.BinanceMarketData.ping",
        return_value={"status": "online", "latency_ms": 12.4, "last_error": None},
    ):
        response = client.get("/api/market/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "online"


def test_readiness_endpoint_reports_dependency_details():
    response = client.get("/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["mode"] == "paper"
    assert payload["checks"]["database"] is True
    assert payload["checks"]["external_adapters_paper_only"] is True
    assert "market_connector" in payload
    assert {"goplus", "honeypotis", "tokensniffer", "dexscreener", "birdeye"} <= set(
        payload["providers"]
    )
    assert payload["provider_summary"]["total"] == len(payload["providers"])
    assert payload["provider_summary"]["unknown"] >= 0
    assert payload["provider_summary"]["skipped"] >= 0
    assert isinstance(payload["provider_summary"]["degraded_providers"], list)
    assert isinstance(payload["provider_summary"]["skipped_providers"], list)
    assert isinstance(payload["provider_summary"]["provider_degraded"], bool)
    assert set(payload["provider_summary"]["provider_diagnostics"]) == set(payload["providers"])
    assert payload["external_adapter_contract"]["order_submission_enabled"] is False
    assert payload["external_adapter_contract"]["mainnet_order_submission_enabled"] is False
    assert payload["providers"]["goplus"]["observed_at"] is None or payload["providers"]["goplus"][
        "observed_at"
    ].endswith("Z")


def test_readiness_endpoint_returns_503_when_database_unavailable():
    with patch("tothemoon_api.main._database_ready", return_value=False):
        response = client.get("/ready")

    assert response.status_code == 503
    payload = response.json()["detail"]
    assert payload["ok"] is False
    assert payload["checks"]["database"] is False
    assert payload["checks"]["settings_valid"] is True


def test_provider_readiness_endpoint_is_safe_for_ops_display():
    response = client.get("/api/providers/readiness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"healthy", "degraded", "not_ready"}
    assert payload["paper_only"] is True
    assert payload["order_submission_enabled"] is False
    assert payload["mainnet_order_submission_enabled"] is False
    assert {provider["name"] for provider in payload["providers"]} == {
        "goplus",
        "honeypotis",
        "tokensniffer",
        "dexscreener",
        "birdeye",
    }
    assert "super-secret-token" not in str(payload)
    assert "secret" not in str(payload).lower()


def test_operator_readiness_evidence_summarizes_paper_saas_modes():
    response = client.get("/api/operator/readiness-evidence")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ready", "limited", "blocked"}
    assert payload["runtime_mode"] == "paper"
    assert payload["safety"] == {
        "paper_only": True,
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "autonomous_signing_enabled": False,
        "live_funds_enabled": False,
    }
    modes = {mode["id"]: mode for mode in payload["modes"]}
    assert modes["mode_1"]["name"] == "Autonomous paper execution"
    assert modes["mode_1"]["status"] in {"ready", "blocked"}
    assert modes["mode_2"]["name"] == "Human approval queue"
    assert modes["mode_2"]["status"] == "ready"
    assert modes["mode_3"] == {
        "id": "mode_3",
        "name": "Real autonomy",
        "status": "locked",
        "evidence": ["mainnet order submission remains disabled by policy"],
        "blocking_reasons": ["real autonomy is intentionally locked"],
    }
    assert payload["provider_readiness"]["paper_only"] is True
    assert "super-secret-token" not in str(payload)
    assert "raw-provider-secret" not in str(payload)
    assert "raw-gemini-secret" not in str(payload)


def test_arc_network_health_endpoint():
    with patch(
        "tothemoon_api.main.ping_arc_network",
        return_value={
            "status": "online",
            "chain_id": 5042002,
            "url": "https://rpc.testnet.arc.network",
        },
    ):
        response = client.get("/api/network/arc/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "online"
    assert payload["chain_id"] == 5042002
