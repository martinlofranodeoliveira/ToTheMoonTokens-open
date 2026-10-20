from __future__ import annotations

import logging
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
from tothemoon_api.external.security import ProviderSkipped

logger = logging.getLogger(__name__)

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{token_address}"
BIRDEYE_OVERVIEW_URL = "https://public-api.birdeye.so/defi/token_overview"
MARKET_CACHE_TTL_SECONDS = 30


def _placeholder_market(token_address: str) -> dict[str, Any]:
    return {
        "token_address": token_address,
        "price": 1.05,
        "price_usd": 1.05,
        "chain": "evm",
        "volatility_index": 0.02,
        "volume_24h": 1_500_000.0,
        "liquidity_usd": 250_000.0,
        "provider": "local_fallback",
        **provider_metadata(
            "local_fallback",
            status="placeholder",
            confidence="low",
            ttl_seconds=MARKET_CACHE_TTL_SECONDS,
        ),
        "warning": "Placeholder market data is for demos only and is not live pricing.",
    }


def _is_placeholder_address(token_address: str) -> bool:
    lowered = token_address.lower()
    if lowered in {"0xsafe", "0xabc", "0xtest", "0xgain", "0xbase", "0xscam"}:
        return True
    return lowered.startswith("0x") and len(lowered) != 42


def _float(value: object, default: float = 0.0) -> float:
    if not isinstance(value, str | int | float):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _best_dex_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pairs:
        return None
    return max(pairs, key=lambda item: _float(item.get("liquidity", {}).get("usd")))


def _normalize_dex_pair(token_address: str, pair: dict[str, Any]) -> dict[str, Any]:
    price = _float(pair.get("priceUsd") or pair.get("priceNative"), 1.0)
    price_change_h1 = _float(pair.get("priceChange", {}).get("h1"))
    return {
        "token_address": token_address,
        "price": price,
        "price_usd": price,
        "chain": str(pair.get("chainId") or "evm"),
        "symbol": pair.get("baseToken", {}).get("symbol"),
        "name": pair.get("baseToken", {}).get("name"),
        "pair_address": pair.get("pairAddress"),
        "volume_24h": _float(pair.get("volume", {}).get("h24")),
        "liquidity_usd": _float(pair.get("liquidity", {}).get("usd")),
        "volatility_index": round(abs(price_change_h1) / 100, 6),
        "provider": "dexscreener",
        **provider_metadata("dexscreener", ttl_seconds=MARKET_CACHE_TTL_SECONDS),
    }


def _fetch_dexscreener(token_address: str) -> dict[str, Any]:
    settings = get_settings()
    started_at = perf_counter()
    try:
        with httpx.Client(timeout=settings.external_http_timeout_s) as client:
            response = client.get(DEXSCREENER_TOKEN_URL.format(token_address=token_address))
            response.raise_for_status()
        payload = response.json()
        pairs = payload.get("pairs") or []
        if not isinstance(pairs, list):
            raise ValueError("DexScreener response did not include pairs")
        pair = _best_dex_pair([item for item in pairs if isinstance(item, dict)])
        if pair is None:
            raise ValueError("DexScreener returned no token pairs")
        record_provider_status("dexscreener", status="ok", started_at=started_at)
        return _normalize_dex_pair(token_address, pair)
    except Exception as exc:
        record_provider_status(
            "dexscreener",
            status="degraded",
            started_at=started_at,
            last_error=str(exc),
        )
        raise


def _fetch_birdeye(token_address: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.birdeye_api_key:
        record_provider_status("birdeye", status="skipped", last_error="missing API key")
        raise ProviderSkipped("Birdeye API key is not configured")

    started_at = perf_counter()
    try:
        headers = {"X-API-KEY": settings.birdeye_api_key, "accept": "application/json"}
        with httpx.Client(timeout=settings.external_http_timeout_s) as client:
            response = client.get(
                BIRDEYE_OVERVIEW_URL, params={"address": token_address}, headers=headers
            )
            response.raise_for_status()
        data = response.json().get("data") or {}
        if not isinstance(data, dict) or not data:
            raise ValueError("Birdeye returned no token overview")
        price = _float(data.get("price"), 1.0)
        record_provider_status("birdeye", status="ok", started_at=started_at)
        return {
            "token_address": token_address,
            "price": price,
            "price_usd": price,
            "chain": "solana",
            "symbol": data.get("symbol"),
            "name": data.get("name"),
            "volume_24h": _float(data.get("v24hUSD")),
            "liquidity_usd": _float(data.get("liquidity")),
            "volatility_index": round(abs(_float(data.get("priceChange1hPercent"))) / 100, 6),
            "provider": "birdeye",
            **provider_metadata("birdeye", ttl_seconds=MARKET_CACHE_TTL_SECONDS),
        }
    except Exception as exc:
        record_provider_status(
            "birdeye", status="degraded", started_at=started_at, last_error=str(exc)
        )
        raise


def get_market_adapters() -> list[ExternalProviderAdapter]:
    settings = get_settings()
    return [
        provider_adapter("dexscreener", {"market_data"}, _fetch_dexscreener),
        provider_adapter(
            "birdeye",
            {"market_data"},
            _fetch_birdeye,
            requires_api_key=True,
            api_key_configured=bool(settings.birdeye_api_key),
        ),
    ]


@cached(ttl=MARKET_CACHE_TTL_SECONDS, namespace="market")
def _get_token_market_data_cached(token_address: str) -> dict[str, Any]:
    if _is_placeholder_address(token_address):
        return _placeholder_market(token_address)
    for adapter in get_market_adapters():
        try:
            result = call_provider(adapter, token_address)
            if "freshness" not in result:
                result = {
                    **result,
                    **provider_metadata(adapter.name, ttl_seconds=MARKET_CACHE_TTL_SECONDS),
                }
            return result
        except ProviderSkipped:
            logger.info(
                "market_provider_skipped",
                extra={"provider": adapter.name, "token_address": token_address},
            )
        except Exception:
            logger.warning(
                "market_provider_failed",
                exc_info=True,
                extra={"provider": adapter.name, "token_address": token_address},
            )
    return _placeholder_market(token_address)


def get_token_market_data(token_address: str) -> dict[str, Any]:
    logger.info("Getting token market data for %s", token_address)
    result = _get_token_market_data_cached(token_address.strip())
    logger.info("Market data result for %s: %s", token_address, result)
    return result
