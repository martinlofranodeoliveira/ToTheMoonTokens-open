from __future__ import annotations

from typing import Any

import httpx


async def create_copilot_proposal(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    token: dict[str, object],
    amount_usd: float = 100.0,
    mode: str = "paper",
) -> dict[str, Any]:
    if mode != "paper":
        raise ValueError("Bot trader entrypoint is paper-only; real orders require manual API approval")
    momentum = float(token.get("momentum") or 0.0)
    volume = float(token.get("volume_24h") or 0.0)
    liquidity = float(token.get("liquidity_usd") or 0.0)
    rationale = (
        f"Momentum {momentum:.2f}% over 1h with ${volume:,.0f} 24h volume "
        f"and ${liquidity:,.0f} liquidity."
    )
    payload = {
        "token_address": token["address"],
        "chain": token.get("chain") or "unknown",
        "symbol": token.get("symbol"),
        "side": "BUY",
        "amount_usd": amount_usd,
        "score": min(100.0, max(0.0, momentum * 5)),
        "rationale": rationale,
        "mode": mode,
    }
    response = await client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json=payload,
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {"proposal": data}


def apply_risk_management(
    entry_price: float,
    current_price: float,
    stop_loss_pct: float = 5.0,
    take_profit_pct: float = 10.0,
) -> str:
    loss_threshold = entry_price * (1 - stop_loss_pct / 100)
    profit_threshold = entry_price * (1 + take_profit_pct / 100)
    if current_price <= loss_threshold or current_price >= profit_threshold:
        return "SELL"
    return "HOLD"
