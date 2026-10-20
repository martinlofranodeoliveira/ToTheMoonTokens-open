from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from tothemoon_api.external.health import record_provider_status

ProviderCapability = Literal["market_data", "security_audit"]
ProviderFetch = Callable[..., object]
VALID_PROVIDER_CAPABILITIES: frozenset[str] = frozenset({"market_data", "security_audit"})


def provider_metadata(
    provider: str,
    *,
    status: Literal["live", "degraded", "stale", "placeholder"] = "live",
    confidence: Literal["high", "medium", "low", "none"] = "high",
    ttl_seconds: int,
) -> dict[str, Any]:
    observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "provider": provider,
        "observed_at": observed_at,
        "freshness": {
            "status": status,
            "ttl_seconds": ttl_seconds,
            "age_seconds": 0,
            "stale": status in {"degraded", "stale", "placeholder"},
        },
        "confidence": confidence,
        "degraded": status in {"degraded", "stale", "placeholder"},
    }


def refresh_freshness_metadata(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    freshness = payload.get("freshness")
    observed_at = payload.get("observed_at")
    if not isinstance(freshness, dict) or not isinstance(observed_at, str):
        return payload
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        return payload
    ttl_seconds = freshness.get("ttl_seconds")
    if not isinstance(ttl_seconds, int | float):
        return payload
    age_seconds = max(0, int((datetime.now(UTC) - observed).total_seconds()))
    status = freshness.get("status")
    stale = age_seconds > ttl_seconds or status in {"degraded", "stale", "placeholder"}
    payload = dict(payload)
    payload["freshness"] = {**freshness, "age_seconds": age_seconds, "stale": stale}
    payload["degraded"] = bool(payload.get("degraded")) or stale
    return payload


class UnsafeProviderAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalProviderAdapter:
    name: str
    capabilities: frozenset[ProviderCapability]
    fetch: ProviderFetch
    requires_api_key: bool = False
    api_key_configured: bool = True
    read_only: bool = True
    supports_order_submission: bool = False
    mainnet_order_submission: bool = False
    public_exposure: bool = False

    def assert_safe_for_paper_mode(self) -> None:
        if not self.name.strip():
            raise UnsafeProviderAdapterError("External provider name must be non-empty")
        invalid_capabilities = sorted(
            capability
            for capability in self.capabilities
            if capability not in VALID_PROVIDER_CAPABILITIES
        )
        if not self.capabilities or invalid_capabilities:
            raise UnsafeProviderAdapterError(
                f"External provider {self.name!r} has invalid capabilities: {invalid_capabilities}"
            )
        if (
            not self.read_only
            or self.supports_order_submission
            or self.mainnet_order_submission
            or self.public_exposure
        ):
            raise UnsafeProviderAdapterError(
                f"External provider {self.name!r} violates the paper-only adapter contract"
            )

    def contract_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "capabilities": sorted(self.capabilities),
            "requires_api_key": self.requires_api_key,
            "api_key_configured": self.api_key_configured,
            "read_only": self.read_only,
            "supports_order_submission": self.supports_order_submission,
            "mainnet_order_submission": self.mainnet_order_submission,
            "public_exposure": self.public_exposure,
        }


def provider_adapter(
    name: str,
    capabilities: set[ProviderCapability],
    fetch: ProviderFetch,
    *,
    requires_api_key: bool = False,
    api_key_configured: bool | None = None,
) -> ExternalProviderAdapter:
    adapter = ExternalProviderAdapter(
        name=name,
        capabilities=frozenset(capabilities),
        fetch=fetch,
        requires_api_key=requires_api_key,
        api_key_configured=(
            not requires_api_key if api_key_configured is None else api_key_configured
        ),
    )
    adapter.assert_safe_for_paper_mode()
    return adapter


def call_provider(adapter: ExternalProviderAdapter, *args: object) -> dict[str, Any]:
    adapter.assert_safe_for_paper_mode()
    try:
        result = adapter.fetch(*args)
    except Exception as exc:
        if exc.__class__.__name__ != "ProviderSkipped":
            record_provider_status(adapter.name, status="degraded", last_error=str(exc))
        raise
    if not isinstance(result, dict):
        record_provider_status(adapter.name, status="degraded", last_error="non-object payload")
        raise TypeError(f"External provider {adapter.name!r} returned a non-object payload")
    provider = result.get("provider")
    if not isinstance(provider, str) or provider != adapter.name:
        record_provider_status(
            adapter.name,
            status="degraded",
            last_error="provider identity mismatch",
        )
        raise ValueError(
            f"External provider {adapter.name!r} returned a mismatched provider identity"
        )
    return result


def is_paper_only_adapter_contract(contract: dict[str, object]) -> bool:
    providers = contract.get("providers")
    return (
        contract.get("mode") == "paper_only_read_through"
        and contract.get("order_submission_enabled") is False
        and contract.get("mainnet_order_submission_enabled") is False
        and isinstance(providers, list)
        and len(providers) > 0
        and all(
            isinstance(provider, dict)
            and isinstance(provider.get("name"), str)
            and bool(str(provider.get("name")).strip())
            and _contract_capabilities_are_valid(provider.get("capabilities"))
            and isinstance(provider.get("requires_api_key"), bool)
            and isinstance(provider.get("api_key_configured"), bool)
            and provider.get("read_only") is True
            and provider.get("supports_order_submission") is False
            and provider.get("mainnet_order_submission") is False
            and provider.get("public_exposure") is False
            for provider in providers
        )
    )


def _contract_capabilities_are_valid(capabilities: object) -> bool:
    return (
        isinstance(capabilities, list)
        and len(capabilities) > 0
        and all(
            isinstance(capability, str) and capability in VALID_PROVIDER_CAPABILITIES
            for capability in capabilities
        )
    )


def adapter_contract_payload(adapters: list[ExternalProviderAdapter]) -> dict[str, object]:
    for adapter in adapters:
        adapter.assert_safe_for_paper_mode()
    contract = {
        "mode": "paper_only_read_through",
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "providers": [adapter.contract_payload() for adapter in adapters],
    }
    if not is_paper_only_adapter_contract(contract):
        raise UnsafeProviderAdapterError("External provider contract is not paper-only safe")
    return contract
