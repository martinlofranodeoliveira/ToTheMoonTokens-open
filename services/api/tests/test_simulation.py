from decimal import Decimal

import pytest
from fastapi import HTTPException

from tothemoon_api.db_models import ApiKey, Membership, Organization, Plan, SimulatedTrade, User
from tothemoon_api.simulation import (
    OrderRequest,
    TradeSide,
    close_position,
    simulate_trade,
)


def _api_key(db_session) -> ApiKey:
    plan = Plan(
        code="test-plan",
        name="Test",
        monthly_request_limit=1000,
        monthly_token_audit_limit=100,
        active_api_key_limit=10,
    )
    user = User(email="synthetic-user-48@example.com", password_hash="hashed")
    db_session.add_all([plan, user])
    db_session.flush()
    org = Organization(name="Simulation Org", plan_id=plan.id)
    db_session.add(org)
    db_session.flush()
    api_key = ApiKey(
        user_id=user.id,
        org_id=org.id,
        name="simulation",
        prefix="ttm_sk_live_",
        key_hash="hashed-key",
    )
    db_session.add_all([Membership(user_id=user.id, org_id=org.id, role="owner"), api_key])
    db_session.commit()
    db_session.refresh(api_key)
    return api_key


def test_order_request_validation():
    req = OrderRequest(token_address="0xABC", amount=100.0, side=TradeSide.BUY)
    assert req.token_address == "0xABC"
    assert req.amount == 100.0
    assert req.side == "BUY"


def test_buy_records_open_trade(db_session):
    api_key = _api_key(db_session)
    req = OrderRequest(token_address="0xTEST", amount=100.0, side=TradeSide.BUY)
    response = simulate_trade(req, api_key=api_key, db=db_session)

    assert response.status == "SUCCESS"
    assert response.trade_id is not None
    assert response.executed_price == 1.05525
    assert response.fees_paid == 8.0
    assert response.net_amount == 92.0

    trade = db_session.query(SimulatedTrade).filter_by(id=response.trade_id).one()
    assert trade.org_id == api_key.org_id
    assert trade.status == "OPEN"
    assert trade.side == "BUY"


def test_honeypot_blocks_buy(db_session):
    api_key = _api_key(db_session)
    req = OrderRequest(token_address="0xSCAM", amount=100.0, side=TradeSide.BUY)

    with pytest.raises(HTTPException) as exc:
        simulate_trade(req, api_key=api_key, db=db_session)

    assert exc.value.status_code == 409


def test_sell_closes_position_and_computes_pnl(db_session, monkeypatch):
    api_key = _api_key(db_session)

    monkeypatch.setattr(
        "tothemoon_api.simulation.get_token_market_data",
        lambda token: {"token_address": token, "price": 1.0, "chain": "solana"},
    )
    opened = simulate_trade(
        OrderRequest(token_address="0xGAIN", amount=100.0, side=TradeSide.BUY),
        api_key=api_key,
        db=db_session,
    )
    trade = db_session.query(SimulatedTrade).filter_by(id=opened.trade_id).one()

    monkeypatch.setattr(
        "tothemoon_api.simulation.get_token_market_data",
        lambda token: {"token_address": token, "price": 1.2, "chain": "solana"},
    )
    closed = close_position(trade)
    db_session.commit()

    assert closed.status == "SUCCESS"
    assert closed.realized_pnl_usd is not None
    assert closed.realized_pnl_usd > 0
    assert trade.status == "CLOSED"
    assert trade.exit_price is not None
    assert trade.realized_pnl_usd == Decimal(str(closed.realized_pnl_usd))


def test_chain_specific_slippage_applied(monkeypatch):
    monkeypatch.setattr(
        "tothemoon_api.simulation.get_token_market_data",
        lambda token: {"token_address": token, "price": 1.0, "chain": "base"},
    )
    req = OrderRequest(token_address="0xBASE", amount=100.0, side=TradeSide.BUY)

    response = simulate_trade(req)

    assert response.executed_price == 1.003
    assert response.fees_paid == 0.4


def test_cost_env_overrides_reject_negative_and_non_finite_values(monkeypatch):
    monkeypatch.setenv(
        "SIMULATION_SLIPPAGE_BPS_BY_CHAIN",
        '{"base": -10, "solana": "nan", "bsc": 25}',
    )
    monkeypatch.setenv("SIMULATION_GAS_USD_BY_CHAIN", "base:-1,solana:inf,bsc:0.25")

    monkeypatch.setattr(
        "tothemoon_api.simulation.get_token_market_data",
        lambda token: {"token_address": token, "price": 1.0, "chain": "base"},
    )
    base_response = simulate_trade(OrderRequest(token_address="0xBASE", amount=100.0, side="BUY"))

    monkeypatch.setattr(
        "tothemoon_api.simulation.get_token_market_data",
        lambda token: {"token_address": token, "price": 1.0, "chain": "bsc"},
    )
    bsc_response = simulate_trade(OrderRequest(token_address="0xBSC", amount=100.0, side="BUY"))

    assert base_response.executed_price == 1.003
    assert base_response.fees_paid == 0.4
    assert bsc_response.executed_price == 1.0025
    assert bsc_response.fees_paid == 0.25


def test_personal_trading_mode_uses_conservative_brl_cost_profile(monkeypatch):
    monkeypatch.setenv("TTM_PERSONAL_TRADING_MODE", "true")
    monkeypatch.setattr(
        "tothemoon_api.simulation.get_token_market_data",
        lambda token: {"token_address": token, "price": 1.0, "chain": "arbitrum"},
    )

    response = simulate_trade(OrderRequest(token_address="0xARB", amount=3.0, side="BUY"))

    assert response.executed_price == 1.005
    assert response.fees_paid == 0.3
