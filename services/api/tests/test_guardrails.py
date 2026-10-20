from __future__ import annotations

from tothemoon_api.config import Settings
from tothemoon_api.guards import connector_status, evaluate_guardrails


def _make_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "wallet_mode": "manual_only",
        "circle_wallet_set_id": "ws-demo",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_hackathon_scope_blocks_all_order_submission():
    status = evaluate_guardrails(_make_settings())
    assert status.mode == "paper"
    assert status.can_submit_testnet_orders is False
    assert status.can_submit_mainnet_orders is False
    assert any("Order submission is disabled" in reason for reason in status.reasons)


def test_high_risk_tier_remains_research_only():
    status = evaluate_guardrails(_make_settings(), risk_tier="high")
    assert status.can_submit_testnet_orders is False
    assert any("RISK_TIER=high" in reason for reason in status.reasons)


def test_wallet_mode_manual_requires_manual_signature():
    status = evaluate_guardrails(_make_settings(wallet_mode="manual_only"))
    assert status.requires_manual_wallet_signature is True


def test_wallet_mode_non_manual_is_reported():
    status = evaluate_guardrails(_make_settings(wallet_mode="custodial"))
    assert status.requires_manual_wallet_signature is False
    assert any("WALLET_MODE='custodial'" in reason for reason in status.reasons)


def test_connector_status_exposes_testnet_endpoints():
    info = connector_status(_make_settings())
    assert info.settlement_network == "arc_testnet"
    assert info.wallet_provider == "circle_developer_controlled_wallets"
    assert info.arc_rpc_url.startswith("https://rpc.testnet.arc.network")
    assert info.wallet_set_id
    assert info.metamask_ready is True


def test_runtime_mode_stays_in_paper():
    assert _make_settings().runtime_mode == "paper"
