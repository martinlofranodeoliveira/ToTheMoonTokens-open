from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..audit import record_audit_log
from ..auth import api_key_scope_set, current_user_from_jwt, require_api_key_scope, verify_api_key
from ..config import get_settings
from ..database import get_db
from ..db_models import (
    ApiKey,
    CopilotProposal,
    CopilotScannerRun,
    Organization,
    SimulatedTrade,
    StrategyOutcome,
    TokenDiscoveryHistory,
    User,
    utcnow_naive,
)
from ..external import get_external_adapter_contract
from ..external.adapters import is_paper_only_adapter_contract
from ..external.health import build_provider_readiness
from ..external.security import get_token_security_audit
from ..observability import redact_sensitive_value
from ..provider_credentials import (
    GEMINI_PROVIDER,
    public_gemini_execution_credential_status,
    resolve_gemini_execution_credential,
)
from ..quota import enforce_quota
from ..risk_book import decide_new_paper_position, policy_from_settings, risk_policy_payload
from ..simulation import OrderRequest, TradeSide, close_position, position_response, simulate_trade
from ..simulation_costs import chain_gas_usd_estimate, chain_slippage_bps
from ..strategies import profile_score
from ..tenancy import primary_org_for_user

router = APIRouter(prefix="/api/v1/copilot", tags=["Copilot"])
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user_from_jwt)]
VerifiedApiKey = Annotated[ApiKey, Depends(verify_api_key)]
DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
DEXSCREENER_TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_TOKEN_BOOSTS_LATEST_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
DEXSCREENER_TOKEN_BOOSTS_TOP_URL = "https://api.dexscreener.com/token-boosts/top/v1"
DEXSCREENER_TOKENS_URL = "https://api.dexscreener.com/tokens/v1/{chain_id}/{token_addresses}"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


