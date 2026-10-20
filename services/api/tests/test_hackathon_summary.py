import json
from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient

from tothemoon_api.main import app

client = TestClient(app)


@pytest.fixture
def synthetic_summary_evidence(tmp_path, monkeypatch):
    """Generated local fixtures only; these hashes represent no real payment."""
    evidence = {
        "synthetic": True,
        "generated_at": "2000-01-01T00:00:00Z",
        "wallet_set_id": "synthetic-wallet-set",
        "summary": {
            "total_attempted": 63, "successful": 63, "failed": 0,
            "total_usdc_moved": 0.126, "amount_per_tx_usdc": 0.002,
            "throughput_tx_per_min": 120, "latency_p50_ms": 4, "latency_p95_ms": 8,
        },
        "source": {"name": "synthetic-sender", "address": "0x" + "0" * 39 + "1"},
        "destinations": [{"name": "synthetic-recipient", "address": "0x" + "0" * 39 + "2"}],
        "transactions": [
            {"seq": number, "tx_hash": "0x" + format(number, "064x"),
             "amount_usdc": 0.002, "status": "synthetic-success",
             "timestamp": "2000-01-01T00:00:00Z", "elapsed_ms": 4}
            for number in range(1, 64)
        ],
    }
    packaged = tmp_path / "synthetic-package.json"
    packaged.write_text(json.dumps(evidence))
    monkeypatch.setattr("tothemoon_api.hackathon_summary._PACKAGED_EVIDENCE_PATH", packaged)
    monkeypatch.setattr("tothemoon_api.hackathon_summary._ROOT_DIR", tmp_path / "no-operations")
    return evidence


@patch(
    "tothemoon_api.hackathon_summary.ping_arc_network",
    return_value={
        "status": "online",
        "chain_id": 5042002,
        "url": "https://rpc.testnet.arc.network",
    },
)
def test_hackathon_summary_exposes_judge_facing_proof(_mock_arc_health, synthetic_summary_evidence):
    response = client.get("/api/hackathon/summary")
    assert response.status_code == 200
    payload = response.json()

    assert payload["ok"] is True
    assert payload["project"] == "TTM Agent Market"
    assert payload["evidence_kind"] == "synthetic"
    assert payload["proof"]["transactions_total"] >= 63
    assert payload["proof"]["transactions_successful"] >= 63
    assert payload["proof"]["transactions_listed"] == len(payload["transactions"])
    assert len(payload["transactions"]) >= 63
    assert len(payload["latest_transactions"]) <= 8
    assert payload["transactions"][0]["seq"] >= payload["transactions"][-1]["seq"]
    assert all(item["tx_hash"].startswith("0x") for item in payload["transactions"])
    assert all(
        item["explorer_url"].startswith("https://testnet.arcscan.app/tx/")
        for item in payload["transactions"]
    )
    assert payload["proof"]["throughput_tx_per_min"] >= 1
    assert payload["proof"]["sample_tx_hash"].startswith("0x")
    assert payload["circle"]["wallet_set_id"]
    assert payload["connectors"]["settlement_network"] == "arc_testnet"
    assert payload["connectors"]["settlement_auth_mode"] in {"manual", "programmatic"}
    assert "agent_chat_ready" in payload["connectors"]
    assert payload["margin"]["eth_l1_counterfactual_gas_usd"] > payload["proof"]["total_usdc_moved"]
    assert all(0 < item["price_usd"] <= 0.01 for item in payload["catalog"])


@patch(
    "tothemoon_api.hackathon_summary.ping_arc_network",
    return_value={
        "status": "online",
        "chain_id": 5042002,
        "url": "https://rpc.testnet.arc.network",
    },
)
def test_hackathon_summary_uses_packaged_full_evidence_when_repo_file_missing(
    _mock_arc_health,
    monkeypatch,
    tmp_path,
    synthetic_summary_evidence,
):
    monkeypatch.setattr("tothemoon_api.hackathon_summary._ROOT_DIR", tmp_path)

    response = client.get("/api/hackathon/summary")
    assert response.status_code == 200
    payload = response.json()

    assert payload["proof"]["transactions_total"] == 63
    assert payload["proof"]["transactions_listed"] == 63
    assert len(payload["transactions"]) == 63
    assert payload["transactions"][0]["seq"] == 63
    assert payload["transactions"][-1]["seq"] == 1


@patch("tothemoon_api.hackathon_summary.ping_arc_network")
def test_summary_without_evidence_is_empty_and_does_not_ping_network(mock_ping, monkeypatch, tmp_path):
    monkeypatch.setattr("tothemoon_api.hackathon_summary._ROOT_DIR", tmp_path)
    monkeypatch.setattr("tothemoon_api.hackathon_summary._PACKAGED_EVIDENCE_PATH", tmp_path / "missing.json")
    response = client.get("/api/hackathon/summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence_kind"] == "none"
    assert payload["proof"]["transactions_total"] == 0
    assert payload["proof"]["transactions_successful"] == 0
    assert payload["proof"]["transactions_listed"] == 0
    assert payload["proof"]["generated_at"] is None
    assert payload["proof"]["sample_tx_hash"] is None
    assert payload["transactions"] == []
    assert payload["circle"]["wallets"] == []
    assert payload["arc"]["status"] == "offline"
    mock_ping.assert_not_called()


def test_open_edition_does_not_discover_previous_operations_evidence(monkeypatch, tmp_path):
    from tothemoon_api.hackathon_summary import _latest_evidence_path

    operations = tmp_path / "ops" / "evidence"
    operations.mkdir(parents=True)
    (operations / "nanopayments-batch-old.json").write_text('{"synthetic": true}')
    monkeypatch.setattr("tothemoon_api.hackathon_summary._ROOT_DIR", tmp_path)
    monkeypatch.setattr("tothemoon_api.hackathon_summary._PACKAGED_EVIDENCE_PATH", tmp_path / "missing.json")
    assert _latest_evidence_path() is None
