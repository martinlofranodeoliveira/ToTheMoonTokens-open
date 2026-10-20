from __future__ import annotations

from typing import Any


class BotSafetyError(RuntimeError):
    pass


def assert_paper_or_guarded_testnet_status(status: dict[str, Any]) -> None:
    runtime_mode = status.get("runtime_mode")
    if runtime_mode not in {"paper", "guarded_testnet"}:
        raise BotSafetyError(f"Unsupported bot runtime mode: {runtime_mode!r}")
    if status.get("order_submission_enabled") is not False:
        raise BotSafetyError("Bot orchestration requires order submission to remain disabled")
    if status.get("mainnet_order_submission_enabled") is not False:
        raise BotSafetyError("Mainnet order submission is permanently blocked for bots")

    safeguards = status.get("safeguards")
    if not isinstance(safeguards, dict):
        raise BotSafetyError("Bot orchestration status is missing safeguards")
    if safeguards.get("production_mainnet_blocked") is not True:
        raise BotSafetyError("Production/mainnet safeguard is not active")
    if safeguards.get("adapter_contract_enforced") is not True:
        raise BotSafetyError("External adapter paper-only contract is not enforced")
    if runtime_mode == "guarded_testnet" and safeguards.get("guarded_testnet_enabled") is not True:
        raise BotSafetyError("Guarded testnet mode requires an explicit feature flag")

    readiness = status.get("readiness")
    if not isinstance(readiness, dict):
        raise BotSafetyError("Bot orchestration status is missing readiness")
    if readiness.get("ready") is not True:
        reasons = readiness.get("blocking_reasons")
        if not isinstance(reasons, list):
            reasons = ["unknown"]
        safe_reasons = [str(reason) for reason in reasons if isinstance(reason, str)] or ["unknown"]
        raise BotSafetyError(
            f"Bot orchestration backend readiness is blocked: {', '.join(safe_reasons)}"
        )

    provider_readiness = status.get("provider_readiness")
    if not isinstance(provider_readiness, dict):
        raise BotSafetyError("Bot orchestration status is missing provider readiness")
    readiness_status = provider_readiness.get("status")
    if not isinstance(readiness_status, str) or readiness_status not in {
        "healthy",
        "degraded",
        "not_ready",
        "unsafe",
    }:
        raise BotSafetyError("Provider readiness status is invalid")
    providers = provider_readiness.get("providers")
    if not isinstance(providers, list):
        raise BotSafetyError("Provider readiness must include provider diagnostics")
    capabilities = provider_readiness.get("capabilities")
    if not isinstance(capabilities, dict):
        raise BotSafetyError("Provider readiness must include capability diagnostics")
    if provider_readiness.get("paper_only") is not True:
        raise BotSafetyError("Provider readiness must preserve paper-only execution")
    if provider_readiness.get("order_submission_enabled") is not False:
        raise BotSafetyError("Provider readiness must not enable order submission")
    if provider_readiness.get("mainnet_order_submission_enabled") is not False:
        raise BotSafetyError("Provider readiness must not enable mainnet order submission")


async def fetch_bot_status(client: Any, *, api_key: str) -> dict[str, Any]:
    response = await client.get("/api/v1/copilot/bot/status", headers={"X-API-Key": api_key})
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise BotSafetyError("Bot orchestration status must be a JSON object")
    assert_paper_or_guarded_testnet_status(data)
    return data
