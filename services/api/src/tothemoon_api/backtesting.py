from __future__ import annotations

from typing import Literal

from .market_data import generate_sample_candles, get_historical_candles
from .models import (
    BacktestMetrics,
    BacktestRequest,
    Candle,
    ProbabilityChecklist,
    RiskProfile,
    RiskTier,
    WalkForwardRequest,
    WalkForwardResult,
)
from .observability import BACKTESTS_RUN_TOTAL, get_logger
from .strategies import build_signals, execution_policy

log = get_logger(__name__)


RISK_PROFILES: dict[RiskTier, RiskProfile] = {
    "low": RiskProfile(
        tier="low",
        max_position_size_pct=10.0,
        max_daily_loss_pct=2.0,
        requires_confirmation=True,
        min_probability_score=80,
    ),
    "medium": RiskProfile(
        tier="medium",
        max_position_size_pct=20.0,
        max_daily_loss_pct=5.0,
        requires_confirmation=True,
        min_probability_score=60,
    ),
    "high": RiskProfile(
        tier="high",
        max_position_size_pct=35.0,
        max_daily_loss_pct=8.0,
        requires_confirmation=False,
        min_probability_score=40,
    ),
}


def _safe_pct(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return (numerator / denominator) * 100


def _resolve_edge_status(net_profit: float) -> Literal["positive", "flat", "negative"]:
    if net_profit > 0:
        return "positive"
    if net_profit < 0:
        return "negative"
    return "flat"


def calculate_checklist_score(checklist: ProbabilityChecklist | None) -> int:
    if not checklist:
        return 0

    score = 0
    if checklist.trend_alignment:
        score += 1
    if checklist.momentum_confirm:
        score += 1
    if checklist.volume_expansion:
        score += 1
    if checklist.key_level_rejection:
        score += 1
    if checklist.no_upcoming_news:
        score += 1
    return score * 20


def _build_candles_for_request(request: BacktestRequest) -> list[Candle]:
    if request.dataset_id == "binance_testnet":
        return get_historical_candles(request.symbol, request.timeframe, request.lookback_bars)
    return generate_sample_candles(
        length=request.lookback_bars,
        dataset_id=request.dataset_id,
        seed=request.seed,
    )


def _blocked_metrics(
    request: BacktestRequest, checklist_score: int, reason: str
) -> BacktestMetrics:
    return BacktestMetrics(
        strategy_id=request.strategy_id,
        timeframe=request.timeframe,
        risk_tier=request.risk_tier,
        initial_capital=request.initial_capital,
        ending_equity=request.initial_capital,
        net_profit=0.0,
        total_return_pct=0.0,
        max_drawdown_pct=0.0,
        trade_count=0,
        win_rate_pct=0.0,
        profit_factor=0.0,
        edge_status="flat",
        checklist_score=checklist_score,
        is_setup_blocked=True,
        block_reasons=[reason],
    )


def _compute_metrics(
    request: BacktestRequest,
    candles: list[Candle],
    signals: list[str],
    max_position_size_pct: float,
) -> BacktestMetrics:
    profile = RISK_PROFILES[request.risk_tier]
    checklist_score = calculate_checklist_score(request.checklist)
    if checklist_score < profile.min_probability_score:
        reason = (
            f"Checklist score {checklist_score} is below minimum "
            f"{profile.min_probability_score} for tier {profile.tier}"
        )
        return _blocked_metrics(request, checklist_score, reason)

    position_limit = (
        min(
            request.position_size_pct,
            max_position_size_pct,
            profile.max_position_size_pct,
        )
        / 100
    )

    cash = request.initial_capital
    units = 0.0
    entry_cost = 0.0
    entry_price = 0.0
    bars_in_trade = 0
    gross_wins = 0.0
    gross_losses = 0.0
    wins = 0
    trade_count = 0
    peak_equity = cash
    max_drawdown = 0.0
    current_win_streak = 0
    current_loss_streak = 0
    max_consecutive_wins = 0
    max_consecutive_losses = 0
    regime_pnl = {"bull": 0.0, "bear": 0.0, "chop": 0.0}
    policy = execution_policy(request.strategy_id)
    position_limit *= policy.position_size_scale
    cooldown_bars_remaining = 0

    for candle, signal in zip(candles, signals, strict=True):
        if units == 0 and cooldown_bars_remaining > 0:
            cooldown_bars_remaining -= 1

        marked_equity = cash + units * candle.close
        peak_equity = max(peak_equity, marked_equity)
        if peak_equity > 0:
            drawdown = (peak_equity - marked_equity) / peak_equity
            max_drawdown = max(max_drawdown, drawdown)

        forced_exit = False
        if units > 0 and entry_price > 0:
            bars_in_trade += 1
            change_pct = ((candle.close - entry_price) / entry_price) * 100
            forced_exit = (
                change_pct <= -policy.stop_loss_pct
                or change_pct >= policy.take_profit_pct
                or bars_in_trade >= policy.max_hold_bars
            )

        if units == 0 and signal == "buy":
            if cooldown_bars_remaining > 0:
                continue
            if candle.regime not in policy.allowed_entry_regimes:
                continue
            notional = cash * position_limit
            if notional <= 0:
                continue
            fill_price = candle.close * (1 + request.slippage_bps / 10_000)
            fee = notional * (request.fee_bps / 10_000)
            units = max((notional - fee) / fill_price, 0.0)
            cash -= notional
            entry_cost = notional
            entry_price = fill_price
            bars_in_trade = 0
            trade_count += 1
            continue

        signal_exit_allowed = signal == "sell"
        if units > 0 and signal == "sell" and policy.signal_exit_min_pct > -900:
            change_pct = (
                ((candle.close - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
            )
            signal_exit_allowed = (
                change_pct >= policy.signal_exit_min_pct or candle.regime == "bear"
            )

        if units > 0 and (forced_exit or signal_exit_allowed):
            gross = units * candle.close * (1 - request.slippage_bps / 10_000)
            fee = gross * (request.fee_bps / 10_000)
            proceeds = gross - fee
            pnl = proceeds - entry_cost
            regime_pnl[candle.regime] = regime_pnl.get(candle.regime, 0.0) + pnl
            if pnl >= 0:
                wins += 1
                gross_wins += pnl
                current_win_streak += 1
                current_loss_streak = 0
                max_consecutive_wins = max(max_consecutive_wins, current_win_streak)
            else:
                gross_losses += abs(pnl)
                current_loss_streak += 1
                current_win_streak = 0
                max_consecutive_losses = max(max_consecutive_losses, current_loss_streak)
                if current_loss_streak >= policy.loss_streak_for_cooldown:
                    cooldown_bars_remaining = max(
                        cooldown_bars_remaining, policy.cooldown_bars_after_loss
                    )
            cash += proceeds
            units = 0.0
            entry_cost = 0.0
            entry_price = 0.0
            bars_in_trade = 0

    if units > 0:
        final_price = candles[-1].close
        gross = units * final_price * (1 - request.slippage_bps / 10_000)
        fee = gross * (request.fee_bps / 10_000)
        proceeds = gross - fee
        pnl = proceeds - entry_cost
        regime_pnl[candles[-1].regime] = regime_pnl.get(candles[-1].regime, 0.0) + pnl
        if pnl >= 0:
            wins += 1
            gross_wins += pnl
            current_win_streak += 1
            max_consecutive_wins = max(max_consecutive_wins, current_win_streak)
        else:
            gross_losses += abs(pnl)
            current_loss_streak += 1
            max_consecutive_losses = max(max_consecutive_losses, current_loss_streak)
        cash += proceeds
        entry_price = 0.0
        bars_in_trade = 0

    ending_equity = round(cash, 4)
    net_profit = round(ending_equity - request.initial_capital, 4)
    closed_trades = trade_count if trade_count > 0 else 0
    win_rate_fraction = (wins / closed_trades) if closed_trades else 0.0
    profit_factor = (
        gross_wins / gross_losses if gross_losses else (gross_wins if gross_wins else 0.0)
    )
    avg_win = (gross_wins / wins) if wins else 0.0
    losses = closed_trades - wins
    avg_loss = (gross_losses / losses) if losses else 0.0
    expectancy = (win_rate_fraction * avg_win) - ((1 - win_rate_fraction) * avg_loss)
    payoff_ratio = (avg_win / avg_loss) if avg_loss else (avg_win if avg_win else 0.0)
    edge_status = _resolve_edge_status(net_profit)

    metrics = BacktestMetrics(
        strategy_id=request.strategy_id,
        timeframe=request.timeframe,
        risk_tier=request.risk_tier,
        initial_capital=request.initial_capital,
        ending_equity=ending_equity,
        net_profit=net_profit,
        total_return_pct=round(_safe_pct(net_profit, request.initial_capital), 4),
        max_drawdown_pct=round(max_drawdown * 100, 4),
        trade_count=closed_trades,
        win_rate_pct=round(win_rate_fraction * 100, 4),
        profit_factor=round(profit_factor, 4),
        edge_status=edge_status,
        expectancy=round(expectancy, 4),
        payoff_ratio=round(payoff_ratio, 4),
        max_consecutive_wins=max_consecutive_wins,
        max_consecutive_losses=max_consecutive_losses,
        checklist_score=checklist_score,
        regime_metrics={
            "bull": {"pnl": round(regime_pnl.get("bull", 0.0), 4)},
            "bear": {"pnl": round(regime_pnl.get("bear", 0.0), 4)},
            "chop": {"pnl": round(regime_pnl.get("chop", 0.0), 4)},
        },
    )

    BACKTESTS_RUN_TOTAL.labels(strategy_id=request.strategy_id, edge_status=edge_status).inc()
    log.info(
        "backtest_completed",
        strategy_id=request.strategy_id,
        symbol=request.symbol,
        timeframe=request.timeframe,
        dataset_id=request.dataset_id,
        risk_tier=request.risk_tier,
        trade_count=metrics.trade_count,
        net_profit=metrics.net_profit,
        checklist_score=metrics.checklist_score,
        edge_status=metrics.edge_status,
    )
    return metrics


def run_backtest(request: BacktestRequest, max_position_size_pct: float) -> BacktestMetrics:
    candles = _build_candles_for_request(request)
    signals = build_signals(request.strategy_id, candles)
    return _compute_metrics(request, candles, signals, max_position_size_pct)


def run_walk_forward(
    request: WalkForwardRequest, max_position_size_pct: float
) -> WalkForwardResult:
    candles = _build_candles_for_request(request)
    signals = build_signals(request.strategy_id, candles)
    split_index = int(len(candles) * (request.train_split_pct / 100.0))

    train_request = BacktestRequest(**request.model_dump())
    validation_request = BacktestRequest(**request.model_dump())
    train_request.lookback_bars = len(candles[:split_index])
    validation_request.lookback_bars = len(candles[split_index:])

    train_metrics = _compute_metrics(
        train_request,
        candles[:split_index],
        signals[:split_index],
        max_position_size_pct,
    )
    validation_metrics = _compute_metrics(
        validation_request,
        candles[split_index:],
        signals[split_index:],
        max_position_size_pct,
    )
    return WalkForwardResult(
        train_metrics=train_metrics,
        validation_metrics=validation_metrics,
    )
