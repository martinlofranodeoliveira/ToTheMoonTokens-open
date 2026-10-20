from __future__ import annotations

from time import perf_counter
from typing import Any

import httpx

try:
    from tothemoon_api.config import get_settings
    from tothemoon_api.external.adapters import provider_adapter
    from tothemoon_api.external.health import record_provider_status
except ModuleNotFoundError:  # pragma: no cover - standalone bot without editable API install
    get_settings = None  # type: ignore[assignment]
    provider_adapter = None  # type: ignore[assignment]
    record_provider_status = None  # type: ignore[assignment]

DEXSCREENER_LATEST = "https://api.dexscreener.com/latest/dex/search"
SCANNER_PROVIDER_NAME = "dexscreener"
SCANNER_PROVIDER_CAPABILITIES = {"market_data"}


class ScannerProviderGuardrailError(RuntimeError):
    pass


def _record_scanner_status(
    status: str,
    *,
    started_at: float | None = None,
    last_error: str | None = None,
) -> None:
    if record_provider_status is not None:
        record_provider_status(
            SCANNER_PROVIDER_NAME,
            status=status,
            started_at=started_at,
            last_error=last_error,
        )


def _float(value: object, default: float = 0.0) -> float:
    if not isinstance(value, str | int | float):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_pair(pair: dict[str, Any]) -> dict[str, object] | None:
    base = pair.get("baseToken")
    if not isinstance(base, dict):
        return None
    address = str(base.get("address") or "").strip()
    symbol = str(base.get("symbol") or "").strip()
    if not address:
        return None
    return {
        "address": address,
        "chain": str(pair.get("chainId") or "unknown"),
        "symbol": symbol or None,
        "volume_24h": _float(pair.get("volume", {}).get("h24") if isinstance(pair.get("volume"), dict) else None),
        "momentum": _float(
            pair.get("priceChange", {}).get("h1") if isinstance(pair.get("priceChange"), dict) else None
        ),
        "liquidity_usd": _float(
            pair.get("liquidity", {}).get("usd") if isinstance(pair.get("liquidity"), dict) else None
        ),
    }


def _ensure_safe_scanner_provider() -> None:
    if provider_adapter is None:
        raise ScannerProviderGuardrailError(
            "Scanner requires the API external provider adapter guardrails"
        )
    adapter = provider_adapter(
        SCANNER_PROVIDER_NAME,
        SCANNER_PROVIDER_CAPABILITIES,
        lambda: {},
    )
    adapter.assert_safe_for_paper_mode()
    contract = adapter.contract_payload()
    if not isinstance(contract, dict):
        raise ScannerProviderGuardrailError("Scanner provider contract must be a JSON object")
    if (
        contract.get("name") != SCANNER_PROVIDER_NAME
        or contract.get("capabilities") != sorted(SCANNER_PROVIDER_CAPABILITIES)
        or contract.get("read_only") is not True
        or contract.get("supports_order_submission") is not False
        or contract.get("mainnet_order_submission") is not False
        or contract.get("public_exposure") is not False
    ):
        raise ScannerProviderGuardrailError("Scanner provider violates paper-only guardrails")


class MarketScanner:
    def __init__(self, *, query: str = "USDC", timeout_s: float | None = None):
        self.query = query
        self.timeout_s = (
            float(timeout_s)
            if timeout_s is not None
            else float(get_settings().external_http_timeout_s if get_settings is not None else 10.0)
        )

    async def scan_new_pairs(self) -> list[dict[str, object]]:
        _ensure_safe_scanner_provider()
        started_at = perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.get(DEXSCREENER_LATEST, params={"q": self.query})
                response.raise_for_status()
        except Exception as exc:
            _record_scanner_status("degraded", started_at=started_at, last_error=str(exc))
            raise
        pairs = response.json().get("pairs", [])
        if not isinstance(pairs, list):
            _record_scanner_status(
                "degraded",
                started_at=started_at,
                last_error="DexScreener scanner response did not include pairs",
            )
            return []
        normalized = [normalize_pair(pair) for pair in pairs if isinstance(pair, dict)]
        _record_scanner_status("ok", started_at=started_at)
        return [token for token in normalized if token is not None]

    def filter_promising_tokens(
        self,
        tokens: list[dict[str, object]],
        *,
        min_volume: float = 100_000,
        min_momentum: float = 5.0,
        min_liquidity: float = 50_000,
    ) -> list[dict[str, object]]:
        promising: list[dict[str, object]] = []
        for token in tokens:
            if (
                _float(token.get("volume_24h")) >= min_volume
                and _float(token.get("momentum")) >= min_momentum
                and _float(token.get("liquidity_usd")) >= min_liquidity
            ):
                promising.append(token)
        return promising


async def scan_market(
    *,
    query: str = "USDC",
    limit: int = 25,
    min_volume: float = 100_000,
    min_momentum: float = 5.0,
    min_liquidity: float = 50_000,
) -> list[dict[str, object]]:
    scanner = MarketScanner(query=query)
    pairs = await scanner.scan_new_pairs()
    return scanner.filter_promising_tokens(
        pairs,
        min_volume=min_volume,
        min_momentum=min_momentum,
        min_liquidity=min_liquidity,
    )[:limit]