class CopilotProposalCreate(BaseModel):
    token_address: str = Field(min_length=3, max_length=128)
    chain: str = Field("unknown", min_length=2, max_length=40)
    symbol: str | None = Field(default=None, max_length=40)
    side: TradeSide = TradeSide.BUY
    amount_usd: Decimal = Field(gt=0, le=1_000_000)
    score: float = Field(default=0.0, ge=0, le=100)
    rationale: str = Field(min_length=1, max_length=2000)
    mode: Literal["paper", "real"] = "paper"

    @field_validator("token_address", "chain", "rationale")
    @classmethod
    def require_non_blank_trimmed_value(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned

    @field_validator("symbol")
    @classmethod
    def normalize_optional_trimmed_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class CopilotScanRequest(BaseModel):
    query: str = Field("USDC", min_length=1, max_length=80)
    strategy_profile: str | None = Field(default=None, max_length=40)
    strategy_family: str | None = Field(default=None, max_length=16)
    amount_usd: Decimal = Field(Decimal("100"), gt=0, le=1_000_000)
    limit: int = Field(5, ge=1, le=25)
    min_volume: float = Field(100_000, ge=0)
    min_momentum: float = Field(5.0, ge=-100)
    min_liquidity: float = Field(50_000, ge=0)
    min_market_cap: float = Field(0.0, ge=0)
    execution_mode: Literal["paper_auto", "copilot", "real_approval"] = "paper_auto"
    wallet_address: str | None = Field(default=None, max_length=128)
    wallet_chain_id: str | None = Field(default=None, max_length=40)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query must not be blank")
        return cleaned

    @field_validator("strategy_profile", "strategy_family", "wallet_address", "wallet_chain_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def real_approval_requires_wallet(self) -> CopilotScanRequest:
        if self.execution_mode == "real_approval" and not self.wallet_address:
            raise ValueError("wallet_address is required for real approval mode")
        return self


class ProposalApprovalRequest(BaseModel):
    wallet_address: str | None = Field(default=None, max_length=128)
    wallet_chain_id: str | None = Field(default=None, max_length=40)
    signed_message: str | None = Field(default=None, max_length=2000)
    wallet_signature: str | None = Field(default=None, max_length=4096)

    @field_validator("wallet_address", "wallet_chain_id", "signed_message", "wallet_signature")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ProposalRejectionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_optional_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


def _proposal_payload(proposal: CopilotProposal) -> dict[str, object]:
    return {
        "id": proposal.id,
        "org_id": proposal.org_id,
        "token_address": proposal.token_address,
        "scanner_run_id": proposal.scanner_run_id,
        "chain": proposal.chain,
        "symbol": proposal.symbol,
        "side": proposal.side,
        "amount_usd": float(proposal.amount_usd),
        "score": proposal.score,
        "rationale": proposal.rationale,
        "mode": proposal.mode,
        "status": proposal.status,
        "execution_payload": proposal.execution_payload,
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
        "approved_at": proposal.approved_at,
    }


def _approval_queue_contract() -> dict[str, object]:
    return {
        "queue": "mode2_approval_queue",
        "approval_result": "paper_simulated_intent",
        "rejection_result": "audit_only_no_execution",
        "paper_only": True,
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "live_funds_enabled": False,
        "autonomous_signing_enabled": False,
    }


def _scanner_run_payload(
    run: CopilotScannerRun,
    *,
    paper_outcome: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": run.id,
        "org_id": run.org_id,
        "query": run.query,
        "execution_mode": run.execution_mode,
        "status": run.status,
        "thresholds": run.thresholds_json,
        "candidate_count": run.candidate_count,
        "blocked_count": run.blocked_count,
        "queued_count": run.queued_count,
        "paper_executed_count": run.paper_executed_count,
        "provider_readiness_summary": run.provider_readiness_summary,
        "error_summary": run.error_summary,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
    }
    if paper_outcome is not None:
        payload["paper_outcome"] = paper_outcome
    return payload


def _scanner_run_trade_ids(run: CopilotScannerRun) -> list[int]:
    trade_ids: list[int] = []
    for proposal in run.proposals:
        payload = proposal.execution_payload
        if not isinstance(payload, dict):
            continue
        try:
            trade_id = int(payload.get("trade_id") or 0)
        except (TypeError, ValueError):
            continue
        if trade_id > 0:
            trade_ids.append(trade_id)
    return trade_ids


def _scanner_run_paper_outcome(run: CopilotScannerRun, db: Session) -> dict[str, object]:
    trade_ids = _scanner_run_trade_ids(run)
    if not trade_ids:
        return {
            "trade_count": 0,
            "open_count": 0,
            "closed_count": 0,
            "volume_brl": 0.0,
            "fees_brl": 0.0,
            "realized_pnl_brl": 0.0,
            "unrealized_pnl_brl": 0.0,
            "total_pnl_brl": 0.0,
            "return_pct": 0.0,
        }

    trades = (
        db.query(SimulatedTrade)
        .filter(SimulatedTrade.org_id == run.org_id, SimulatedTrade.id.in_(trade_ids))
        .all()
    )
    volume = sum((trade.amount for trade in trades), Decimal("0"))
    fees = sum((trade.fees_total for trade in trades), Decimal("0"))
    realized = sum(
        (trade.realized_pnl_usd for trade in trades if trade.realized_pnl_usd is not None),
        Decimal("0"),
    )
    unrealized = Decimal("0")
    valuation_errors = 0
    for trade in trades:
        if trade.status != "OPEN":
            continue
        try:
            unrealized += Decimal(str(position_response(trade).unrealized_pnl_usd))
        except Exception:
            valuation_errors += 1
    total = realized + unrealized
    return_pct = (total / volume * Decimal("100")) if volume > 0 else Decimal("0")
    return {
        "trade_count": len(trades),
        "open_count": sum(1 for trade in trades if trade.status == "OPEN"),
        "closed_count": sum(1 for trade in trades if trade.status == "CLOSED"),
        "volume_brl": round(float(volume), 6),
        "fees_brl": round(float(fees), 6),
        "realized_pnl_brl": round(float(realized), 6),
        "unrealized_pnl_brl": round(float(unrealized), 6),
        "total_pnl_brl": round(float(total), 6),
        "return_pct": round(float(return_pct), 6),
        "valuation_errors": valuation_errors,
    }


def _decimal_or_none(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _strategy_name_from_run(run: CopilotScannerRun) -> str:
    thresholds = run.thresholds_json if isinstance(run.thresholds_json, dict) else {}
    return str(thresholds.get("strategy_profile") or "default")[:64]


def _strategy_family_from_run(run: CopilotScannerRun) -> str | None:
    thresholds = run.thresholds_json if isinstance(run.thresholds_json, dict) else {}
    family = str(thresholds.get("strategy_family") or "").strip().upper()
    return family[:16] or None


def _record_strategy_outcome(
    db: Session,
    *,
    scanner_run: CopilotScannerRun,
    discovery: TokenDiscoveryHistory,
    candidate: dict[str, object],
    decision: str,
    paper_position_id: int | None = None,
    entry_price: Decimal | None = None,
    paper_pnl_brl: Decimal | None = None,
    classification: str | None = None,
    block_reason: str | None = None,
    block_stage: str | None = None,
) -> StrategyOutcome:
    outcome = (
        db.query(StrategyOutcome)
        .filter(
            StrategyOutcome.org_id == scanner_run.org_id,
            StrategyOutcome.scanner_run_id == scanner_run.id,
            StrategyOutcome.candidate_address == discovery.token_address,
        )
        .first()
    )
    if outcome is None:
        outcome = StrategyOutcome(
            org_id=scanner_run.org_id,
            scanner_run_id=scanner_run.id,
            candidate_address=discovery.token_address,
            chain=discovery.chain,
            created_at=utcnow_naive(),
        )
        db.add(outcome)
    outcome.profile_name = _strategy_name_from_run(scanner_run)
    outcome.family = _strategy_family_from_run(scanner_run)
    outcome.paper_position_id = paper_position_id
    outcome.decision = decision
    outcome.entry_price = entry_price or _decimal_or_none(candidate.get("price_usd"))
    outcome.paper_pnl_brl = paper_pnl_brl
    outcome.classification = classification
    outcome.metadata_json = {
        "symbol": discovery.symbol,
        "score": discovery.score,
        "recommendation": discovery.recommendation,
        "volume_24h": candidate.get("volume_24h"),
        "momentum_h1": candidate.get("momentum_h1"),
        "liquidity_usd": candidate.get("liquidity_usd"),
        "market_cap_usd": candidate.get("market_cap_usd"),
        "source": candidate.get("source"),
        "audit": candidate.get("_audit"),
        "paper_roundtrip_cost": candidate.get("_paper_roundtrip_cost"),
        "paper_slot_allocation": candidate.get("_paper_slot_allocation"),
        "paper_symbol_allocation": candidate.get("_paper_symbol_allocation"),
        "shadow_real_execution": candidate.get("_shadow_real_execution"),
        "block_reason": block_reason,
        "block_stage": block_stage,
    }
    outcome.updated_at = utcnow_naive()
    return outcome


def _strategy_outcome_payload(outcome: StrategyOutcome) -> dict[str, object]:
    return {
        "id": outcome.id,
        "profile_name": outcome.profile_name,
        "family": outcome.family,
        "scanner_run_id": outcome.scanner_run_id,
        "paper_position_id": outcome.paper_position_id,
        "candidate_address": outcome.candidate_address,
        "chain": outcome.chain,
        "decision": outcome.decision,
        "entry_price": float(outcome.entry_price) if outcome.entry_price is not None else None,
        "price_24h": float(outcome.price_24h) if outcome.price_24h is not None else None,
        "price_7d": float(outcome.price_7d) if outcome.price_7d is not None else None,
        "paper_pnl_brl": (
            float(outcome.paper_pnl_brl) if outcome.paper_pnl_brl is not None else None
        ),
        "classification": outcome.classification,
        "metadata": outcome.metadata_json,
        "created_at": outcome.created_at,
        "updated_at": outcome.updated_at,
    }


def _counter_from_outcome_metadata(
    rows: list[StrategyOutcome],
    key: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        value = metadata.get(key)
        if value:
            counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True)[:10])


def _counter_from_nested_outcome_metadata(
    rows: list[StrategyOutcome],
    parent_key: str,
    child_key: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        parent = metadata.get(parent_key)
        if not isinstance(parent, dict):
            continue
        value = parent.get(child_key)
        if value:
            counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True)[:10])


def _aggregate_strategy_outcomes(rows: list[StrategyOutcome], db: Session, org_id: int) -> dict[str, object]:
    grouped: dict[str, list[StrategyOutcome]] = {}
    family_grouped: dict[str, list[StrategyOutcome]] = {}
    for row in rows:
        grouped.setdefault(row.profile_name, []).append(row)
        family_grouped.setdefault(row.family or "unknown", []).append(row)

    by_profile: dict[str, dict[str, object]] = {}
    for profile, items in grouped.items():
        scored = [row for row in items if row.paper_pnl_brl is not None]
        wins = [row for row in scored if row.paper_pnl_brl is not None and row.paper_pnl_brl > 0]
        losses = [row for row in scored if row.paper_pnl_brl is not None and row.paper_pnl_brl <= 0]
        rugs = [row for row in scored if row.classification == "rug"]
        pnl = sum((row.paper_pnl_brl for row in scored if row.paper_pnl_brl is not None), Decimal("0"))
        score = profile_score(profile, db, org_id=org_id)
        block_reasons = _counter_from_outcome_metadata(items, "block_reason")
        block_stages = _counter_from_outcome_metadata(items, "block_stage")
        cost_tiers = _counter_from_nested_outcome_metadata(
            items,
            "paper_roundtrip_cost",
            "cost_tier",
        )
        by_profile[profile] = {
            "profile_name": profile,
            "family": next((row.family for row in items if row.family), None),
            "outcomes": len(items),
            "scored_outcomes": len(scored),
            "approved": sum(1 for row in items if row.decision == "approved"),
            "rejected_audit": sum(1 for row in items if row.decision == "rejected_audit"),
            "rejected_risk": sum(1 for row in items if row.decision == "rejected_risk"),
            "queued": sum(1 for row in items if row.decision == "queued"),
            "wins": len(wins),
            "losses": len(losses),
            "rugs": len(rugs),
            "win_rate": round(len(wins) / len(scored), 6) if scored else 0.0,
            "rug_rate": round(len(rugs) / len(scored), 6) if scored else 0.0,
            "paper_pnl_brl": round(float(pnl), 6),
            "score": score,
            "block_reasons": block_reasons,
            "block_stages": block_stages,
            "cost_tiers": cost_tiers,
            "latest": _strategy_outcome_payload(items[0]),
        }

    def family_payload(family: str, items: list[StrategyOutcome]) -> dict[str, object]:
        scored = [row for row in items if row.paper_pnl_brl is not None]
        wins = [row for row in scored if row.paper_pnl_brl is not None and row.paper_pnl_brl > 0]
        rugs = [row for row in scored if row.classification == "rug"]
        pnl = sum((row.paper_pnl_brl for row in scored if row.paper_pnl_brl is not None), Decimal("0"))
        profile_names = {row.profile_name for row in items}
        block_reasons = _counter_from_outcome_metadata(items, "block_reason")
        block_stages = _counter_from_outcome_metadata(items, "block_stage")
        cost_tiers = _counter_from_nested_outcome_metadata(
            items,
            "paper_roundtrip_cost",
            "cost_tier",
        )
        return {
            "family": family,
            "outcomes": len(items),
            "scored_outcomes": len(scored),
            "profiles": len({row.profile_name for row in items}),
            "approved": sum(1 for row in items if row.decision == "approved"),
            "rejected_audit": sum(1 for row in items if row.decision == "rejected_audit"),
            "rejected_risk": sum(1 for row in items if row.decision == "rejected_risk"),
            "win_rate": round(len(wins) / len(scored), 6) if scored else 0.0,
            "rug_rate": round(len(rugs) / len(scored), 6) if scored else 0.0,
            "paper_pnl_brl": round(float(pnl), 6),
            "avg_score": round(
                sum(float(by_profile[name]["score"]) for name in profile_names)
                / max(len(profile_names), 1),
                6,
            ),
            "block_reasons": block_reasons,
            "block_stages": block_stages,
            "cost_tiers": cost_tiers,
        }

    by_family = {
        family: family_payload(family, items) for family, items in family_grouped.items()
    }

    min_outcomes = _env_int("TTM_TOURNAMENT_MIN_TRADES_FOR_SCORE", 10)
    dead_runs = _env_int("TTM_TOURNAMENT_DEAD_THRESHOLD_RUNS", 50)
    rug_kill = _env_float("TTM_TOURNAMENT_RUG_RATE_KILL", 0.30)
    top3 = sorted(
        (
            item
            for item in by_profile.values()
            if int(item["scored_outcomes"]) >= min_outcomes and float(item["score"]) > 0
        ),
        key=lambda item: float(item["score"]),
        reverse=True,
    )[:3]
    dead = [
        item
        for item in by_profile.values()
        if (
            int(item["scored_outcomes"]) >= min_outcomes
            and float(item["rug_rate"]) > rug_kill
        )
        or (int(item["outcomes"]) >= dead_runs and float(item["score"]) <= 0)
    ]
    alive = [
        item
        for item in by_profile.values()
        if item["profile_name"] not in {row["profile_name"] for row in dead}
        and float(item["score"]) > 0
    ]
    exploring = [
        item
        for item in by_profile.values()
        if item["profile_name"] not in {row["profile_name"] for row in dead}
        and int(item["scored_outcomes"]) < min_outcomes
    ]
    return {
        "by_profile": by_profile,
        "by_family": by_family,
        "top3": top3,
        "alive": alive,
        "exploring": exploring,
        "dead": dead,
    }


def _redact_provider_context(value: object) -> object:
    if isinstance(value, dict):
        clean: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(marker in key_text.lower() for marker in ("token", "secret", "password", "api_key")):
                clean[key_text] = "[REDACTED]"
            else:
                clean[key_text] = _redact_provider_context(item)
        return clean
    if isinstance(value, list):
        return [_redact_provider_context(item) for item in value]
    return redact_sensitive_value(value)


def _paper_run_execution_result(run: CopilotScannerRun) -> dict[str, object]:
    return {
        "paper_only": True,
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "candidate_count": run.candidate_count,
        "blocked_count": run.blocked_count,
        "queued_count": run.queued_count,
        "paper_executed_count": run.paper_executed_count,
        "error_summary": run.error_summary,
    }


def _paper_scanner_run_evidence_payload(run: CopilotScannerRun) -> dict[str, object]:
    return {
        "run_id": run.id,
        "mode": run.execution_mode,
        "status": run.status,
        "paper_execution_result": _paper_run_execution_result(run),
        "audit_evidence_timestamp": run.completed_at or run.created_at,
        "provider_context": _redact_provider_context(run.provider_readiness_summary or {}),
    }


def _scanner_thresholds(payload: CopilotScanRequest) -> dict[str, object]:
    settings = get_settings()
    thresholds: dict[str, object] = {
        "limit": payload.limit,
        "strategy_profile": payload.strategy_profile or "default",
        "strategy_family": payload.strategy_family or "unknown",
        "min_volume": payload.min_volume,
        "min_momentum": payload.min_momentum,
        "min_liquidity": payload.min_liquidity,
        "min_market_cap": payload.min_market_cap,
        "amount_usd": str(payload.amount_usd),
    }
    if settings.personal_trading_mode:
        thresholds.update(
            {
                "currency": "BRL",
                "paper_capital_brl": settings.paper_capital_brl,
                "paper_trade_amount_brl": settings.paper_trade_amount_brl,
                "real_min_trade_amount_brl": settings.real_min_trade_amount_brl,
                "paper_high_cost_scout_pct": settings.paper_high_cost_scout_pct,
                "paper_max_roundtrip_cost_pct": settings.paper_max_roundtrip_cost_pct,
                "accelerated_research": settings.paper_accelerated_research,
            }
        )
    return thresholds


def _scanner_error_summary(exc: Exception) -> str:
    summary = f"{type(exc).__name__}: {exc}"
    redacted = redact_sensitive_value(summary)
    return str(redacted)[:500]


def _paper_execution_decision_payload(
    *,
    scanner_run: CopilotScannerRun,
    proposal: CopilotProposal | None,
    discovery: TokenDiscoveryHistory,
    execution_mode: str,
    decision: str,
    reason: str,
    trade_id: object | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "scanner_run_id": scanner_run.id,
        "execution_mode": execution_mode,
        "decision": decision,
        "reason": reason,
        "token_address": discovery.token_address,
        "chain": discovery.chain,
        "symbol": discovery.symbol,
        "score": discovery.score,
        "recommendation": discovery.recommendation,
        "rationale": discovery.rationale,
    }
    if proposal is not None:
        payload.update(
            {
                "proposal_id": proposal.id,
                "proposal_status": proposal.status,
                "proposal_mode": proposal.mode,
                "amount_usd": str(proposal.amount_usd),
            }
        )
    if trade_id is not None:
        payload["trade_id"] = trade_id
    return payload


def _safe_provider_readiness_summary(status: dict[str, object]) -> dict[str, object]:
    provider_readiness = status.get("provider_readiness")
    if not isinstance(provider_readiness, dict):
        provider_readiness = {}
    providers = status.get("providers")
    gemini_status = providers.get(GEMINI_PROVIDER) if isinstance(providers, dict) else None
    if not isinstance(gemini_status, dict):
        gemini_status = {}
    readiness = status.get("readiness")
    if not isinstance(readiness, dict):
        readiness = {}
    return {
        "status": provider_readiness.get("status"),
        "paper_only": provider_readiness.get("paper_only"),
        "order_submission_enabled": provider_readiness.get("order_submission_enabled"),
        "mainnet_order_submission_enabled": provider_readiness.get(
            "mainnet_order_submission_enabled"
        ),
        "blocking_reasons": readiness.get("blocking_reasons", []),
        "gemini": {
            "configured": gemini_status.get("configured"),
            "source": gemini_status.get("source"),
            "key_hint": gemini_status.get("key_hint"),
        },
    }


def _float(value: object, default: float = 0.0) -> float:
    if not isinstance(value, str | int | float):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_scan_pair(pair: dict[str, object]) -> dict[str, object] | None:
    base_token = pair.get("baseToken")
    if not isinstance(base_token, dict):
        return None
    address = str(base_token.get("address") or "").strip()
    if not address:
        return None
    volume = pair.get("volume") if isinstance(pair.get("volume"), dict) else {}
    price_change = pair.get("priceChange") if isinstance(pair.get("priceChange"), dict) else {}
    liquidity = pair.get("liquidity") if isinstance(pair.get("liquidity"), dict) else {}
    return {
        "token_address": address,
        "chain": str(pair.get("chainId") or "unknown").strip().lower(),
        "symbol": str(base_token.get("symbol") or "").strip().upper() or None,
        "volume_24h": _float(volume.get("h24") if isinstance(volume, dict) else None),
        "momentum_h1": _float(price_change.get("h1") if isinstance(price_change, dict) else None),
        "liquidity_usd": _float(liquidity.get("usd") if isinstance(liquidity, dict) else None),
        "market_cap_usd": _float(pair.get("marketCap") or pair.get("fdv")),
        "price_usd": _float(pair.get("priceUsd")),
        "source": str(pair.get("_source") or "dexscreener_search"),
    }


def _free_discovery_enabled(payload: CopilotScanRequest) -> bool:
    settings = get_settings()
    if not settings.scanner_free_discovery_enabled:
        return False
    family = (payload.strategy_family or "").strip().upper()
    query = payload.query.strip().lower()
    return family in {"B", "F"} or query in {"pump", "meme", "ai", "rwa", "depin", "game"}


def _discovery_text(item: dict[str, object]) -> str:
    values = [
        item.get("chainId"),
        item.get("tokenAddress"),
        item.get("description"),
        item.get("url"),
        item.get("header"),
    ]
    links = item.get("links")
    if isinstance(links, list):
        for link in links:
            if isinstance(link, dict):
                values.extend([link.get("label"), link.get("url")])
    return " ".join(str(value) for value in values if value).lower()


def _fetch_free_discovery_token_refs(
    client: httpx.Client,
    payload: CopilotScanRequest,
) -> list[tuple[str, str]]:
    if not _free_discovery_enabled(payload):
        return []
    refs: list[tuple[str, str]] = []
    query = payload.query.strip().lower()
    urls = (
        DEXSCREENER_TOKEN_PROFILES_URL,
        DEXSCREENER_TOKEN_BOOSTS_LATEST_URL,
        DEXSCREENER_TOKEN_BOOSTS_TOP_URL,
    )
    for url in urls:
        try:
            response = client.get(url)
            response.raise_for_status()
            rows = response.json()
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            chain_id = str(item.get("chainId") or "").strip()
            token_address = str(item.get("tokenAddress") or "").strip()
            if not chain_id or not token_address:
                continue
            if payload.strategy_family == "F" and query not in _discovery_text(item):
                continue
            refs.append((chain_id, token_address))
            if len(refs) >= 40:
                return refs
    return refs


def _fetch_pairs_for_token_refs(
    client: httpx.Client,
    refs: list[tuple[str, str]],
) -> list[dict[str, object]]:
    if not refs:
        return []
    grouped: dict[str, list[str]] = {}
    for chain_id, token_address in refs:
        addresses = grouped.setdefault(chain_id, [])
        if token_address not in addresses:
            addresses.append(token_address)

    pairs: list[dict[str, object]] = []
    for chain_id, addresses in grouped.items():
        for offset in range(0, len(addresses), 30):
            chunk = addresses[offset : offset + 30]
            try:
                response = client.get(
                    DEXSCREENER_TOKENS_URL.format(
                        chain_id=chain_id,
                        token_addresses=",".join(chunk),
                    )
                )
                response.raise_for_status()
                rows = response.json()
            except Exception:
                continue
            if isinstance(rows, list):
                for item in rows:
                    if isinstance(item, dict):
                        item["_source"] = "dexscreener_free_discovery"
                        pairs.append(item)
    return pairs


def _scan_market_candidates(payload: CopilotScanRequest) -> list[dict[str, object]]:
    settings = get_settings()
    with httpx.Client(timeout=settings.external_http_timeout_s) as client:
        response = client.get(DEXSCREENER_SEARCH_URL, params={"q": payload.query})
        response.raise_for_status()
        pairs = response.json().get("pairs") or []
        discovery_pairs = _fetch_pairs_for_token_refs(
            client,
            _fetch_free_discovery_token_refs(client, payload),
        )
    if not isinstance(pairs, list):
        raise HTTPException(status_code=502, detail="DexScreener search returned no pair list")
    pairs = [*pairs, *discovery_pairs]
    candidates = [
        normalized
        for pair in pairs
        if isinstance(pair, dict)
        if (normalized := _normalize_scan_pair(pair)) is not None
    ]
    deduped: dict[tuple[str, str], dict[str, object]] = {}
    for item in candidates:
        key = (str(item.get("chain")), str(item.get("token_address")).lower())
        previous = deduped.get(key)
        if previous is None or _float(item.get("liquidity_usd")) > _float(
            previous.get("liquidity_usd")
        ):
            deduped[key] = item
    filtered = [
        item
        for item in deduped.values()
        if _float(item.get("volume_24h")) >= payload.min_volume
        and _float(item.get("momentum_h1")) >= payload.min_momentum
        and _float(item.get("liquidity_usd")) >= payload.min_liquidity
        and _float(item.get("market_cap_usd")) >= payload.min_market_cap
    ]
    return sorted(
        filtered,
        key=lambda item: (
            _float(item.get("momentum_h1")),
            _float(item.get("volume_24h")),
            _float(item.get("liquidity_usd")),
        ),
        reverse=True,
    )[: payload.limit]


def _score_candidate(candidate: dict[str, object], audit: dict[str, object]) -> float:
    volume_score = min(_float(candidate.get("volume_24h")) / 1_000_000 * 35, 35)
    momentum_score = min(max(_float(candidate.get("momentum_h1")), 0) / 20 * 30, 30)
    liquidity_score = min(_float(candidate.get("liquidity_usd")) / 250_000 * 25, 25)
    risk_penalty = min(_float(audit.get("risk_score")), 100) * 0.25
    return round(max(0, min(100, volume_score + momentum_score + liquidity_score + 10 - risk_penalty)), 2)


def _paper_roundtrip_cost_viability(
    *,
    candidate: dict[str, object],
    audit: dict[str, object],
    amount_brl: Decimal,
) -> dict[str, object]:
    amount = Decimal(str(amount_brl))
    chain = str(candidate.get("chain") or "evm")
    if amount <= 0:
        return {"allowed": False, "reason": "amount_must_be_positive", "chain": chain}
    gas_brl = chain_gas_usd_estimate(chain, amount) * Decimal("2")
    slippage_bps = Decimal(str(chain_slippage_bps(chain)))
    slippage_brl = amount * slippage_bps * Decimal("2") / Decimal("10000")
    buy_tax_pct = Decimal(str(_float(audit.get("buy_tax"))))
    sell_tax_pct = Decimal(str(_float(audit.get("sell_tax"))))
    token_tax_brl = amount * (buy_tax_pct + sell_tax_pct) / Decimal("100")
    total_cost_brl = gas_brl + slippage_brl + token_tax_brl
    cost_pct = total_cost_brl / amount * Decimal("100")
    settings = get_settings()
    scout_pct = Decimal(str(settings.paper_high_cost_scout_pct))
    max_cost_pct = Decimal(str(settings.paper_max_roundtrip_cost_pct))
    allowed = cost_pct <= max_cost_pct
    cost_tier = "normal" if cost_pct <= scout_pct else "high_cost_scout"
    return {
        "allowed": allowed,
        "reason": "allowed" if allowed else "roundtrip_cost_pct_exceeded",
        "cost_tier": cost_tier if allowed else "unviable",
        "chain": chain,
        "amount_brl": float(amount),
        "gas_brl_roundtrip": round(float(gas_brl), 6),
        "slippage_bps_each_side": float(slippage_bps),
        "slippage_brl_roundtrip": round(float(slippage_brl), 6),
        "buy_tax_pct": float(buy_tax_pct),
        "sell_tax_pct": float(sell_tax_pct),
        "token_tax_brl_roundtrip": round(float(token_tax_brl), 6),
        "estimated_roundtrip_cost_brl": round(float(total_cost_brl), 6),
        "estimated_roundtrip_cost_pct": round(float(cost_pct), 6),
        "high_cost_scout_pct": float(scout_pct),
        "max_roundtrip_cost_pct": float(max_cost_pct),
    }


def _rationale(candidate: dict[str, object], audit: dict[str, object], score: float) -> str:
    return (
        f"Scanner encontrou momentum h1={_float(candidate.get('momentum_h1')):.2f}%, "
        f"volume 24h=${_float(candidate.get('volume_24h')):,.0f}, "
        f"liquidez=${_float(candidate.get('liquidity_usd')):,.0f}. "
        f"Consenso de seguranca: risk_score={_float(audit.get('risk_score')):.0f}, "
        f"honeypot={bool(audit.get('is_honeypot'))}. Score final {score:.2f}/100."
    )


def _manual_real_gate_payload(payload: CopilotScanRequest) -> dict[str, object]:
    return {
        "manual_gate": "metamask_signature_required",
        "wallet_address": payload.wallet_address,
        "wallet_chain_id": payload.wallet_chain_id,
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "note": "Real-money submission is not performed by this endpoint.",
    }


def _manual_approval_payload(payload: ProposalApprovalRequest | None) -> dict[str, object] | None:
    if payload is None:
        return None
    evidence = {
        "wallet_address": payload.wallet_address,
        "wallet_chain_id": payload.wallet_chain_id,
        "signed_message": payload.signed_message,
        "wallet_signature": payload.wallet_signature,
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "next_step": "Manual execution remains outside the autonomous runtime.",
    }
    return {key: value for key, value in evidence.items() if value is not None}


def _select_bot_api_key(org_id: int, db: Session) -> ApiKey:
    keys = (
        db.query(ApiKey)
        .filter(ApiKey.org_id == org_id, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        .all()
    )
    required = {"copilot:proposals", "simulate:order"}
    for api_key in keys:
        if required.issubset(api_key_scope_set(api_key)):
            return api_key
    raise HTTPException(
        status_code=409,
        detail=(
            "Create an active API key with copilot:proposals and simulate:order scopes "
            "before running the autonomous paper scanner."
        ),
    )


def _sync_scanner_run_counts(scanner_run: CopilotScannerRun, db: Session) -> None:
    proposals = (
        db.query(CopilotProposal.status)
        .filter(
            CopilotProposal.org_id == scanner_run.org_id,
            CopilotProposal.scanner_run_id == scanner_run.id,
        )
        .all()
    )
    scanner_run.queued_count = sum(1 for (status,) in proposals if status == "pending")
    scanner_run.paper_executed_count = sum(1 for (status,) in proposals if status == "executed")


def _org_for_api_key(api_key: ApiKey) -> Organization:
    if api_key.organization is None:
        raise HTTPException(status_code=403, detail="API key is not scoped to an organization")
    return api_key.organization


def _bot_workflow_status(org: Organization) -> dict[str, object]:
    return {
        "scanner": "api_adapter_ready",
        "auditor": "api_adapter_ready",
        "trader": "paper_proposal_only",
        "consecutive_failures": org.bot_consecutive_failures,
        "circuit_breaker_tripped": org.bot_consecutive_failures >= 3,
    }


def _today_new_paper_capital(org_id: int, db: Session) -> Decimal:
    start = utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
    value = (
        db.query(func.coalesce(func.sum(SimulatedTrade.amount), 0))
        .filter(
            SimulatedTrade.org_id == org_id,
            SimulatedTrade.side == TradeSide.BUY.value,
            SimulatedTrade.created_at >= start,
        )
        .scalar()
    )
    return Decimal(str(value or 0))


def _open_paper_capital(org_id: int, db: Session) -> Decimal:
    value = (
        db.query(func.coalesce(func.sum(SimulatedTrade.amount), 0))
        .filter(
            SimulatedTrade.org_id == org_id,
            SimulatedTrade.side == TradeSide.BUY.value,
            SimulatedTrade.status == "OPEN",
        )
        .scalar()
    )
    return Decimal(str(value or 0))


def _open_paper_positions_count(org_id: int, db: Session) -> int:
    return int(
        db.query(func.count(SimulatedTrade.id))
        .filter(SimulatedTrade.org_id == org_id, SimulatedTrade.status == "OPEN")
        .scalar()
        or 0
    )


def _paper_slot_category(strategy_family: object) -> str:
    return "onchain_scout" if str(strategy_family or "").strip().upper() == "B" else "liquid_binance"


def _paper_slot_budgets(settings: object) -> dict[str, int]:
    return {
        "liquid_binance": int(getattr(settings, "paper_liquid_slots", 7)),
        "onchain_scout": int(getattr(settings, "paper_onchain_scout_slots", 3)),
    }


def _open_paper_slot_counts(org_id: int, db: Session) -> dict[str, int]:
    counts = {"liquid_binance": 0, "onchain_scout": 0}
    rows = (
        db.query(StrategyOutcome.paper_position_id, StrategyOutcome.family)
        .join(SimulatedTrade, StrategyOutcome.paper_position_id == SimulatedTrade.id)
        .filter(
            StrategyOutcome.org_id == org_id,
            SimulatedTrade.org_id == org_id,
            SimulatedTrade.status == "OPEN",
        )
        .all()
    )
    counted_positions: set[int] = set()
    for paper_position_id, family in rows:
        if paper_position_id is None or paper_position_id in counted_positions:
            continue
        counted_positions.add(int(paper_position_id))
        counts[_paper_slot_category(family)] += 1

    legacy_open_positions = max(_open_paper_positions_count(org_id, db) - len(counted_positions), 0)
    counts["liquid_binance"] += legacy_open_positions
    return counts


def _paper_slot_allocation_viability(
    *,
    org_id: int,
    db: Session,
    strategy_family: object,
    settings: object,
) -> dict[str, object]:
    category = _paper_slot_category(strategy_family)
    counts = _open_paper_slot_counts(org_id, db)
    budgets = _paper_slot_budgets(settings)
    current = counts[category]
    budget = budgets[category]
    allowed = current < budget
    reason = "allowed" if allowed else f"paper_{category}_slots_full"
    return {
        "allowed": allowed,
        "reason": reason,
        "category": category,
        "strategy_family": str(strategy_family or "unknown").strip().upper() or "unknown",
        "open_slots": current,
        "slot_budget": budget,
        "slot_counts": counts,
        "slot_budgets": budgets,
    }


def _candidate_symbol(candidate: dict[str, object]) -> str:
    symbol = str(candidate.get("symbol") or candidate.get("token_address") or "UNKNOWN")
    cleaned = "".join(character for character in symbol.upper() if character.isalnum())
    return cleaned[:40] or "UNKNOWN"


def _open_paper_symbol_counts(org_id: int, db: Session) -> dict[str, int]:
    rows = (
        db.query(StrategyOutcome.paper_position_id, StrategyOutcome.metadata_json)
        .join(SimulatedTrade, StrategyOutcome.paper_position_id == SimulatedTrade.id)
        .filter(
            StrategyOutcome.org_id == org_id,
            SimulatedTrade.org_id == org_id,
            SimulatedTrade.status == "OPEN",
        )
        .all()
    )
    counts: dict[str, int] = {}
    counted_positions: set[int] = set()
    for paper_position_id, metadata in rows:
        if paper_position_id is None or paper_position_id in counted_positions:
            continue
        counted_positions.add(int(paper_position_id))
        symbol = "UNKNOWN"
        if isinstance(metadata, dict):
            symbol = _candidate_symbol(metadata)
        counts[symbol] = counts.get(symbol, 0) + 1
    return counts


def _paper_symbol_allocation_viability(
    *,
    org_id: int,
    db: Session,
    candidate: dict[str, object],
    settings: object,
) -> dict[str, object]:
    symbol = _candidate_symbol(candidate)
    counts = _open_paper_symbol_counts(org_id, db)
    current = counts.get(symbol, 0)
    budget = int(getattr(settings, "paper_max_open_per_symbol", 2))
    allowed = current < budget
    return {
        "allowed": allowed,
        "reason": "allowed" if allowed else "paper_symbol_slots_full",
        "symbol": symbol,
        "open_symbol_positions": current,
        "symbol_position_budget": budget,
        "open_symbol_counts": counts,
    }


def _shadow_real_execution_route(strategy_family: object) -> dict[str, object]:
    category = _paper_slot_category(strategy_family)
    if category == "onchain_scout":
        return {
            "mode": "shadow_real",
            "category": category,
            "future_execution_surface": "self_custody_wallet",
            "future_wallets": ["metamask", "phantom"],
            "real_order_submission_enabled": False,
            "paper_only": True,
        }
    return {
        "mode": "shadow_real",
        "category": category,
        "future_execution_surface": "binance_spot",
        "future_wallets": ["binance_account"],
        "real_order_submission_enabled": False,
        "paper_only": True,
    }


def _paper_cycle_state(org: Organization, db: Session) -> dict[str, object]:
    settings = get_settings()
    open_capital = _open_paper_capital(org.id, db)
    open_positions = _open_paper_positions_count(org.id, db)
    open_slot_counts = _open_paper_slot_counts(org.id, db)
    slot_budgets = _paper_slot_budgets(settings)
    cycle_capital = Decimal(str(settings.paper_capital_brl))
    trade_amount = Decimal(str(settings.paper_trade_amount_brl))
    target_positions = int(cycle_capital // trade_amount) if trade_amount > 0 else 0
    target_positions = min(target_positions, int(settings.paper_max_open_positions))
    return {
        "currency": "BRL",
        "cycle_capital_brl": float(cycle_capital),
        "trade_amount_brl": float(trade_amount),
        "future_real_min_trade_amount_brl": float(settings.real_min_trade_amount_brl),
        "target_positions_per_cycle": target_positions,
        "position_pct_per_trade": float((trade_amount / cycle_capital) * Decimal("100"))
        if cycle_capital > 0
        else 0.0,
        "open_positions_count": open_positions,
        "slot_budgets": slot_budgets,
        "open_slot_counts": open_slot_counts,
        "symbol_position_budget": int(settings.paper_max_open_per_symbol),
        "open_symbol_counts": _open_paper_symbol_counts(org.id, db),
        "open_capital_brl": float(open_capital),
        "remaining_cycle_capital_brl": float(max(cycle_capital - open_capital, Decimal("0"))),
        "reset_due": open_capital >= cycle_capital
        or (target_positions > 0 and open_positions >= target_positions),
    }


def _gemini_provider_status(org: Organization, db: Session) -> dict[str, object]:
    settings = get_settings()
    execution_credential = resolve_gemini_execution_credential(db, org.id, settings)
    status = public_gemini_execution_credential_status(execution_credential)
    status["provider"] = GEMINI_PROVIDER
    return status


def _bot_readiness_summary(
    *,
    gemini_status: dict[str, object],
    provider_readiness: dict[str, object],
    workflows: dict[str, object],
    adapter_contract_enforced: bool,
) -> dict[str, object]:
    blocking_reasons: list[str] = []
    if gemini_status.get("configured") is not True:
        blocking_reasons.append("gemini_not_configured")
    if provider_readiness.get("status") != "healthy":
        blocking_reasons.append("provider_readiness_not_healthy")
    if workflows.get("circuit_breaker_tripped") is True:
        blocking_reasons.append("circuit_breaker_tripped")
    if not adapter_contract_enforced:
        blocking_reasons.append("external_adapter_contract_unsafe")
    return {
        "ready": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "paper_only": True,
    }


def _paper_bot_status(org: Organization, db: Session) -> dict[str, object]:
    settings = get_settings()
    contract = get_external_adapter_contract()
    provider_readiness = build_provider_readiness(contract=contract)
    workflows = _bot_workflow_status(org)
    gemini_status = _gemini_provider_status(org, db)
    adapter_contract_enforced = is_paper_only_adapter_contract(contract)
    payload: dict[str, object] = {
        "org_id": org.id,
        "runtime_mode": settings.runtime_mode,
        "paper_only_default": settings.runtime_mode == "paper",
        "personal_trading_mode": settings.personal_trading_mode,
        "guarded_testnet_allowed": settings.runtime_mode == "guarded_testnet",
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "real_mode_enabled_for_org": org.real_mode_enabled,
        "workflows": workflows,
        "safeguards": {
            "guarded_testnet_enabled": bool(
                settings.enable_live_trading and not settings.allow_mainnet_trading
            ),
            "production_mainnet_blocked": settings.app_env == "production"
            or not settings.allow_mainnet_trading,
            "adapter_contract_enforced": adapter_contract_enforced,
        },
        "providers": {
            GEMINI_PROVIDER: gemini_status,
        },
        "readiness": _bot_readiness_summary(
            gemini_status=gemini_status,
            provider_readiness=provider_readiness,
            workflows=workflows,
            adapter_contract_enforced=adapter_contract_enforced,
        ),
        "provider_readiness": provider_readiness,
        "external_adapter_contract": contract,
    }
    if settings.personal_trading_mode:
        policy = policy_from_settings(settings)
        payload["personal_risk_policy"] = risk_policy_payload(policy)
        payload["personal_paper_state"] = {
            "currency": "BRL",
            "default_trade_amount_brl": settings.paper_trade_amount_brl,
            "future_real_min_trade_amount_brl": settings.real_min_trade_amount_brl,
            "accelerated_research": settings.paper_accelerated_research,
            "open_positions_count": _open_paper_positions_count(org.id, db),
            "symbol_position_budget": settings.paper_max_open_per_symbol,
            "open_symbol_counts": _open_paper_symbol_counts(org.id, db),
            "slot_budgets": _paper_slot_budgets(settings),
            "open_slot_counts": _open_paper_slot_counts(org.id, db),
            "open_capital_brl": float(_open_paper_capital(org.id, db)),
            "today_new_capital_brl": float(_today_new_paper_capital(org.id, db)),
            "paper_cycle": _paper_cycle_state(org, db),
        }
    return payload


def _enforce_proposal_guardrails(status: dict[str, object]) -> None:
    if status.get("runtime_mode") == "blocked_mainnet":
        raise HTTPException(status_code=503, detail="Mainnet runtime is blocked by project policy")
    if status.get("mainnet_order_submission_enabled") is not False:
        raise HTTPException(status_code=503, detail="Mainnet order submission is blocked")

    safeguards = status.get("safeguards")
    if not isinstance(safeguards, dict) or safeguards.get("adapter_contract_enforced") is not True:
        raise HTTPException(status_code=503, detail="External adapter contract is not paper-only safe")

    provider_readiness = status.get("provider_readiness")
    if not isinstance(provider_readiness, dict) or provider_readiness.get("paper_only") is not True:
        raise HTTPException(status_code=503, detail="Provider readiness must remain paper-only")
    if provider_readiness.get("order_submission_enabled") is not False:
        raise HTTPException(status_code=503, detail="Provider readiness must not enable order submission")
    if provider_readiness.get("mainnet_order_submission_enabled") is not False:
        raise HTTPException(
            status_code=503,
            detail="Provider readiness must not enable mainnet order submission",
        )

    workflows = status.get("workflows")
    if isinstance(workflows, dict) and workflows.get("circuit_breaker_tripped") is True:
        raise HTTPException(status_code=423, detail="Bot circuit breaker is tripped")


@router.post("/proposals", status_code=201)
def create_proposal(
    payload: CopilotProposalCreate,
    api_key: VerifiedApiKey,
    db: DbSession,
) -> dict[str, object]:
    require_api_key_scope(api_key, "copilot:proposals")
    org = _org_for_api_key(api_key)
    status = _paper_bot_status(org, db)
    _enforce_proposal_guardrails(status)
    if payload.mode == "real" and not org.real_mode_enabled:
        raise HTTPException(status_code=403, detail="Real mode is disabled for this organization")

    proposal = CopilotProposal(
        org_id=org.id,
        api_key_id=api_key.id,
        token_address=payload.token_address,
        chain=payload.chain.lower(),
        symbol=payload.symbol,
        side=payload.side.value,
        amount_usd=payload.amount_usd,
        score=payload.score,
        rationale=payload.rationale,
        mode=payload.mode,
        status="pending",
    )
    db.add(proposal)
    db.flush()
    record_audit_log(
        db,
        org_id=org.id,
        actor_id=api_key.user_id,
        actor_type="api_key",
        action="copilot.proposal.created",
        target_type="copilot_proposal",
        target_id=proposal.id,
        after={
            "status": proposal.status,
            "mode": proposal.mode,
            "token_address": proposal.token_address,
            "chain": proposal.chain,
            "api_key_prefix": api_key.prefix[:12],
        },
    )
    db.commit()
    db.refresh(proposal)
    return {"proposal": _proposal_payload(proposal)}


@router.get("/bot/status")
def get_bot_status(api_key: VerifiedApiKey, db: DbSession) -> dict[str, object]:
    require_api_key_scope(api_key, "copilot:proposals")
    org = _org_for_api_key(api_key)
    return _paper_bot_status(org, db)


@router.get("/paper-cycle")
def get_paper_cycle(user: CurrentUser, db: DbSession) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    db.commit()
    return {"org_id": org.id, "paper_cycle": _paper_cycle_state(org, db)}


@router.post("/paper-cycle/reset")
def reset_paper_cycle(
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    db.commit()
    rows = (
        db.query(SimulatedTrade)
        .filter(SimulatedTrade.org_id == org.id, SimulatedTrade.status == "OPEN")
        .order_by(SimulatedTrade.created_at.asc(), SimulatedTrade.id.asc())
        .all()
    )
    closed: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    for trade in rows:
        try:
            result = close_position(trade)
            db.add(trade)
            outcome = (
                db.query(StrategyOutcome)
                .filter(
                    StrategyOutcome.org_id == org.id,
                    StrategyOutcome.paper_position_id == trade.id,
                )
                .first()
            )
            outcome_payload: dict[str, object] = {}
            if outcome is not None:
                realized = Decimal(str(result.realized_pnl_usd or 0))
                outcome.paper_pnl_brl = realized
                outcome.classification = (
                    "winner" if realized > 0 else "loser" if realized < 0 else "sideways"
                )
                metadata = outcome.metadata_json if isinstance(outcome.metadata_json, dict) else {}
                outcome.metadata_json = {
                    **metadata,
                    "closed_by": "paper_cycle_reset",
                    "exit_price": float(trade.exit_price) if trade.exit_price is not None else None,
                    "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
                    "realized_pnl_brl": float(realized),
                }
                outcome.updated_at = utcnow_naive()
                outcome_payload = {
                    "strategy_profile": outcome.profile_name,
                    "strategy_family": outcome.family,
                    "outcome_id": outcome.id,
                    "classification": outcome.classification,
                }
            closed.append(
                {
                    "trade_id": trade.id,
                    "token_address": trade.token_address,
                    "entry_price": float(trade.entry_price),
                    "exit_price": float(trade.exit_price) if trade.exit_price is not None else None,
                    "realized_pnl_brl": result.realized_pnl_usd,
                    **outcome_payload,
                }
            )
        except Exception as exc:
            failed.append(
                {
                    "trade_id": trade.id,
                    "token_address": trade.token_address,
                    "error": _scanner_error_summary(exc),
                }
            )
    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="paper_cycle.reset",
        target_type="organization",
        target_id=org.id,
        request=request,
        after={
            "closed": len(closed),
            "failed": len(failed),
            "paper_cycle": _paper_cycle_state(org, db),
        },
    )
    db.commit()
    return {
        "org_id": org.id,
        "closed": closed,
        "failed": failed,
        "paper_cycle": _paper_cycle_state(org, db),
    }


@router.get("/proposals")
def list_proposals(user: CurrentUser, db: DbSession) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    db.commit()
    rows = (
        db.query(CopilotProposal)
        .filter(CopilotProposal.org_id == org.id)
        .order_by(CopilotProposal.created_at.desc(), CopilotProposal.id.desc())
        .limit(100)
        .all()
    )
    return {"org_id": org.id, "proposals": [_proposal_payload(row) for row in rows]}


@router.get("/approval-queue")
def list_approval_queue(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(100, ge=1, le=200),
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    db.commit()
    rows = (
        db.query(CopilotProposal)
        .filter(
            CopilotProposal.org_id == org.id,
            CopilotProposal.status == "pending",
            CopilotProposal.mode == "paper",
        )
        .order_by(CopilotProposal.created_at.desc(), CopilotProposal.id.desc())
        .limit(limit)
        .all()
    )
    approvals = [_proposal_payload(row) for row in rows]
    return {
        "org_id": org.id,
        "pending_count": len(approvals),
        "contract": _approval_queue_contract(),
        "approvals": approvals,
    }


@router.get("/scanner-runs")
def list_scanner_runs(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(100, ge=1, le=200),
    include_outcomes: bool = Query(False),
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    db.commit()
    rows = (
        db.query(CopilotScannerRun)
        .filter(CopilotScannerRun.org_id == org.id)
        .order_by(CopilotScannerRun.created_at.desc(), CopilotScannerRun.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "org_id": org.id,
        "scanner_runs": [
            _scanner_run_payload(
                row,
                paper_outcome=_scanner_run_paper_outcome(row, db) if include_outcomes else None,
            )
            for row in rows
        ],
    }


@router.get("/strategy-outcomes")
def list_strategy_outcomes(
    user: CurrentUser,
    db: DbSession,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    db.commit()
    since = utcnow_naive() - timedelta(days=days)
    rows = (
        db.query(StrategyOutcome)
        .filter(StrategyOutcome.org_id == org.id, StrategyOutcome.created_at >= since)
        .order_by(StrategyOutcome.created_at.desc(), StrategyOutcome.id.desc())
        .limit(limit)
        .all()
    )
    aggregates = _aggregate_strategy_outcomes(rows, db, org.id)
    return {
        "org_id": org.id,
        "days": days,
        "outcomes": [_strategy_outcome_payload(row) for row in rows],
        **aggregates,
    }


@router.get("/scanner-runs/latest-paper-evidence")
def get_latest_paper_scanner_run_evidence(
    user: CurrentUser,
    db: DbSession,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    db.commit()
    scanner_run = (
        db.query(CopilotScannerRun)
        .filter(
            CopilotScannerRun.org_id == org.id,
            CopilotScannerRun.execution_mode == "paper_auto",
            CopilotScannerRun.status.in_(("completed", "failed")),
        )
        .order_by(CopilotScannerRun.created_at.desc(), CopilotScannerRun.id.desc())
        .first()
    )
    return {
        "org_id": org.id,
        "evidence": _paper_scanner_run_evidence_payload(scanner_run) if scanner_run else None,
    }


@router.get("/scanner-runs/{scanner_run_id}")
def get_scanner_run(
    scanner_run_id: int,
    user: CurrentUser,
    db: DbSession,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    db.commit()
    scanner_run = (
        db.query(CopilotScannerRun)
        .filter(CopilotScannerRun.id == scanner_run_id, CopilotScannerRun.org_id == org.id)
        .first()
    )
    if scanner_run is None:
        raise HTTPException(status_code=404, detail="Scanner run not found")
    proposals = (
        db.query(CopilotProposal)
        .filter(CopilotProposal.org_id == org.id, CopilotProposal.scanner_run_id == scanner_run.id)
        .order_by(CopilotProposal.created_at.desc(), CopilotProposal.id.desc())
        .all()
    )
    return {
        "org_id": org.id,
        "scanner_run": _scanner_run_payload(scanner_run),
        "proposals": [_proposal_payload(row) for row in proposals],
    }


@router.post("/scan", status_code=201)
def run_scanner(
    payload: CopilotScanRequest,
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    api_key = _select_bot_api_key(org.id, db)
    status = _paper_bot_status(org, db)
    _enforce_proposal_guardrails(status)
    if payload.execution_mode == "real_approval" and not org.real_mode_enabled:
        raise HTTPException(
            status_code=403,
            detail=(
                "Real Mode 2 approval is disabled for this organization. "
                "Mode 1 paper_auto and Mode 2 paper copilot remain available."
            ),
        )
    settings = get_settings()
    if (
        payload.execution_mode == "real_approval"
        and settings.personal_trading_mode
        and payload.amount_usd < Decimal(str(settings.real_min_trade_amount_brl))
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Real approval amount is below the configured minimum that accounts "
                f"for fees and slippage: R$ {settings.real_min_trade_amount_brl:.2f}"
            ),
        )

    scanner_run = CopilotScannerRun(
        org_id=org.id,
        api_key_id=api_key.id,
        actor_user_id=user.id,
        query=payload.query,
        execution_mode=payload.execution_mode,
        status="running",
        thresholds_json=_scanner_thresholds(payload),
        candidate_count=0,
        provider_readiness_summary=_safe_provider_readiness_summary(status),
        created_at=utcnow_naive(),
    )
    db.add(scanner_run)
    db.flush()
    scanner_run_id = scanner_run.id
    db.commit()
    db.refresh(scanner_run)
    created: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    candidate_count = 0
    paper_executed_count = 0
    try:
        candidates = _scan_market_candidates(payload)
        candidate_count = len(candidates)
        scanner_run.candidate_count = candidate_count
        for candidate in candidates:
            address = str(candidate["token_address"])
            chain = str(candidate["chain"])
            audit = get_token_security_audit(address, chain)
            score = _score_candidate(candidate, audit)
            paper_roundtrip_cost = _paper_roundtrip_cost_viability(
                candidate=candidate,
                audit=audit,
                amount_brl=payload.amount_usd,
            )
            paper_slot_allocation = _paper_slot_allocation_viability(
                org_id=org.id,
                db=db,
                strategy_family=payload.strategy_family,
                settings=settings,
            )
            paper_symbol_allocation = _paper_symbol_allocation_viability(
                org_id=org.id,
                db=db,
                candidate=candidate,
                settings=settings,
            )
            shadow_real_execution = _shadow_real_execution_route(payload.strategy_family)
            candidate["_audit"] = {
                "risk_score": audit.get("risk_score"),
                "is_honeypot": audit.get("is_honeypot"),
                "buy_tax": audit.get("buy_tax"),
                "sell_tax": audit.get("sell_tax"),
                "providers": audit.get("providers", []),
            }
            candidate["_paper_roundtrip_cost"] = paper_roundtrip_cost
            candidate["_paper_slot_allocation"] = paper_slot_allocation
            candidate["_paper_symbol_allocation"] = paper_symbol_allocation
            candidate["_shadow_real_execution"] = shadow_real_execution
            discovery = TokenDiscoveryHistory(
                org_id=org.id,
                token_address=address,
                chain=chain,
                symbol=str(candidate["symbol"]) if candidate.get("symbol") else None,
                score=score,
                recommendation="skip"
                if audit.get("is_honeypot")
                else ("real_approval" if payload.execution_mode == "real_approval" else payload.execution_mode),
                rationale=_rationale(candidate, audit, score),
                metadata_json={
                    "source": "copilot.scan",
                    "volume_24h": candidate.get("volume_24h"),
                    "momentum_h1": candidate.get("momentum_h1"),
                    "liquidity_usd": candidate.get("liquidity_usd"),
                    "market_cap_usd": candidate.get("market_cap_usd"),
                    "candidate_source": candidate.get("source"),
                    "execution_mode": payload.execution_mode,
                    "wallet_address": payload.wallet_address,
                    "wallet_chain_id": payload.wallet_chain_id,
                    "security_providers": audit.get("providers", []),
                    "audit": candidate["_audit"],
                    "paper_roundtrip_cost": paper_roundtrip_cost,
                    "paper_slot_allocation": paper_slot_allocation,
                    "paper_symbol_allocation": paper_symbol_allocation,
                    "shadow_real_execution": shadow_real_execution,
                },
            )
            db.add(discovery)
            if audit.get("is_honeypot"):
                skipped.append({"token_address": address, "reason": "honeypot_detected", "score": score})
                db.flush()
                _record_strategy_outcome(
                    db,
                    scanner_run=scanner_run,
                    discovery=discovery,
                    candidate=candidate,
                    decision="rejected_audit",
                    paper_pnl_brl=Decimal("0"),
                    classification="rug",
                    block_reason="honeypot_detected",
                    block_stage="pre_trade_audit",
                )
                record_audit_log(
                    db,
                    org_id=org.id,
                    actor_id=user.id,
                    action="paper_execution.decision_recorded",
                    target_type="token_discovery_history",
                    target_id=discovery.id,
                    request=request,
                    after=_paper_execution_decision_payload(
                        scanner_run=scanner_run,
                        proposal=None,
                        discovery=discovery,
                        execution_mode=payload.execution_mode,
                        decision="blocked",
                        reason="honeypot_detected",
                    ),
                )
                continue

            settings = get_settings()
            if payload.execution_mode == "paper_auto" and settings.personal_trading_mode:
                if paper_symbol_allocation.get("allowed") is not True:
                    reason = str(
                        paper_symbol_allocation.get("reason") or "paper_symbol_allocation_rejected"
                    )
                    skipped.append(
                        {
                            "token_address": address,
                            "reason": reason,
                            "score": score,
                            "paper_symbol_allocation": paper_symbol_allocation,
                        }
                    )
                    db.flush()
                    _record_strategy_outcome(
                        db,
                        scanner_run=scanner_run,
                        discovery=discovery,
                        candidate=candidate,
                        decision="rejected_risk",
                        classification="symbol_full",
                        block_reason=reason,
                        block_stage="paper_symbol_allocation",
                    )
                    record_audit_log(
                        db,
                        org_id=org.id,
                        actor_id=user.id,
                        action="paper_execution.decision_recorded",
                        target_type="token_discovery_history",
                        target_id=discovery.id,
                        request=request,
                        after={
                            **_paper_execution_decision_payload(
                                scanner_run=scanner_run,
                                proposal=None,
                                discovery=discovery,
                                execution_mode=payload.execution_mode,
                                decision="blocked",
                                reason=reason,
                            ),
                            "paper_symbol_allocation": paper_symbol_allocation,
                        },
                    )
                    continue
                if paper_slot_allocation.get("allowed") is not True:
                    reason = str(
                        paper_slot_allocation.get("reason") or "paper_slot_allocation_rejected"
                    )
                    skipped.append(
                        {
                            "token_address": address,
                            "reason": reason,
                            "score": score,
                            "paper_slot_allocation": paper_slot_allocation,
                        }
                    )
                    db.flush()
                    _record_strategy_outcome(
                        db,
                        scanner_run=scanner_run,
                        discovery=discovery,
                        candidate=candidate,
                        decision="rejected_risk",
                        classification="slot_full",
                        block_reason=reason,
                        block_stage="paper_slot_allocation",
                    )
                    record_audit_log(
                        db,
                        org_id=org.id,
                        actor_id=user.id,
                        action="paper_execution.decision_recorded",
                        target_type="token_discovery_history",
                        target_id=discovery.id,
                        request=request,
                        after={
                            **_paper_execution_decision_payload(
                                scanner_run=scanner_run,
                                proposal=None,
                                discovery=discovery,
                                execution_mode=payload.execution_mode,
                                decision="blocked",
                                reason=reason,
                            ),
                            "paper_slot_allocation": paper_slot_allocation,
                        },
                    )
                    continue
                if paper_roundtrip_cost.get("allowed") is not True:
                    reason = str(paper_roundtrip_cost.get("reason") or "roundtrip_cost_rejected")
                    skipped.append(
                        {
                            "token_address": address,
                            "reason": reason,
                            "score": score,
                            "paper_roundtrip_cost": paper_roundtrip_cost,
                        }
                    )
                    db.flush()
                    _record_strategy_outcome(
                        db,
                        scanner_run=scanner_run,
                        discovery=discovery,
                        candidate=candidate,
                        decision="rejected_audit",
                        paper_pnl_brl=Decimal("0"),
                        classification="cost_unviable",
                        block_reason=reason,
                        block_stage="roundtrip_cost_filter",
                    )
                    record_audit_log(
                        db,
                        org_id=org.id,
                        actor_id=user.id,
                        action="paper_execution.decision_recorded",
                        target_type="token_discovery_history",
                        target_id=discovery.id,
                        request=request,
                        after={
                            **_paper_execution_decision_payload(
                                scanner_run=scanner_run,
                                proposal=None,
                                discovery=discovery,
                                execution_mode=payload.execution_mode,
                                decision="blocked",
                                reason=reason,
                            ),
                            "paper_roundtrip_cost": paper_roundtrip_cost,
                        },
                    )
                    continue
                policy = policy_from_settings(settings)
                cycle_capital_brl = (
                    _open_paper_capital(org.id, db)
                    if settings.paper_accelerated_research
                    else _today_new_paper_capital(org.id, db)
                )
                risk_decision = decide_new_paper_position(
                    policy=policy,
                    requested_brl=payload.amount_usd,
                    open_positions_count=_open_paper_positions_count(org.id, db),
                    today_new_capital_brl=cycle_capital_brl,
                )
                if not risk_decision.allowed:
                    skipped.append(
                        {
                            "token_address": address,
                            "reason": risk_decision.reason,
                            "score": score,
                        }
                    )
                    db.flush()
                    _record_strategy_outcome(
                        db,
                        scanner_run=scanner_run,
                        discovery=discovery,
                        candidate=candidate,
                        decision="rejected_risk",
                        classification="rejected_risk",
                        block_reason=risk_decision.reason,
                        block_stage="risk_book",
                    )
                    record_audit_log(
                        db,
                        org_id=org.id,
                        actor_id=user.id,
                        action="paper_execution.decision_recorded",
                        target_type="token_discovery_history",
                        target_id=discovery.id,
                        request=request,
                        after={
                            **_paper_execution_decision_payload(
                                scanner_run=scanner_run,
                                proposal=None,
                                discovery=discovery,
                                execution_mode=payload.execution_mode,
                                decision="blocked",
                                reason=risk_decision.reason,
                            ),
                            "risk_policy": risk_policy_payload(policy),
                            "requested_brl": str(risk_decision.requested_brl),
                            "open_positions_count": risk_decision.open_positions_count,
                            "today_new_capital_brl": str(
                                risk_decision.today_new_capital_brl
                            ),
                            "accelerated_research_uses_open_capital": (
                                settings.paper_accelerated_research
                            ),
                            "paper_slot_allocation": paper_slot_allocation,
                        },
                    )
                    continue

            proposal = CopilotProposal(
                org_id=org.id,
                api_key_id=api_key.id,
                scanner_run=scanner_run,
                token_address=address,
                chain=chain,
                symbol=str(candidate["symbol"]) if candidate.get("symbol") else None,
                side=TradeSide.BUY.value,
                amount_usd=payload.amount_usd,
                score=score,
                rationale=discovery.rationale or "Autonomous paper scanner proposal.",
                mode="real" if payload.execution_mode == "real_approval" else "paper",
                status="pending",
                execution_payload=_manual_real_gate_payload(payload)
                if payload.execution_mode == "real_approval"
                else None,
            )
            db.add(proposal)
            db.flush()

            if payload.execution_mode == "paper_auto":
                enforce_quota(api_key, "simulate_order", db, simulated_volume_usd=payload.amount_usd)
                try:
                    result = simulate_trade(
                        OrderRequest(
                            token_address=address,
                            amount=float(payload.amount_usd),
                            side=TradeSide.BUY,
                        ),
                        api_key=api_key,
                        db=db,
                    )
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                    reason = str(exc.detail or "paper_simulation_rejected")
                    proposal.status = "rejected"
                    proposal.updated_at = utcnow_naive()
                    proposal.execution_payload = {
                        "paper_only": True,
                        "stage": "simulate_trade",
                        "reason": reason,
                        "order_submission_enabled": False,
                    }
                    skipped.append({"token_address": address, "reason": reason, "score": score})
                    db.flush()
                    _record_strategy_outcome(
                        db,
                        scanner_run=scanner_run,
                        discovery=discovery,
                        candidate=candidate,
                        decision="rejected_audit",
                        paper_pnl_brl=Decimal("0"),
                        classification="rug" if "honeypot" in reason.lower() else "rejected_audit",
                        block_reason=reason,
                        block_stage="simulate_trade",
                    )
                    record_audit_log(
                        db,
                        org_id=org.id,
                        actor_id=user.id,
                        action="paper_execution.decision_recorded",
                        target_type="copilot_proposal",
                        target_id=proposal.id,
                        request=request,
                        after=_paper_execution_decision_payload(
                            scanner_run=scanner_run,
                            proposal=proposal,
                            discovery=discovery,
                            execution_mode=payload.execution_mode,
                            decision="blocked",
                            reason=reason,
                        ),
                    )
                    continue
                proposal.status = "executed"
                proposal.approved_at = utcnow_naive()
                proposal.updated_at = utcnow_naive()
                proposal.execution_payload = result.model_dump(mode="json")
                org.bot_consecutive_failures = 0
                paper_executed_count += 1
                db.flush()

                trade_id = None
                if isinstance(proposal.execution_payload, dict):
                    trade_id = proposal.execution_payload.get("trade_id")
                try:
                    paper_position_id = int(trade_id) if trade_id is not None else None
                except (TypeError, ValueError):
                    paper_position_id = None
                _record_strategy_outcome(
                    db,
                    scanner_run=scanner_run,
                    discovery=discovery,
                    candidate=candidate,
                    decision="approved",
                    paper_position_id=paper_position_id,
                    entry_price=Decimal(str(result.executed_price)),
                )
                record_audit_log(
                    db,
                    org_id=org.id,
                    actor_id=user.id,
                    action="paper_execution.decision_recorded",
                    target_type="copilot_proposal",
                    target_id=proposal.id,
                    request=request,
                    after=_paper_execution_decision_payload(
                        scanner_run=scanner_run,
                        proposal=proposal,
                        discovery=discovery,
                        execution_mode=payload.execution_mode,
                        decision="paper_executed",
                        reason="mode_1_paper_auto",
                        trade_id=trade_id,
                    ),
                )
            else:
                _record_strategy_outcome(
                    db,
                    scanner_run=scanner_run,
                    discovery=discovery,
                    candidate=candidate,
                    decision="queued",
                )
                record_audit_log(
                    db,
                    org_id=org.id,
                    actor_id=user.id,
                    action="paper_execution.decision_recorded",
                    target_type="copilot_proposal",
                    target_id=proposal.id,
                    request=request,
                    after=_paper_execution_decision_payload(
                        scanner_run=scanner_run,
                        proposal=proposal,
                        discovery=discovery,
                        execution_mode=payload.execution_mode,
                        decision="queued",
                        reason="manual_approval_required"
                        if payload.execution_mode == "real_approval"
                        else "copilot_review_required",
                    ),
                )

            created.append(_proposal_payload(proposal))
    except Exception as exc:
        db.rollback()
        scanner_run = (
            db.query(CopilotScannerRun)
            .filter(CopilotScannerRun.id == scanner_run_id, CopilotScannerRun.org_id == org.id)
            .one()
        )
        scanner_run.status = "failed"
        scanner_run.error_summary = _scanner_error_summary(exc)
        scanner_run.candidate_count = candidate_count
        scanner_run.blocked_count = 0
        scanner_run.queued_count = 0
        scanner_run.paper_executed_count = 0
        scanner_run.completed_at = utcnow_naive()
        failure_org = db.query(Organization).filter(Organization.id == org.id).one()
        failure_org.bot_consecutive_failures += 1
        if failure_org.bot_consecutive_failures >= 3:
            failure_org.real_mode_enabled = False
        record_audit_log(
            db,
            org_id=org.id,
            actor_id=user.id,
            action="copilot.scan.failed",
            target_type="copilot_scan",
            target_id=scanner_run.id,
            request=request,
            after={
                "scanner_run_id": scanner_run.id,
                "query": payload.query,
                "execution_mode": payload.execution_mode,
                "candidate_count": candidate_count,
                "error_summary": scanner_run.error_summary,
                "bot_consecutive_failures": failure_org.bot_consecutive_failures,
                "real_mode_enabled": failure_org.real_mode_enabled,
            },
        )
        db.commit()
        raise

    scanner_run.status = "completed"
    scanner_run.blocked_count = len(skipped)
    scanner_run.queued_count = sum(1 for item in created if item.get("status") == "pending")
    scanner_run.paper_executed_count = paper_executed_count
    scanner_run.status = "completed"
    scanner_run.completed_at = utcnow_naive()
    org.bot_consecutive_failures = 0
    db.flush()

    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="copilot.scan.executed",
        target_type="copilot_scan",
        request=request,
        after={
            "query": payload.query,
            "execution_mode": payload.execution_mode,
            "created": len(created),
            "skipped": len(skipped),
            "scanner_run_id": scanner_run.id,
        },
    )
    db.commit()
    db.refresh(scanner_run)
    return {
        "org_id": org.id,
        "execution_mode": payload.execution_mode,
        "scanner_run": _scanner_run_payload(scanner_run),
        "created": created,
        "skipped": skipped,
    }


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(
    proposal_id: int,
    user: CurrentUser,
    db: DbSession,
    request: Request,
    payload: Annotated[ProposalApprovalRequest | None, Body()] = None,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    proposal = (
        db.query(CopilotProposal)
        .filter(CopilotProposal.id == proposal_id, CopilotProposal.org_id == org.id)
        .first()
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(status_code=409, detail="Proposal is not pending")
    before = _proposal_payload(proposal)
    if proposal.mode == "real":
        if not org.real_mode_enabled:
            raise HTTPException(
                status_code=403, detail="Real mode is disabled for this organization"
            )
        manual_approval = _manual_approval_payload(payload)
        if manual_approval:
            proposal.execution_payload = {
                **(proposal.execution_payload or {}),
                "manual_approval": manual_approval,
            }
        proposal.status = "approved_manual_required"
        proposal.approved_at = utcnow_naive()
        proposal.updated_at = utcnow_naive()
        db.flush()
        if proposal.scanner_run_id is not None:
            scanner_run = (
                db.query(CopilotScannerRun)
                .filter(
                    CopilotScannerRun.id == proposal.scanner_run_id,
                    CopilotScannerRun.org_id == org.id,
                )
                .one_or_none()
            )
            if scanner_run is not None:
                _sync_scanner_run_counts(scanner_run, db)
        record_audit_log(
            db,
            org_id=org.id,
            actor_id=user.id,
            action="copilot.proposal.approved",
            target_type="copilot_proposal",
            target_id=proposal.id,
            request=request,
            before={"status": before["status"], "scanner_run_id": before["scanner_run_id"]},
            after={
                "status": proposal.status,
                "scanner_run_id": proposal.scanner_run_id,
                "execution": "manual_required",
            },
        )
        db.commit()
        db.refresh(proposal)
        return {"proposal": _proposal_payload(proposal)}

    api_key = (
        db.query(ApiKey)
        .filter(
            ApiKey.id == proposal.api_key_id, ApiKey.org_id == org.id, ApiKey.revoked_at.is_(None)
        )
        .first()
    )
    if api_key is None:
        raise HTTPException(status_code=409, detail="Proposal API key is unavailable")
    require_api_key_scope(api_key, "simulate:order")
    order = OrderRequest(
        token_address=proposal.token_address,
        amount=float(proposal.amount_usd),
        side=TradeSide(proposal.side),
    )
    enforce_quota(api_key, "simulate_order", db, simulated_volume_usd=proposal.amount_usd)
    result = simulate_trade(order, api_key=api_key, db=db)
    org.bot_consecutive_failures = 0
    proposal.status = "executed"
    proposal.approved_at = utcnow_naive()
    proposal.updated_at = utcnow_naive()
    proposal.execution_payload = result.model_dump(mode="json")
    db.flush()
    if proposal.scanner_run_id is not None:
        scanner_run = (
            db.query(CopilotScannerRun)
            .filter(
                CopilotScannerRun.id == proposal.scanner_run_id,
                CopilotScannerRun.org_id == org.id,
            )
            .one_or_none()
        )
        if scanner_run is not None:
            _sync_scanner_run_counts(scanner_run, db)
    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="copilot.proposal.approved",
        target_type="copilot_proposal",
        target_id=proposal.id,
        request=request,
        before={"status": before["status"], "scanner_run_id": before["scanner_run_id"]},
        after={
            "status": proposal.status,
            "scanner_run_id": proposal.scanner_run_id,
            "execution": "paper_simulated",
            "trade_id": proposal.execution_payload.get("trade_id")
            if isinstance(proposal.execution_payload, dict)
            else None,
        },
    )
    db.commit()
    db.refresh(proposal)
    return {"proposal": _proposal_payload(proposal)}


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(
    proposal_id: int,
    user: CurrentUser,
    db: DbSession,
    request: Request,
    payload: Annotated[ProposalRejectionRequest | None, Body()] = None,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    proposal = (
        db.query(CopilotProposal)
        .filter(CopilotProposal.id == proposal_id, CopilotProposal.org_id == org.id)
        .first()
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(status_code=409, detail="Proposal is not pending")

    before = _proposal_payload(proposal)
    rejection_reason = payload.reason if payload is not None else None
    if rejection_reason is not None:
        proposal.execution_payload = {
            **(proposal.execution_payload or {}),
            "rejection": {
                "reason": rejection_reason,
                "order_submission_enabled": False,
                "mainnet_order_submission_enabled": False,
            },
        }
    proposal.status = "rejected"
    proposal.updated_at = utcnow_naive()
    db.flush()
    if proposal.scanner_run_id is not None:
        scanner_run = (
            db.query(CopilotScannerRun)
            .filter(
                CopilotScannerRun.id == proposal.scanner_run_id,
                CopilotScannerRun.org_id == org.id,
            )
            .one_or_none()
        )
        if scanner_run is not None:
            _sync_scanner_run_counts(scanner_run, db)

    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="copilot.proposal.rejected",
        target_type="copilot_proposal",
        target_id=proposal.id,
        request=request,
        before={"status": before["status"], "scanner_run_id": before["scanner_run_id"]},
        after={
            "status": proposal.status,
            "scanner_run_id": proposal.scanner_run_id,
            "rejection_reason": rejection_reason,
            "execution": "none",
        },
    )
    db.commit()
    db.refresh(proposal)
    return {"proposal": _proposal_payload(proposal)}


@router.post("/circuit-breaker/failures")
def record_failure(api_key: VerifiedApiKey, db: DbSession) -> dict[str, object]:
    require_api_key_scope(api_key, "copilot:proposals")
    org = _org_for_api_key(api_key)
    org.bot_consecutive_failures += 1
    tripped = org.bot_consecutive_failures >= 3
    if tripped:
        org.real_mode_enabled = False
    db.commit()
    db.refresh(org)
    return {
        "org_id": org.id,
        "bot_consecutive_failures": org.bot_consecutive_failures,
        "real_mode_enabled": org.real_mode_enabled,
        "circuit_breaker_tripped": tripped,
    }


@router.get("/stream")
async def stream_proposals(
    user: CurrentUser,
    db: DbSession,
    once: bool = Query(False),
) -> StreamingResponse:
    org = primary_org_for_user(user, db)
    db.commit()

    async def events():
        last_seen = 0
        while True:
            rows = (
                db.query(CopilotProposal)
                .filter(CopilotProposal.org_id == org.id, CopilotProposal.id > last_seen)
                .order_by(CopilotProposal.id.asc())
                .limit(25)
                .all()
            )
            for row in rows:
                last_seen = max(last_seen, int(row.id))
                yield f"event: proposal\ndata: {json.dumps(_proposal_payload(row), default=str)}\n\n"
            if once:
                break
            await asyncio.sleep(2)

    return StreamingResponse(events(), media_type="text/event-stream")
