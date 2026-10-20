from __future__ import annotations

import logging
import math
from time import perf_counter
from typing import Any

import httpx

from tothemoon_api.config import get_settings
from tothemoon_api.external.adapters import (
    ExternalProviderAdapter,
    call_provider,
    provider_adapter,
    provider_metadata,
)
from tothemoon_api.external.cache import cached
from tothemoon_api.external.health import record_provider_status

logger = logging.getLogger(__name__)

GOPLUS_TOKEN_SECURITY_URL = "https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
GOPLUS_SOLANA_SECURITY_URL = "https://api.gopluslabs.io/api/v1/solana/token_security"
HONEYPOT_URL = "https://api.honeypot.is/v2/IsHoneypot"
TOKENSNIFFER_URL = "https://tokensniffer.com/api/v2/tokens/{chain_id}/{token_address}"
SECURITY_CACHE_TTL_SECONDS = 300

CHAIN_IDS = {
    "eth": "1",
    "ethereum": "1",
    "evm": "1",
    "bsc": "56",
    "base": "8453",
    "arbitrum": "42161",
    "polygon": "137",
}


class ProviderSkipped(RuntimeError):
    pass


def _is_placeholder_address(token_address: str) -> bool:
    lowered = token_address.lower()
    if lowered in {"0xsafe", "0xabc", "0xtest", "0xgain", "0xbase", "0xscam"}:
        return True
    return lowered.startswith("0x") and len(lowered) != 42


def _placeholder_audit(token_address: str) -> dict[str, Any]:
    is_honeypot = token_address.lower().endswith("scam")
    return {
        "token_address": token_address,
        "is_honeypot": is_honeypot,
        "liquidity_locked_pct": 0.0 if is_honeypot else 95.5,
        "contract_verified": not is_honeypot,
        "risk_score": 99 if is_honeypot else 15,
        "buy_tax": 0,
        "sell_tax": 0,
        "providers": ["local_fallback"],
        **provider_metadata(
            "local_fallback",
            status="placeholder",
            confidence="low",
            ttl_seconds=SECURITY_CACHE_TTL_SECONDS,
        ),
        "warning": "Placeholder security data is for demos only and is not a live safety decision.",
    }


def _unavailable_audit(token_address: str) -> dict[str, Any]:
    return {
        "token_address": token_address,
        "is_honeypot": True,
        "liquidity_locked_pct": 0.0,
        "contract_verified": False,
        "risk_score": 99,
        "buy_tax": 0,
        "sell_tax": 0,
        "providers": ["provider_unavailable"],
        "provider_health": "degraded",
        **provider_metadata(
            "provider_unavailable",
            status="degraded",
            confidence="none",
            ttl_seconds=SECURITY_CACHE_TTL_SECONDS,
        ),
        "warning": "Security providers unavailable; token is blocked for safety.",
    }


def _float(value: object, default: float = 0.0) -> float:
    if not isinstance(value, str | int | float):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def _bool_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _risk_score(is_honeypot: bool, raw_score: object = None, default: int = 15) -> int:
    if is_honeypot:
        return 99
    if not isinstance(raw_score, str | int | float):
        return default
    try:
        parsed = float(raw_score)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed):
        return default
    try:
        score = int(parsed)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, min(99, score))


def _chain_id(chain: str) -> str:
    return CHAIN_IDS.get(chain.strip().lower(), "1")


def _infer_chain(token_address: str, chain: str) -> str:
    normalized = chain.strip().lower()
    if normalized != "evm":
        return normalized
    if not token_address.lower().startswith("0x"):
        return "solana"
    return normalized


def _fetch_goplus(token_address: str, chain: str) -> dict[str, Any]:
    settings = get_settings()
    headers = {"accept": "application/json"}
    if settings.goplus_api_key:
        headers["Authorization"] = f"Bearer {settings.goplus_api_key}"

    params = {"contract_addresses": token_address}
    url = GOPLUS_TOKEN_SECURITY_URL.format(chain_id=_chain_id(chain))
    if chain.strip().lower() == "solana":
        url = GOPLUS_SOLANA_SECURITY_URL
        params = {"contract_addresses": token_address}

    started_at = perf_counter()
    try:
        with httpx.Client(timeout=settings.external_http_timeout_s) as client:
            response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
        payload = response.json()
        result = payload.get("result") or {}
        if isinstance(result, dict) and token_address.lower() in result:
            result = result[token_address.lower()]
        elif isinstance(result, dict) and token_address in result:
            result = result[token_address]
        if not isinstance(result, dict) or not result:
            raise ValueError("GoPlus returned no token security record")

        is_honeypot = _bool_flag(result.get("is_honeypot"))
        record_provider_status("goplus", status="ok", started_at=started_at)
        return {
            "provider": "goplus",
            "is_honeypot": is_honeypot,
            "risk_score": _risk_score(is_honeypot, default=20),
            "buy_tax": _float(result.get("buy_tax")),
            "sell_tax": _float(result.get("sell_tax")),
            "contract_verified": not _bool_flag(result.get("is_open_source") == "0"),
        }
    except Exception as exc:
        record_provider_status(
            "goplus", status="degraded", started_at=started_at, last_error=str(exc)
        )
        raise


