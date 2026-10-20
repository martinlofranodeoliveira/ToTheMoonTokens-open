from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _int_score(value: object, default: int = 100) -> int:
    if not isinstance(value, str | int | float):
        return default
    try:
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return default


def _sanitize_skip_reasons(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    reasons: list[str] = []
    for value in values:
        if isinstance(value, str) and value.replace("_", "").replace("-", "").isalnum():
            reasons.append(value[:80])
    return reasons


async def audit_token(
    client: httpx.AsyncClient,
    token_address: str,
    *,
    api_key: str,
) -> dict[str, Any] | None:
    response = await client.get(
        f"/api/v1/tokens/{token_address}/audit",
        headers={"X-API-Key": api_key},
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else None


async def is_token_safe(
    client: httpx.AsyncClient,
    token_address: str,
    *,
    api_key: str,
    max_risk_score: int = 80,
) -> bool:
    decision = await token_safety_decision(
        client,
        token_address,
        api_key=api_key,
        max_risk_score=max_risk_score,
    )
    return bool(decision["safe"])


def token_safety_decision_from_audit(
    audit: dict[str, Any] | None,
    *,
    max_risk_score: int = 80,
) -> dict[str, Any]:
    security = audit.get("security", {}) if audit else {}
    readiness = audit.get("proposal_readiness", {}) if audit else {}
    risk_score = _int_score(readiness.get("risk_score", security.get("risk_score")))
    is_honeypot = bool(readiness.get("is_honeypot", security.get("is_honeypot")))
    provider_degraded = bool(
        readiness.get("provider_degraded")
        or security.get("degraded")
        or security.get("confidence") == "none"
    )
    skip_reasons = _sanitize_skip_reasons(readiness.get("skip_reasons"))
    if readiness and readiness.get("ready") is not True and not skip_reasons:
        skip_reasons.append("proposal_readiness_failed")
    if is_honeypot and "honeypot_detected" not in skip_reasons:
        skip_reasons.append("honeypot_detected")
    if risk_score >= max_risk_score and "risk_score_high" not in skip_reasons:
        skip_reasons.append("risk_score_high")
    if provider_degraded and "provider_degraded" not in skip_reasons:
        skip_reasons.append("provider_degraded")
    safe = bool(audit) and not skip_reasons and not is_honeypot and risk_score < max_risk_score
    return {
        "safe": safe,
        "skip_reasons": skip_reasons,
        "risk_score": risk_score,
        "is_honeypot": is_honeypot,
        "provider_degraded": provider_degraded,
    }


async def token_safety_decision(
    client: httpx.AsyncClient,
    token_address: str,
    *,
    api_key: str,
    max_risk_score: int = 80,
) -> dict[str, Any]:
    try:
        audit = await audit_token(client, token_address, api_key=api_key)
    except Exception as exc:
        logger.warning("token_audit_failed", extra={"token_address": token_address, "error": str(exc)})
        return {
            "safe": False,
            "skip_reasons": ["token_audit_failed"],
            "risk_score": 100,
            "is_honeypot": False,
            "provider_degraded": True,
        }
    return token_safety_decision_from_audit(audit, max_risk_score=max_risk_score)


async def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Audit a token through the TTM SaaS API")
    parser.add_argument("token_address")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8010")
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.api_base_url, timeout=20) as client:
        safe = await is_token_safe(client, args.token_address, api_key=args.api_key)
    print("SAFE" if safe else "UNSAFE")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_cli())
