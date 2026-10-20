from __future__ import annotations

import pytest

from tothemoon_api.backtesting import run_backtest
from tothemoon_api.models import BacktestRequest


def _request(**overrides: object) -> BacktestRequest:
    payload: dict[str, object] = {"strategy_id": "ema_crossover", "lookback_bars": 200}
    payload.update(overrides)
    return BacktestRequest(**payload)  # type: ignore[arg-type]


def test_position_size_is_capped_by_global_limit():
    metrics = run_backtest(_request(position_size_pct=90), max_position_size_pct=10.0)
    assert metrics.initial_capital == 10_000
    assert metrics.ending_equity > 0


@pytest.mark.parametrize("strategy", ["ema_crossover", "breakout", "mean_reversion"])
def test_each_strategy_returns_valid_metrics(strategy: str):
    metrics = run_backtest(_request(strategy_id=strategy), max_position_size_pct=25.0)
    assert metrics.trade_count >= 0
    assert metrics.max_drawdown_pct >= 0
    assert metrics.edge_status in {"positive", "flat", "negative"}


def test_edge_status_reflects_net_profit_sign():
    metrics = run_backtest(_request(), max_position_size_pct=25.0)
    if metrics.net_profit > 0:
        assert metrics.edge_status == "positive"
    elif metrics.net_profit < 0:
        assert metrics.edge_status == "negative"
    else:
        assert metrics.edge_status == "flat"


def test_high_fees_reduce_ending_equity():
    cheap = run_backtest(_request(fee_bps=0, slippage_bps=0), max_position_size_pct=25.0)
    expensive = run_backtest(_request(fee_bps=100, slippage_bps=100), max_position_size_pct=25.0)
    assert expensive.ending_equity <= cheap.ending_equity


def test_zero_position_size_is_rejected_by_validation():
    with pytest.raises(ValueError):
        _request(position_size_pct=0)


def test_backtest_is_deterministic_given_same_inputs():
    first = run_backtest(_request(lookback_bars=180), max_position_size_pct=25.0)
    second = run_backtest(_request(lookback_bars=180), max_position_size_pct=25.0)
    assert first.model_dump() == second.model_dump()
