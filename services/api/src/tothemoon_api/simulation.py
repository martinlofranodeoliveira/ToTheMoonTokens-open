from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from .db_models import ApiKey, SimulatedTrade, utcnow_naive
from .external.market import get_token_market_data
from .external.security import get_token_security_audit
from .simulation_costs import chain_gas_usd_estimate, chain_slippage_bps


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderRequest(BaseModel):
    token_address: str = Field(min_length=3, max_length=128)
    amount: float = Field(gt=0, le=1_000_000)
    side: TradeSide
    reference_price: float | None = Field(default=None, gt=0)
    fee_bps: float = Field(default=0.0, ge=0, le=1_000)
    slippage_bps: float | None = Field(default=None, ge=0, le=5_000)
    gas_fee: float | None = Field(default=None, ge=0, le=10_000)
    token_tax_bps: float | None = Field(default=None, ge=0, le=5_000)

    @field_validator("token_address")
    @classmethod
    def validate_token_address(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("token_address is required")
        if any(ch.isspace() for ch in cleaned):
            raise ValueError("token_address cannot contain whitespace")
        return cleaned


class OrderResponse(BaseModel):
    status: str
    trade_id: int | None = None
    token_address: str
    side: TradeSide
    input_amount: float
    executed_price: float
    fees_paid: float
    slippage_paid: float
    token_tax_paid: float
    net_amount: float
    estimated_quantity: float
    realized_pnl_usd: float | None = None
    source: str
    warning: str


class PositionResponse(BaseModel):
    id: int
    token_address: str
    side: str
    amount: float
    entry_price: float
    current_price: float
    fees_total: float
    slippage_bps: float
    status: str
    created_at: datetime
    unrealized_pnl_usd: float


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _market_price(order: OrderRequest, market: dict[str, object]) -> Decimal:
    if order.reference_price is not None:
        return _decimal(order.reference_price)
    return _decimal(market.get("price") or market.get("price_usd") or 1)


def _tax_rate(order: OrderRequest, audit: dict[str, object]) -> Decimal:
    if order.token_tax_bps is not None:
        return _decimal(order.token_tax_bps) / Decimal("10000")
    field = "buy_tax" if order.side == TradeSide.BUY else "sell_tax"
    return _decimal(audit.get(field, 0)) / Decimal("100")


def _executed_price(spot: Decimal, side: TradeSide, slippage_bps: float) -> Decimal:
    slippage = _decimal(slippage_bps) / Decimal("10000")
    if side == TradeSide.BUY:
        return spot * (Decimal("1") + slippage)
    executed = spot * (Decimal("1") - slippage)
    if executed <= 0:
        raise ValueError("slippage is too high for the reference price")
    return executed


def _build_response(
    order: OrderRequest,
    *,
    trade_id: int | None,
    executed: Decimal,
    gas_usd: Decimal,
    fee_usd: Decimal,
    tax_usd: Decimal,
    slippage_bps: float,
    realized_pnl_usd: float | None = None,
) -> OrderResponse:
    amount = _decimal(order.amount)
    slippage_paid = amount * _decimal(slippage_bps) / Decimal("10000")
    fees_total = gas_usd + fee_usd + tax_usd
    net = amount - fees_total
    if net <= 0:
        raise ValueError("fees, gas, and token taxes exceed the order amount")
    estimated_quantity = net / executed
    return OrderResponse(
        status="SUCCESS",
        trade_id=trade_id,
        token_address=order.token_address,
        side=order.side,
        input_amount=round(float(amount), 8),
        executed_price=round(float(executed), 8),
        fees_paid=round(float(gas_usd + fee_usd), 8),
        slippage_paid=round(float(slippage_paid), 8),
        token_tax_paid=round(float(tax_usd), 8),
        net_amount=round(float(net), 8),
        estimated_quantity=round(float(estimated_quantity), 12),
        realized_pnl_usd=realized_pnl_usd,
        source="paper_simulation",
        warning="Paper trading only. No on-chain order was created.",
    )


def simulate_trade(
    order: OrderRequest,
    *,
    api_key: ApiKey | None = None,
    db: Session | None = None,
) -> OrderResponse:
    audit = get_token_security_audit(order.token_address)
    if audit.get("is_honeypot"):
        raise HTTPException(status_code=409, detail="Honeypot detected, blocked")

    market = get_token_market_data(order.token_address)
    chain = str(market.get("chain") or "evm")
    spot = _market_price(order, market)
    slippage_bps = (
        order.slippage_bps if order.slippage_bps is not None else chain_slippage_bps(chain)
    )
    gas = (
        order.gas_fee if order.gas_fee is not None else chain_gas_usd_estimate(chain, order.amount)
    )
    gas_usd = _decimal(gas)
    fee_usd = _decimal(order.amount) * _decimal(order.fee_bps) / Decimal("10000")
    tax_usd = _decimal(order.amount) * _tax_rate(order, audit)
    executed = _executed_price(spot, order.side, float(slippage_bps))

    trade_id: int | None = None
    realized_pnl = Decimal("0") if order.side == TradeSide.SELL else None
    if api_key is not None and db is not None:
        org_id = api_key.org_id
        if org_id is None:
            raise HTTPException(status_code=403, detail="API key is not scoped to an organization")
        trade = SimulatedTrade(
            org_id=org_id,
            api_key_id=api_key.id,
            token_address=order.token_address,
            side=order.side.value,
            amount=_decimal(order.amount),
            entry_price=executed,
            exit_price=executed if order.side == TradeSide.SELL else None,
            fees_total=gas_usd + fee_usd + tax_usd,
            slippage_bps=float(slippage_bps),
            realized_pnl_usd=realized_pnl,
            status="OPEN" if order.side == TradeSide.BUY else "CLOSED",
            created_at=utcnow_naive(),
            closed_at=utcnow_naive() if order.side == TradeSide.SELL else None,
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)
        trade_id = int(trade.id)

    return _build_response(
        order,
        trade_id=trade_id,
        executed=executed,
        gas_usd=gas_usd,
        fee_usd=fee_usd,
        tax_usd=tax_usd,
        slippage_bps=float(slippage_bps),
        realized_pnl_usd=0.0 if realized_pnl is not None else None,
    )


def position_response(trade: SimulatedTrade) -> PositionResponse:
    market = get_token_market_data(trade.token_address)
    current_price = float(market.get("price") or market.get("price_usd") or trade.entry_price)
    entry = float(trade.entry_price)
    notional = float(trade.amount)
    unrealized = ((current_price - entry) / entry) * notional
    unrealized -= float(trade.fees_total or 0.0)
    return PositionResponse(
        id=int(trade.id),
        token_address=trade.token_address,
        side=trade.side,
        amount=notional,
        entry_price=entry,
        current_price=current_price,
        fees_total=float(trade.fees_total),
        slippage_bps=float(trade.slippage_bps),
        status=trade.status,
        created_at=trade.created_at,
        unrealized_pnl_usd=round(unrealized, 8),
    )


def close_position(trade: SimulatedTrade) -> OrderResponse:
    if trade.status != "OPEN":
        raise ValueError("position is not open")
    order = OrderRequest(
        token_address=trade.token_address,
        amount=float(trade.amount),
        side=TradeSide.SELL,
    )
    market = get_token_market_data(trade.token_address)
    audit = get_token_security_audit(trade.token_address)
    chain = str(market.get("chain") or "evm")
    spot = _market_price(order, market)
    slippage_bps = chain_slippage_bps(chain)
    gas_usd = _decimal(chain_gas_usd_estimate(chain, order.amount))
    fee_usd = Decimal("0")
    tax_usd = _decimal(order.amount) * _tax_rate(order, audit)
    executed = _executed_price(spot, TradeSide.SELL, slippage_bps)

    pnl = (
        (executed - _decimal(trade.entry_price))
        / _decimal(trade.entry_price)
        * _decimal(trade.amount)
    )
    pnl -= _decimal(trade.fees_total or 0) + gas_usd + fee_usd + tax_usd
    trade.exit_price = executed
    trade.realized_pnl_usd = pnl
    trade.fees_total = _decimal(trade.fees_total or 0) + gas_usd + fee_usd + tax_usd
    trade.status = "CLOSED"
    trade.closed_at = utcnow_naive()
    return _build_response(
        order,
        trade_id=int(trade.id),
        executed=executed,
        gas_usd=gas_usd,
        fee_usd=fee_usd,
        tax_usd=tax_usd,
        slippage_bps=slippage_bps,
        realized_pnl_usd=round(float(pnl), 8),
    )
