# services/api/src/tothemoon_api/routers/tokens.py
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from tothemoon_api.audit import record_audit_log
from tothemoon_api.auth import require_api_key_scope, verify_api_key
from tothemoon_api.database import get_db
from tothemoon_api.db_models import ApiKey
from tothemoon_api.external.health import summarize_provider_health
from tothemoon_api.external.market import get_token_market_data
from tothemoon_api.external.security import get_token_security_audit
from tothemoon_api.quota import enforce_quota

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tokens", tags=["Tokens"])
VerifiedApiKey = Annotated[ApiKey, Depends(verify_api_key)]
DbSession = Annotated[Session, Depends(get_db)]


def _int_score(value: object, default: int = 100) -> int:
    if not isinstance(value, str | int | float):
        return default
    try:
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return default


def _proposal_readiness(security: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    risk_score = _int_score(security.get("risk_score"))
    is_honeypot = bool(security.get("is_honeypot"))
    provider_degraded = bool(security.get("degraded") or market.get("degraded"))
    provider_names = [
        str(provider)
        for provider in [*security.get("providers", []), security.get("provider"), market.get("provider")]
        if provider
    ]
    health_summary = summarize_provider_health(*provider_names)
    if provider_names:
        provider_degraded = provider_degraded or bool(health_summary["provider_degraded"])

    skip_reasons: list[str] = []
    if is_honeypot:
        skip_reasons.append("honeypot_detected")
    if risk_score >= 80:
        skip_reasons.append("risk_score_high")
    if provider_degraded:
        skip_reasons.append("provider_degraded")
    if security.get("confidence") == "none":
        skip_reasons.append("security_provider_unavailable")

    return {
        "ready": not skip_reasons,
        "skip_reasons": skip_reasons,
        "risk_score": risk_score,
        "is_honeypot": is_honeypot,
        "provider_degraded": provider_degraded,
        "provider_diagnostics": health_summary["provider_diagnostics"],
    }


@router.get("/{token_address}/audit", response_model=dict[str, Any])
def get_token_audit(
    token_address: str,
    api_key: VerifiedApiKey,
    db: DbSession,
    request: Request,
) -> dict[str, Any]:
    logger.info(f"Fetching token audit for {token_address}")
    try:
        require_api_key_scope(api_key, "tokens:audit")
        enforce_quota(api_key, "token_audit", db)
        security_data: dict[str, Any] = get_token_security_audit(token_address)
        market_data: dict[str, Any] = get_token_market_data(token_address)

        proposal_readiness = _proposal_readiness(security_data, market_data)
        result: dict[str, Any] = {
            "token_address": token_address,
            "security": security_data,
            "market": market_data,
            "proposal_readiness": proposal_readiness,
        }
        record_audit_log(
            db,
            org_id=api_key.org_id,
            actor_id=api_key.user_id,
            actor_type="api_key",
            action="token_audit.evaluated",
            target_type="token",
            target_id=token_address,
            request=request,
            after={
                "ready": proposal_readiness["ready"],
                "skip_reasons": proposal_readiness["skip_reasons"],
                "risk_score": proposal_readiness["risk_score"],
                "is_honeypot": proposal_readiness["is_honeypot"],
                "provider_degraded": proposal_readiness["provider_degraded"],
            },
        )
        db.commit()
        logger.info(f"Successfully fetched token audit for {token_address}")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to get token audit for {token_address}: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while fetching token audit data",
        ) from exc
