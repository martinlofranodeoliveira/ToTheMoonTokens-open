from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from tothemoon_api.observability import redact_sensitive_value

_DEFAULT_PROVIDERS = ("goplus", "honeypotis", "tokensniffer", "dexscreener", "birdeye")
_VALID_PROVIDER_STATUSES = frozenset({"ok", "skipped", "degraded", "unknown"})
_PROVIDER_HEALTH: dict[str, dict[str, Any]] = {
    provider: {"status": "unknown", "latency_ms": None, "last_error": None, "observed_at": None}
    for provider in _DEFAULT_PROVIDERS
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def record_provider_status(
    provider: str,
    *,
    status: str,
    started_at: float | None = None,
    last_error: str | None = None,
) -> None:
    if provider not in _PROVIDER_HEALTH:
        raise ValueError(f"Unknown external provider health key: {provider!r}")
    if status not in _VALID_PROVIDER_STATUSES:
        raise ValueError(f"Invalid external provider health status: {status!r}")
    latency_ms = None
    if started_at is not None:
        latency_ms = round((perf_counter() - started_at) * 1000, 2)
    _PROVIDER_HEALTH[provider] = {
        "status": status,
        "latency_ms": latency_ms,
        "last_error": redact_sensitive_value(last_error),
        "observed_at": utc_now_iso(),
    }


def get_provider_health() -> dict[str, dict[str, Any]]:
    return {provider: dict(status) for provider, status in _PROVIDER_HEALTH.items()}


def _parse_observed_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _provider_readiness_status(
    health: dict[str, Any],
    *,
    now: datetime,
    stale_after_seconds: int,
    requires_api_key: bool = False,
    api_key_configured: bool = True,
) -> str:
    if requires_api_key and not api_key_configured:
        return "skipped"
    status = str(health.get("status") or "unknown").strip().lower()
    if status == "ok":
        observed_at = _parse_observed_at(health.get("observed_at"))
        if observed_at is None:
            return "unknown"
        age_seconds = max(0, int((now - observed_at).total_seconds()))
        return "stale" if age_seconds > stale_after_seconds else "ready"
    if status == "skipped":
        return "skipped"
    if status == "degraded":
        return "degraded"
    return "unknown"


def _rollup_status(statuses: list[str], *, ignore_skipped: bool = False) -> str:
    effective_statuses = (
        [status for status in statuses if status != "skipped"] if ignore_skipped else statuses
    )
    if any(status == "unsafe" for status in effective_statuses):
        return "unsafe"
    if effective_statuses and all(status in {"ready", "healthy"} for status in effective_statuses):
        return "healthy"
    if any(status in {"ready", "healthy"} for status in effective_statuses):
        return "degraded"
    return "not_ready"


def _contract_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def build_provider_readiness(
    *,
    health: dict[str, dict[str, Any]] | None = None,
    contract: dict[str, object],
    now: datetime | None = None,
    stale_after_seconds: int = 300,
) -> dict[str, object]:
    providers = contract.get("providers")
    if not isinstance(providers, list):
        providers = []
    now = now or datetime.now(UTC)
    provider_health = get_provider_health() if health is None else health
    provider_payloads: list[dict[str, object]] = []
    capability_statuses: dict[str, list[str]] = {}

    for provider_contract in providers:
        if not isinstance(provider_contract, dict):
            continue
        name = str(provider_contract.get("name") or "").strip()
        if not name:
            continue
        health_snapshot = dict(provider_health.get(name) or {})
        requires_api_key_value = provider_contract.get("requires_api_key", False)
        api_key_configured_value = provider_contract.get("api_key_configured", True)
        credential_contract_safe = isinstance(requires_api_key_value, bool) and isinstance(
            api_key_configured_value, bool
        )
        requires_api_key = _contract_bool(requires_api_key_value, default=False)
        api_key_configured = _contract_bool(
            api_key_configured_value,
            default=True,
        )
        readiness_status = _provider_readiness_status(
            health_snapshot,
            now=now,
            stale_after_seconds=stale_after_seconds,
            requires_api_key=bool(requires_api_key),
            api_key_configured=bool(api_key_configured),
        )
        read_only = provider_contract.get("read_only") is True
        supports_order_submission = provider_contract.get("supports_order_submission") is True
        mainnet_order_submission = provider_contract.get("mainnet_order_submission") is True
        public_exposure = provider_contract.get("public_exposure") is True
        contract_safe = (
            credential_contract_safe
            and read_only
            and not supports_order_submission
            and not mainnet_order_submission
            and not public_exposure
        )
        safety_issue = None if contract_safe else "unsafe provider contract"
        if not credential_contract_safe:
            safety_issue = "unsafe provider credential contract"
        if safety_issue is not None:
            readiness_status = "unsafe"
        capabilities = provider_contract.get("capabilities")
        capability_names = (
            [str(capability) for capability in capabilities if isinstance(capability, str)]
            if isinstance(capabilities, list)
            else []
        )
        for capability in capability_names:
            capability_statuses.setdefault(capability, []).append(readiness_status)
        provider_payloads.append(
            {
                "name": name,
                "status": readiness_status,
                "source_status": str(health_snapshot.get("status") or "unknown"),
                "capabilities": capability_names,
                "requires_api_key": bool(requires_api_key),
                "api_key_configured": bool(api_key_configured),
                "read_only": read_only,
                "supports_order_submission": supports_order_submission,
                "mainnet_order_submission": mainnet_order_submission,
                "public_exposure": public_exposure,
                "paper_only_safe": contract_safe,
                "safety_issue": safety_issue,
                "latency_ms": health_snapshot.get("latency_ms"),
                "observed_at": health_snapshot.get("observed_at"),
                "last_error": (
                    "missing API key"
                    if requires_api_key and not api_key_configured
                    else redact_sensitive_value(health_snapshot.get("last_error"))
                ),
            }
        )

    capability_payloads = {
        capability: {"status": _rollup_status(statuses, ignore_skipped=True)}
        for capability, statuses in sorted(capability_statuses.items())
    }
    capability_rollups = [str(payload["status"]) for payload in capability_payloads.values()]
    provider_contracts_safe = (
        contract.get("order_submission_enabled") is not True
        and contract.get("mainnet_order_submission_enabled") is not True
        and all(provider.get("paper_only_safe") is True for provider in provider_payloads)
    )
    return {
        "status": _rollup_status(capability_rollups) if provider_contracts_safe else "unsafe",
        "stale_after_seconds": stale_after_seconds,
        "provider_count": len(provider_payloads),
        "providers": provider_payloads,
        "capabilities": capability_payloads,
        "paper_only": provider_contracts_safe,
        "order_submission_enabled": contract.get("order_submission_enabled") is True,
        "mainnet_order_submission_enabled": contract.get("mainnet_order_submission_enabled")
        is True,
    }


def summarize_provider_health(*provider_names: str) -> dict[str, Any]:
    selected = set(provider_names)
    provider_diagnostics = {
        provider: {
            "status": status.get("status"),
            "latency_ms": status.get("latency_ms"),
            "observed_at": status.get("observed_at"),
        }
        for provider, status in get_provider_health().items()
        if not selected or provider in selected
    }
    skipped_providers = [
        provider
        for provider, status in provider_diagnostics.items()
        if status.get("status") == "skipped"
    ]
    provider_degraded = any(
        status.get("status") in {"degraded", "unknown"}
        for status in provider_diagnostics.values()
    )
    degraded_providers = [
        provider
        for provider, status in provider_diagnostics.items()
        if status.get("status") in {"degraded", "unknown"}
    ]
    return {
        "total": len(provider_diagnostics),
        "unknown": sum(
            1 for status in provider_diagnostics.values() if status.get("status") == "unknown"
        ),
        "skipped": len(skipped_providers),
        "skipped_providers": skipped_providers,
        "degraded_providers": degraded_providers,
        "provider_degraded": provider_degraded,
        "provider_diagnostics": provider_diagnostics,
    }


def build_operator_readiness_evidence(
    *,
    provider_readiness: dict[str, object],
    runtime_mode: str,
) -> dict[str, object]:
    paper_only = provider_readiness.get("paper_only") is True
    order_submission_enabled = provider_readiness.get("order_submission_enabled") is True
    mainnet_order_submission_enabled = (
        provider_readiness.get("mainnet_order_submission_enabled") is True
    )
    provider_status = provider_readiness.get("status")
    providers = provider_readiness.get("providers")
    has_provider_contract = isinstance(providers, list) and len(providers) > 0
    provider_contract_safe = (
        paper_only
        and not order_submission_enabled
        and not mainnet_order_submission_enabled
        and has_provider_contract
    )
    provider_read_through_available = provider_contract_safe and provider_status in {
        "healthy",
        "degraded",
    }
    provider_blockers = []
    if not has_provider_contract:
        provider_blockers.append("external provider contract is missing")
    if not paper_only:
        provider_blockers.append("external providers are not paper-only safe")
    if order_submission_enabled:
        provider_blockers.append("external provider order submission is enabled")
    if mainnet_order_submission_enabled:
        provider_blockers.append("external provider mainnet submission is enabled")
    if provider_status not in {"healthy", "degraded"}:
        provider_blockers.append("external provider readiness has no usable evidence")

    mode_1_status = "ready" if provider_read_through_available else "blocked"
    mode_2_status = "ready" if provider_contract_safe else "blocked"
    modes = [
        {
            "id": "mode_1",
            "name": "Autonomous paper execution",
            "status": mode_1_status,
            "evidence": [
                "provider adapter contract is read-through only",
                "paper simulations can use provider readiness without order submission",
            ],
            "blocking_reasons": provider_blockers if mode_1_status == "blocked" else [],
        },
        {
            "id": "mode_2",
            "name": "Human approval queue",
            "status": mode_2_status,
            "evidence": [
                "approval flow remains paper/testnet gated",
                "provider diagnostics are safe for operator review",
            ],
            "blocking_reasons": provider_blockers if mode_2_status == "blocked" else [],
        },
        {
            "id": "mode_3",
            "name": "Real autonomy",
            "status": "locked",
            "evidence": ["mainnet order submission remains disabled by policy"],
            "blocking_reasons": ["real autonomy is intentionally locked"],
        },
    ]
    blocking_modes = [mode for mode in modes if mode["status"] == "blocked"]
    limited_modes = [mode for mode in modes if mode["status"] in {"limited", "locked"}]
    return {
        "status": "blocked" if blocking_modes else "limited" if limited_modes else "ready",
        "runtime_mode": runtime_mode,
        "generated_at": utc_now_iso(),
        "safety": {
            "paper_only": provider_contract_safe,
            "order_submission_enabled": order_submission_enabled,
            "mainnet_order_submission_enabled": mainnet_order_submission_enabled,
            "autonomous_signing_enabled": False,
            "live_funds_enabled": False,
        },
        "modes": modes,
        "provider_readiness": provider_readiness,
    }
