from __future__ import annotations

from fastapi.testclient import TestClient

import tothemoon_api.main as main_module
from tothemoon_api.observability import rate_limiter

client = TestClient(main_module.app)


def setup_function() -> None:
    rate_limiter.reset()


def _backtest_body() -> dict[str, object]:
    return {
        "strategy_id": "ema_crossover",
        "lookback_bars": 100,
        "initial_capital": 5000,
        "position_size_pct": 10,
        "fee_bps": 5,
        "slippage_bps": 3,
    }


def test_backtest_rate_limiter_returns_429_after_budget_exhausted():
    limit = main_module.settings.rate_limit_backtest_per_minute
    for _ in range(limit):
        response = client.post("/api/backtests/run", json=_backtest_body())
        assert response.status_code == 200

    over = client.post("/api/backtests/run", json=_backtest_body())
    assert over.status_code == 429
    payload = over.json()
    assert payload["error"] == "rate_limited"
    assert int(over.headers["retry-after"]) >= 1


def test_rate_limiter_reset_hook_restores_backtest_budget():
    for _ in range(main_module.settings.rate_limit_backtest_per_minute + 1):
        client.post("/api/backtests/run", json=_backtest_body())
    rate_limiter.reset()
    response = client.post("/api/backtests/run", json=_backtest_body())
    assert response.status_code == 200


def test_rate_limit_rejections_are_counted_in_metrics():
    for _ in range(main_module.settings.rate_limit_backtest_per_minute + 2):
        client.post("/api/backtests/run", json=_backtest_body())
    response = client.get("/metrics")
    assert "rate_limit_rejections_total" in response.text