def _fetch_honeypotis(token_address: str, chain: str) -> dict[str, Any]:
    if chain.strip().lower() == "solana":
        record_provider_status("honeypotis", status="skipped", last_error="unsupported chain")
        raise ProviderSkipped("Honeypot.is does not support Solana token checks here")

    started_at = perf_counter()
    try:
        settings = get_settings()
        with httpx.Client(timeout=settings.external_http_timeout_s) as client:
            response = client.get(
                HONEYPOT_URL,
                params={"address": token_address, "chainID": _chain_id(chain)},
            )
            response.raise_for_status()
        payload = response.json()
        result = payload.get("honeypotResult") or payload.get("summary") or {}
        simulation = payload.get("simulationResult") or {}
        is_honeypot = _bool_flag(result.get("isHoneypot") or result.get("honeypot"))
        risk = (
            payload.get("summary", {}).get("risk")
            if isinstance(payload.get("summary"), dict)
            else None
        )
        risk_score = 99 if str(risk).lower() == "high" else _risk_score(is_honeypot, default=25)
        record_provider_status("honeypotis", status="ok", started_at=started_at)
        return {
            "provider": "honeypotis",
            "is_honeypot": is_honeypot,
            "risk_score": risk_score,
            "buy_tax": _float(simulation.get("buyTax")),
            "sell_tax": _float(simulation.get("sellTax")),
        }
    except Exception as exc:
        record_provider_status(
            "honeypotis",
            status="degraded",
            started_at=started_at,
            last_error=str(exc),
        )
        raise


def _fetch_tokensniffer(token_address: str, chain: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.tokensniffer_api_key:
        record_provider_status("tokensniffer", status="skipped", last_error="missing API key")
        raise ProviderSkipped("TokenSniffer API key is not configured")

    started_at = perf_counter()
    try:
        headers = {"X-API-KEY": settings.tokensniffer_api_key, "accept": "application/json"}
        with httpx.Client(timeout=settings.external_http_timeout_s) as client:
            response = client.get(
                TOKENSNIFFER_URL.format(chain_id=_chain_id(chain), token_address=token_address),
                headers=headers,
                params={"include_metrics": "true", "include_tests": "true"},
            )
            response.raise_for_status()
        payload = response.json()
        score = payload.get("score")
        is_honeypot = _bool_flag(payload.get("is_honeypot") or payload.get("isHoneypot"))
        record_provider_status("tokensniffer", status="ok", started_at=started_at)
        return {
            "provider": "tokensniffer",
            "is_honeypot": is_honeypot,
            "risk_score": _risk_score(is_honeypot, 100 - _float(score, 85.0), default=30),
            "buy_tax": _float(payload.get("buy_tax")),
            "sell_tax": _float(payload.get("sell_tax")),
        }
    except Exception as exc:
        record_provider_status(
            "tokensniffer",
            status="degraded",
            started_at=started_at,
            last_error=str(exc),
        )
        raise


def get_security_adapters() -> list[ExternalProviderAdapter]:
    settings = get_settings()
    return [
        provider_adapter("goplus", {"security_audit"}, _fetch_goplus),
        provider_adapter("honeypotis", {"security_audit"}, _fetch_honeypotis),
        provider_adapter(
            "tokensniffer",
            {"security_audit"},
            _fetch_tokensniffer,
            requires_api_key=True,
            api_key_configured=bool(settings.tokensniffer_api_key),
        ),
    ]


def _provider_results(token_address: str, chain: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for adapter in get_security_adapters():
        try:
            result = call_provider(adapter, token_address, chain)
            if "freshness" not in result:
                result = {
                    **result,
                    **provider_metadata(adapter.name, ttl_seconds=SECURITY_CACHE_TTL_SECONDS),
                }
            results.append(result)
        except ProviderSkipped:
            continue
        except Exception as exc:
            logger.warning(
                "security_provider_failed",
                extra={"provider": adapter.name, "token_address": token_address, "error": str(exc)},
            )
    return results


@cached(ttl=SECURITY_CACHE_TTL_SECONDS, namespace="security")
def _get_token_security_audit_cached(token_address: str, chain: str) -> dict[str, Any]:
    if _is_placeholder_address(token_address):
        return _placeholder_audit(token_address)

    results = _provider_results(token_address, chain)
    if not results:
        return _unavailable_audit(token_address)

    is_honeypot = sum(1 for result in results if result.get("is_honeypot")) >= 2
    return {
        "token_address": token_address,
        "is_honeypot": is_honeypot,
        "liquidity_locked_pct": 0.0,
        "contract_verified": all(result.get("contract_verified", True) for result in results),
        "risk_score": max(
            (_risk_score(False, result.get("risk_score")) for result in results), default=50
        ),
        "buy_tax": max((_float(result.get("buy_tax")) for result in results), default=0.0),
        "sell_tax": max((_float(result.get("sell_tax")) for result in results), default=0.0),
        "providers": [str(result["provider"]) for result in results],
        **provider_metadata(
            "security_consensus",
            confidence="medium" if len(results) == 1 else "high",
            ttl_seconds=SECURITY_CACHE_TTL_SECONDS,
        ),
    }


def get_token_security_audit(token_address: str, chain: str = "evm") -> dict[str, Any]:
    logger.info("Getting token security audit for %s", token_address)
    cleaned = token_address.strip()
    result = _get_token_security_audit_cached(cleaned, _infer_chain(cleaned, chain))
    logger.info("Security audit result for %s: %s", token_address, result)
    return result
