from __future__ import annotations

import argparse
import asyncio
import logging

import httpx
from auditor import token_safety_decision
from safety import fetch_bot_status
from scanner import scan_market
from trader import create_copilot_proposal

logger = logging.getLogger(__name__)


async def analyze_and_propose(
    client: httpx.AsyncClient,
    token: dict[str, object],
    *,
    api_key: str,
    amount_usd: float,
    mode: str,
) -> dict[str, object] | None:
    address = str(token["address"])
    decision = await token_safety_decision(client, address, api_key=api_key)
    if not decision["safe"]:
        logger.info(
            "token_skipped",
            extra={
                "token_address": address,
                "skip_reasons": decision["skip_reasons"],
                "risk_score": decision["risk_score"],
                "is_honeypot": decision["is_honeypot"],
                "provider_degraded": decision["provider_degraded"],
            },
        )
        return None
    proposal = await create_copilot_proposal(
        client,
        api_key=api_key,
        token=token,
        amount_usd=amount_usd,
        mode=mode,
    )
    return proposal


async def run_once(
    *,
    api_key: str,
    api_base_url: str,
    query: str,
    amount_usd: float,
    limit: int,
    mode: str,
    min_volume: float = 100_000,
    min_momentum: float = 5.0,
    min_liquidity: float = 50_000,
) -> list[dict[str, object]]:
    if mode != "paper":
        raise ValueError("Bot orchestration is paper-only; real orders require manual API approval")

    proposals: list[dict[str, object]] = []
    async with httpx.AsyncClient(base_url=api_base_url, timeout=30) as client:
        status = await fetch_bot_status(client, api_key=api_key)
        logger.info(
            "bot_orchestration_status",
            extra={
                "runtime_mode": status.get("runtime_mode"),
                "order_submission_enabled": status.get("order_submission_enabled"),
                "mainnet_order_submission_enabled": status.get("mainnet_order_submission_enabled"),
                "workflow_status": status.get("workflows", {}),
            },
        )
        workflows = status.get("workflows")
        if isinstance(workflows, dict) and workflows.get("provider_degraded") is True:
            logger.warning("bot_provider_health_degraded", extra={"workflow_status": workflows})
            return []
        tokens = await scan_market(
            query=query,
            limit=limit,
            min_volume=min_volume,
            min_momentum=min_momentum,
            min_liquidity=min_liquidity,
        )
        for token in tokens:
            try:
                proposal = await analyze_and_propose(
                    client,
                    token,
                    api_key=api_key,
                    amount_usd=amount_usd,
                    mode=mode,
                )
            except Exception as exc:
                logger.warning(
                    "proposal_failed",
                    extra={"token_address": token.get("address"), "error": str(exc)},
                )
                continue
            if proposal is not None:
                proposals.append(proposal)
    return proposals


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="TTM copilot market scanner job")
    parser.add_argument("--api-key", required=True, help="TTM SaaS API key")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--query", default="USDC")
    parser.add_argument("--amount", type=float, default=100.0)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--mode", choices=["paper"], default="paper")
    parser.add_argument("--min-volume", type=float, default=100_000)
    parser.add_argument("--min-momentum", type=float, default=5.0)
    parser.add_argument("--min-liquidity", type=float, default=50_000)
    args = parser.parse_args()

    proposals = await run_once(
        api_key=args.api_key,
        api_base_url=args.api_base_url,
        query=args.query,
        amount_usd=args.amount,
        limit=args.limit,
        mode=args.mode,
        min_volume=args.min_volume,
        min_momentum=args.min_momentum,
        min_liquidity=args.min_liquidity,
    )
    print(f"Created {len(proposals)} copilot proposals.")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
