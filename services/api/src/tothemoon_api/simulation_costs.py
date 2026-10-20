from __future__ import annotations

import json
import math
import os
from decimal import Decimal
from typing import Any

SLIPPAGE_BPS_BY_CHAIN = {"evm": 50.0, "base": 30.0, "solana": 10.0, "bsc": 40.0}
GAS_USD_BY_CHAIN = {"evm": 8.0, "base": 0.4, "solana": 0.05, "bsc": 0.5}
PERSONAL_SLIPPAGE_BPS_BY_CHAIN = {
    "evm": 80.0,
    "ethereum": 80.0,
    "base": 50.0,
    "solana": 30.0,
    "bsc": 70.0,
    "arbitrum": 50.0,
}
PERSONAL_GAS_BY_CHAIN = {
    "evm": 30.0,
    "ethereum": 30.0,
    "base": 0.50,
    "solana": 0.05,
    "bsc": 0.40,
    "arbitrum": 0.30,
}


def _chain_key(chain: str) -> str:
    return chain.strip().lower().replace("-", "_")


def _positive_finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _mapping_from_env(name: str, defaults: dict[str, float]) -> dict[str, float]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return defaults
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            json_overrides = {
                _chain_key(str(key)): value
                for key, raw_value in parsed.items()
                if (value := _positive_finite(raw_value)) is not None
            }
            return {**defaults, **json_overrides}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    overrides: dict[str, float] = {}
    for item in raw.split(","):
        if ":" not in item:
            continue
        key, raw_value = item.split(":", 1)
        parsed = _positive_finite(raw_value)
        if parsed is not None:
            overrides[_chain_key(key)] = parsed
    return {**defaults, **overrides}


def chain_slippage_bps(chain: str) -> float:
    defaults = (
        PERSONAL_SLIPPAGE_BPS_BY_CHAIN
        if os.getenv("TTM_PERSONAL_TRADING_MODE", "").strip().lower()
        in {"1", "true", "yes", "on"}
        else SLIPPAGE_BPS_BY_CHAIN
    )
    values = _mapping_from_env("SIMULATION_SLIPPAGE_BPS_BY_CHAIN", defaults)
    return values.get(_chain_key(chain), values["evm"])


def chain_gas_usd_estimate(chain: str, amount_usd: float | Decimal) -> Decimal:
    defaults = (
        PERSONAL_GAS_BY_CHAIN
        if os.getenv("TTM_PERSONAL_TRADING_MODE", "").strip().lower()
        in {"1", "true", "yes", "on"}
        else GAS_USD_BY_CHAIN
    )
    values = _mapping_from_env("SIMULATION_GAS_USD_BY_CHAIN", defaults)
    _ = amount_usd
    return Decimal(str(values.get(_chain_key(chain), values["evm"])))
