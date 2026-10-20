import logging
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from tothemoon_api.auth import require_api_key_scope, verify_api_key
from tothemoon_api.database import get_db
from tothemoon_api.db_models import ApiKey, SimulatedTrade
from tothemoon_api.quota import enforce_quota
from tothemoon_api.simulation import (
    OrderRequest,
    OrderResponse,
    PositionResponse,
    close_position,
    position_response,
    simulate_trade,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/simulate", tags=["Simulation"])
VerifiedApiKey = Annotated[ApiKey, Depends(verify_api_key)]
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/order", response_model=OrderResponse)
def execute_simulation(
    order: OrderRequest,
    api_key: VerifiedApiKey,
    db: DbSession,
) -> OrderResponse:
    logger.info(f"Executing simulation for order: {order}")
    try:
        require_api_key_scope(api_key, "simulate:order")
        enforce_quota(
            api_key,
            "simulate_order",
            db,
            simulated_volume_usd=Decimal(str(order.amount)),
        )
        result: OrderResponse = simulate_trade(order, api_key=api_key, db=db)
        logger.info("Simulation executed successfully")
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Error executing simulation: {exc}")
        raise HTTPException(status_code=500, detail="Simulation execution failed") from exc


@router.get("/positions", response_model=list[PositionResponse])
def list_positions(
    api_key: VerifiedApiKey,
    db: DbSession,
) -> list[PositionResponse]:
    require_api_key_scope(api_key, "simulate:order")
    if api_key.org_id is None:
        raise HTTPException(status_code=403, detail="API key is not scoped to an organization")
    rows = (
        db.query(SimulatedTrade)
        .filter(SimulatedTrade.org_id == api_key.org_id, SimulatedTrade.status == "OPEN")
        .order_by(SimulatedTrade.created_at.desc())
        .all()
    )
    return [position_response(row) for row in rows]


@router.post("/positions/{trade_id}/close", response_model=OrderResponse)
def close_simulated_position(
    trade_id: int,
    api_key: VerifiedApiKey,
    db: DbSession,
) -> OrderResponse:
    require_api_key_scope(api_key, "simulate:order")
    if api_key.org_id is None:
        raise HTTPException(status_code=403, detail="API key is not scoped to an organization")
    trade = (
        db.query(SimulatedTrade)
        .filter(
            SimulatedTrade.id == trade_id,
            SimulatedTrade.org_id == api_key.org_id,
            SimulatedTrade.status == "OPEN",
        )
        .first()
    )
    if trade is None:
        raise HTTPException(status_code=404, detail="Open position not found")
    enforce_quota(
        api_key,
        "simulate_order",
        db,
        simulated_volume_usd=Decimal(str(trade.amount)),
    )
    try:
        result = close_position(trade)
        db.add(trade)
        db.commit()
        logger.info("Position closed successfully")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
