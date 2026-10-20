from __future__ import annotations

import re
from datetime import UTC, datetime
from math import tanh
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from .journal import load_trades
from .models import PaperTradeRecord

router = APIRouter(prefix="/api/agents", tags=["agents"])

_AGENT_PATTERNS = (
    re.compile(r"\[agent:(?P<agent>[a-zA-Z0-9_\-]+)\]"),
    re.compile(r"\bagent[:=](?P<agent>[a-zA-Z0-9_\-]+)\b"),
    re.compile(r"\bpublisher[:=](?P<agent>[a-zA-Z0-9_\-]+)\b"),
)


class AgentOperationalReputation(BaseModel):
    agent_id: str
    successful_jobs: int = 0
    failed_jobs: int = 0
    is_verified: bool = True
    reputation_score: float = 0.0


class AgentReputationSnapshot(BaseModel):
    agent_id: str
    regime: str | None = None
    timeframe: str | None = None
    total_trades: int = 0
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    win_rate_pct: float = 0.0
    freshness_score: float = 0.0
    activity_score: float = 0.0
    pnl_score: float = 0.0
    reputation_score: float = 0.0
    tier: Literal["unproven", "standard", "trusted", "elite"] = "unproven"
    last_trade_at: datetime | None = None


def infer_agent_id(record: PaperTradeRecord) -> str:
    for pattern in _AGENT_PATTERNS:
        match = pattern.search(record.setup_reason)
        if match:
            return match.group("agent")
    return record.strategy_id


def _tier_for_score(score: float) -> Literal["unproven", "standard", "trusted", "elite"]:
    if score >= 0.85:
        return "elite"
    if score >= 0.7:
        return "trusted"
    if score >= 0.5:
        return "standard"
    return "unproven"


def _freshness_score(last_trade_at: datetime | None, now: datetime) -> float:
    if last_trade_at is None:
        return 0.0
    age_days = max((now - last_trade_at).total_seconds() / 86_400, 0.0)
    if age_days <= 1:
        return 1.0
    if age_days <= 3:
        return 0.8
    if age_days <= 7:
        return 0.6
    if age_days <= 14:
        return 0.4
    return 0.2


def calculate_reputation(
    agent_id: str,
    trades: list[PaperTradeRecord] | None = None,
    regime: str | None = None,
    timeframe: str | None = None,
) -> AgentReputationSnapshot:
    records = trades if trades is not None else load_trades()
    filtered = [record for record in records if infer_agent_id(record) == agent_id]
    if regime is not None:
        filtered = [record for record in filtered if record.market_regime == regime]
    if timeframe is not None:
        filtered = [record for record in filtered if record.timeframe == timeframe]

    if not filtered:
        return AgentReputationSnapshot(agent_id=agent_id, regime=regime, timeframe=timeframe)

    closed = [record for record in filtered if record.status == "closed" or record.pnl is not None]
    wins = sum(1 for record in closed if record.outcome == "win" or (record.pnl or 0.0) > 0)
    losses = sum(1 for record in closed if record.outcome == "loss" or (record.pnl or 0.0) < 0)
    total_pnl = round(sum(record.pnl or 0.0 for record in filtered), 4)
    last_trade_at = max(record.entry_time for record in filtered)
    now = datetime.now(UTC)

    win_rate = wins / len(closed) if closed else 0.0
    pnl_score = (tanh(total_pnl / 250.0) + 1.0) / 2.0
    freshness_score = _freshness_score(last_trade_at, now)
    activity_score = min(len(closed) / 10.0, 1.0)
    reputation_score = round(
        (0.4 * win_rate) + (0.35 * pnl_score) + (0.15 * freshness_score) + (0.1 * activity_score),
        4,
    )

    return AgentReputationSnapshot(
        agent_id=agent_id,
        regime=regime,
        timeframe=timeframe,
        total_trades=len(filtered),
        closed_trades=len(closed),
        wins=wins,
        losses=losses,
        total_pnl=total_pnl,
        win_rate_pct=round(win_rate * 100.0, 4),
        freshness_score=round(freshness_score, 4),
        activity_score=round(activity_score, 4),
        pnl_score=round(pnl_score, 4),
        reputation_score=reputation_score,
        tier=_tier_for_score(reputation_score),
        last_trade_at=last_trade_at,
    )


def get_operational_reputation(agent_id: str) -> AgentOperationalReputation:
    snapshot = calculate_reputation(agent_id)
    return AgentOperationalReputation(
        agent_id=agent_id,
        successful_jobs=snapshot.wins,
        failed_jobs=snapshot.losses,
        is_verified=snapshot.total_trades > 0,
        reputation_score=snapshot.reputation_score,
    )


@router.get("/{agent_id}/reputation", response_model=AgentReputationSnapshot)
def get_agent_reputation(agent_id: str, regime: str | None = None, timeframe: str | None = None):
    return calculate_reputation(agent_id=agent_id, regime=regime, timeframe=timeframe)
