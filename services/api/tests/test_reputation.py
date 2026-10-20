from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tothemoon_api.journal import clear_journal
from tothemoon_api.main import app

client = TestClient(app)


def test_reputation_endpoint_aggregates_agent_trades(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_JOURNAL_FILE", str(tmp_path / "paper_journal.json"))
    clear_journal()

    now = datetime.now(UTC)
    base_payload = {
        "strategy_id": "ema_crossover",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "market_regime": "bull",
        "position_size": 1.0,
        "direction": "long",
        "risk_taken": 0.5,
        "status": "closed",
    }
    payloads = [
        {
            **base_payload,
            "entry_time": (now - timedelta(hours=2)).isoformat(),
            "exit_time": (now - timedelta(hours=1, minutes=30)).isoformat(),
            "entry_price": 100.0,
            "exit_price": 101.5,
            "setup_reason": "[agent:research_01] morning breakout",
            "exit_reason": "target hit",
            "pnl": 15.0,
            "outcome": "win",
        },
        {
            **base_payload,
            "entry_time": (now - timedelta(hours=1)).isoformat(),
            "exit_time": (now - timedelta(minutes=30)).isoformat(),
            "entry_price": 102.0,
            "exit_price": 101.2,
            "setup_reason": "[agent:research_01] fade failed breakout",
            "exit_reason": "stop hit",
            "pnl": -8.0,
            "outcome": "loss",
        },
    ]

    for payload in payloads:
        response = client.post("/api/journal/trades", json=payload)
        assert response.status_code == 200

    response = client.get("/api/agents/research_01/reputation")
    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_id"] == "research_01"
    assert payload["total_trades"] == 2
    assert payload["closed_trades"] == 2
    assert payload["wins"] == 1
    assert payload["losses"] == 1
    assert payload["win_rate_pct"] == 50.0
    assert payload["reputation_score"] > 0


def test_reputation_endpoint_returns_unproven_when_agent_has_no_history():
    response = client.get("/api/agents/unknown_agent/reputation")
    assert response.status_code == 200
    payload = response.json()
    assert payload["tier"] == "unproven"
    assert payload["reputation_score"] == 0.0
