from __future__ import annotations

from fastapi.testclient import TestClient

from tothemoon_api.journal import clear_trades
from tothemoon_api.main import app

client = TestClient(app)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "trade-1",
        "strategy_id": "ema_crossover",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "entry_time": "2026-01-01T10:00:00Z",
        "exit_time": "2026-01-01T11:00:00Z",
        "entry_price": 20000.0,
        "exit_price": 20500.0,
        "position_size": 0.5,
        "side": "long",
        "risk_taken": 100.0,
        "setup_reason": "EMA crossed over",
        "market_regime": "trending",
        "pnl": 500.0,
        "drawdown": 10.0,
        "outcome": "win",
    }
    payload.update(overrides)
    return payload


def test_paper_journal_lifecycle_and_alias_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_JOURNAL_FILE", str(tmp_path / "paper_journal.json"))
    clear_trades()

    first = client.post("/api/paper/journal", json=_payload())
    assert first.status_code == 200
    assert first.json()["direction"] == "long"
    assert first.json()["status"] == "closed"

    second = client.post(
        "/api/journal/entries",
        json=_payload(
            id="trade-2",
            strategy_id="breakout",
            symbol="ETHUSDT",
            timeframe="5m",
            entry_time="2026-01-02T10:00:00Z",
            exit_time="2026-01-02T10:15:00Z",
            entry_price=3100.0,
            exit_price=3050.0,
            direction="short",
            pnl=-200.0,
            drawdown=22.0,
            outcome="loss",
        ),
    )
    assert second.status_code == 200
    assert second.json()["direction"] == "short"

    entries = client.get("/api/paper/journal")
    assert entries.status_code == 200
    payload = entries.json()
    assert len(payload) == 2
    assert payload[0]["id"] == "trade-2"

    performance = client.get("/api/paper/performance")
    assert performance.status_code == 200
    metrics = performance.json()
    assert metrics["total_trades"] == 2
    assert metrics["total_pnl"] == 300.0
    assert metrics["win_rate_pct"] == 50.0
    assert metrics["by_strategy"]["ema_crossover"]["total_pnl"] == 500.0
    assert metrics["by_symbol"]["ETHUSDT"]["total_trades"] == 1

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert len(body["recent_trades"]) == 2
    assert body["performance"]["total_trades"] == 2

    cleared = client.delete("/api/paper/journal")
    assert cleared.status_code == 200
    assert cleared.json()["ok"] is True
    assert client.get("/api/journal/entries").json() == []
