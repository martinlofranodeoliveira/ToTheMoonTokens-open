from __future__ import annotations

from fastapi.testclient import TestClient

from tothemoon_api.journal import clear_journal
from tothemoon_api.main import app
from tothemoon_api.news import clear_news_store
from tothemoon_api.observability import rate_limiter

client = TestClient(app)


def setup_function() -> None:
    clear_journal()
    clear_news_store()
    rate_limiter.reset()


def test_walk_forward_endpoint_returns_split_metrics() -> None:
    response = client.post(
        "/api/backtests/walk-forward",
        json={
            "strategy_id": "ema_crossover",
            "lookback_bars": 180,
            "train_split_pct": 60,
            "risk_tier": "low",
            "checklist": {
                "trend_alignment": True,
                "momentum_confirm": True,
                "volume_expansion": True,
                "key_level_rejection": True,
                "no_upcoming_news": True,
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["train_metrics"]["checklist_score"] == 100
    assert payload["validation_metrics"]["checklist_score"] == 100
    assert payload["train_metrics"]["is_setup_blocked"] is False
    assert payload["validation_metrics"]["is_setup_blocked"] is False


def test_journal_endpoints_record_and_aggregate_closed_trades() -> None:
    winning_trade = {
        "strategy_id": "ema_crossover",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "regime": "bull",
        "entry": {
            "timestamp": "2026-01-01T12:00:00Z",
            "price": 100.0,
            "size": 1.0,
            "reason": "Breakout confirmation",
            "risk_assumed": 1.0,
        },
        "exit": {
            "timestamp": "2026-01-01T12:05:00Z",
            "price": 102.0,
            "reason": "Target hit",
        },
        "pnl": 100.0,
        "outcome": "win",
    }
    losing_trade = {
        "strategy_id": "ema_crossover",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "regime": "bear",
        "entry": {
            "timestamp": "2026-01-01T13:00:00Z",
            "price": 100.0,
            "size": 1.0,
            "reason": "Pullback entry",
            "risk_assumed": 1.0,
        },
        "exit": {
            "timestamp": "2026-01-01T13:03:00Z",
            "price": 99.0,
            "reason": "Stop hit",
        },
        "pnl": -50.0,
        "outcome": "loss",
    }

    first = client.post("/api/journal/trades", json=winning_trade)
    second = client.post("/api/journal/trades", json=losing_trade)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"]
    assert second.json()["id"]

    trades = client.get("/api/journal/trades")
    assert trades.status_code == 200
    assert len(trades.json()) == 2

    aggregates = client.get("/api/journal/performance?strategy_id=ema_crossover")
    assert aggregates.status_code == 200
    payload = aggregates.json()
    assert payload["total_trades"] == 2
    assert payload["win_rate"] == 0.5
    assert payload["profit_factor"] == 2.0
    assert payload["total_pnl"] == 50.0
    assert payload["outcomes_by_regime"]["bull"]["win"] == 1
    assert payload["outcomes_by_regime"]["bear"]["loss"] == 1


def test_news_endpoints_ingest_filter_and_block_high_risk_headlines() -> None:
    payload = {
        "headline": "SEC lawsuit puts Bitcoin ETF approval at risk",
        "timestamp": "2026-01-02T09:00:00Z",
        "source": "wire",
        "body": "Regulation pressure and court action increase uncertainty for BTC pairs.",
    }

    first = client.post("/api/news/ingest", json=payload)
    duplicate = client.post("/api/news/ingest", json=payload)
    assert first.status_code == 200
    assert first.json()["status"] == "ingested"
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "ignored"

    listing = client.get("/api/news?horizon=long&category=regulatory")
    assert listing.status_code == 200
    items = listing.json()
    assert len(items) == 1
    assert items[0]["category"] == "regulatory"
    assert items[0]["impact_score"] >= 8

    risk = client.get("/api/news/risk?symbol=BTCUSDT")
    assert risk.status_code == 200
    risk_payload = risk.json()
    assert risk_payload["is_trading_blocked"] is True
    assert risk_payload["risk_level"] == "high"
    assert risk_payload["reasons"]


def test_scalp_validation_endpoint_accepts_clean_low_risk_setup() -> None:
    response = client.post(
        "/api/scalp/validate",
        json={
            "setup": {
                "symbol": "BTCUSDT",
                "entry_price": 100.0,
                "stop_loss": 99.0,
                "target_price": 101.8,
                "strategy_limit_bps": 8.0,
                "risk_tier": "low",
            },
            "context": {
                "timeframe": "1m",
                "regime": "trend",
                "has_high_impact_news": False,
                "extreme_volatility": False,
                "trend_aligned": True,
                "volume_above_baseline": True,
                "at_support_or_resistance": True,
                "spread_bps": 2.0,
                "slippage_bps": 2.0,
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["is_eligible"] is True
    assert payload["reasons"] == ["Eligible: Setup meets all criteria"]
