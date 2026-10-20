from __future__ import annotations

import os

from .circle import circle_client
from .config import Settings
from .market_data import connector_state
from .models import ConnectorStatus, GuardrailStatus, RiskTier
from .observability import GUARDRAIL_EVALUATIONS_TOTAL, get_logger

log = get_logger(__name__)

FORBIDDEN_SECRETS = [
    "BINANCE_API_SECRET",
    "BINANCE_SECRET_KEY",
    "BINANCE_SECRET",
    "WALLET_PRIVATE_KEY",
    "PRIVATE_KEY",
    "WEB3_PRIVATE_KEY",
    "MAINNET_API_KEY",
    "MAINNET_PRIVATE_KEY",
]


def gemini_configured(settings: Settings) -> bool:
    return bool(
        settings.gemini_api_key or (settings.gemini_use_vertexai and settings.gemini_vertex_project)
    )


def evaluate_guardrails(settings: Settings, risk_tier: RiskTier = "low") -> GuardrailStatus:
    reasons: list[str] = [
        "Order submission is disabled. Hackathon scope is paid agent artifacts, not trading automation."
    ]
    can_submit_testnet_orders = False
    can_submit_mainnet_orders = False

    if risk_tier == "high":
        reasons.append(
            "RISK_TIER=high: perfis agressivos permanecem elegiveis apenas para pesquisa e evidencia."
        )

    for secret in FORBIDDEN_SECRETS:
        if os.getenv(secret):
            reasons.append(
                f"FORBIDDEN_SECRET: {secret} detectado. Segredos de carteira e keys reais sao bloqueados."
            )

    if settings.wallet_mode == "disabled":
        reasons.append("WALLET_MODE='disabled': Circle wallet actions are disabled.")
    elif settings.wallet_mode == "custodial":
        reasons.append(
            "WALLET_MODE='custodial': Circle developer-controlled wallet route is available."
        )

    GUARDRAIL_EVALUATIONS_TOTAL.labels(
        mode=settings.runtime_mode,
        can_submit_testnet=str(can_submit_testnet_orders).lower(),
    ).inc()

    log.warning("guardrail_blocked", runtime_mode=settings.runtime_mode, reasons=reasons)

    return GuardrailStatus(
        mode=settings.runtime_mode,
        can_submit_testnet_orders=can_submit_testnet_orders,
        can_submit_mainnet_orders=can_submit_mainnet_orders,
        requires_manual_wallet_signature=settings.wallet_mode == "manual_only",
        settlement_auth_mode=settings.settlement_auth_mode,
        autonomous_payments_enabled=settings.autonomous_payments_enabled,
        reasons=reasons,
    )


def connector_status(settings: Settings) -> ConnectorStatus:
    return ConnectorStatus(
        settlement_network="arc_testnet",
        wallet_provider="circle_developer_controlled_wallets",
        wallet_mode=settings.wallet_mode,
        settlement_auth_mode=settings.settlement_auth_mode,
        autonomous_payments_enabled=settings.autonomous_payments_enabled,
        wallet_set_id=settings.circle_wallet_set_id or None,
        wallets_configured=len(circle_client.roles) if settings.circle_wallet_set_id else 0,
        wallets_loaded=circle_client.wallets_loaded,
        treasury_address=circle_client.get_wallet_address("TREASURY"),
        arc_rpc_url=settings.arc_testnet_rpc_url,
        metamask_ready=settings.wallet_mode == "manual_only",
        agent_chat_ready=gemini_configured(settings),
        agent_model=settings.gemini_model or None,
        latency_ms=connector_state.last_latency_ms or None,
        reconnect_count=connector_state.reconnect_count,
        last_error=connector_state.last_error,
    )
