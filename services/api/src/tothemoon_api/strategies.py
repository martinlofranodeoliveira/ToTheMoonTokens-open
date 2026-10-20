from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from .db_models import StrategyOutcome, utcnow_naive
from .models import Candle, MarketRegime, StrategyDescriptor, StrategyId

STRATEGIES: list[StrategyDescriptor] = [
    StrategyDescriptor(
        id="ema_crossover",
        name="EMA Crossover",
        description="Segue tendencia quando a media curta cruza acima da longa.",
        market_regime="trend",
        risk_tier="low",
    ),
    StrategyDescriptor(
        id="breakout",
        name="Breakout Range",
        description="Compra rompimento de maxima recente e sai na perda de estrutura.",
        market_regime="expansion",
        risk_tier="medium",
    ),
    StrategyDescriptor(
        id="mean_reversion",
        name="Mean Reversion",
        description="Compra desvios negativos contra uma media curta em mercado lateral.",
        market_regime="range",
        risk_tier="medium",
    ),
]


@dataclass(frozen=True, slots=True)
class StrategyExecutionPolicy:
    allowed_entry_regimes: tuple[MarketRegime, ...]
    stop_loss_pct: float
    take_profit_pct: float
    max_hold_bars: int
    position_size_scale: float
    cooldown_bars_after_loss: int
    loss_streak_for_cooldown: int = 1
    signal_exit_min_pct: float = -999.0


EXECUTION_POLICIES: dict[StrategyId, StrategyExecutionPolicy] = {
    "ema_crossover": StrategyExecutionPolicy(
        allowed_entry_regimes=("bull",),
        stop_loss_pct=0.42,
        take_profit_pct=1.1,
        max_hold_bars=40,
        position_size_scale=0.8,
        cooldown_bars_after_loss=18,
        loss_streak_for_cooldown=2,
    ),
    "breakout": StrategyExecutionPolicy(
        allowed_entry_regimes=("bull",),
        stop_loss_pct=0.34,
        take_profit_pct=0.92,
        max_hold_bars=20,
        position_size_scale=0.55,
        cooldown_bars_after_loss=22,
        loss_streak_for_cooldown=1,
    ),
    "mean_reversion": StrategyExecutionPolicy(
        allowed_entry_regimes=("chop",),
        stop_loss_pct=0.2,
        take_profit_pct=0.48,
        max_hold_bars=8,
        position_size_scale=0.35,
        cooldown_bars_after_loss=26,
        loss_streak_for_cooldown=1,
        signal_exit_min_pct=0.34,
    ),
}


def strategy_catalog() -> list[StrategyDescriptor]:
    return STRATEGIES


def execution_policy(strategy_id: StrategyId) -> StrategyExecutionPolicy:
    return EXECUTION_POLICIES[strategy_id]


def profile_score(
    profile: str,
    db: Session,
    *,
    org_id: int | None = None,
    days: int = 30,
    min_outcomes: int = 10,
) -> float:
    query = db.query(StrategyOutcome).filter(
        StrategyOutcome.profile_name == profile,
        StrategyOutcome.created_at >= utcnow_naive() - timedelta(days=days),
        StrategyOutcome.paper_pnl_brl.is_not(None),
    )
    if org_id is not None:
        query = query.filter(StrategyOutcome.org_id == org_id)
    rows = query.all()
    if len(rows) < min_outcomes:
        return 0.0

    wins = [row for row in rows if row.paper_pnl_brl is not None and row.paper_pnl_brl > 0]
    losses = [row for row in rows if row.paper_pnl_brl is not None and row.paper_pnl_brl <= 0]
    rugs = [row for row in rows if row.classification == "rug"]
    profit = sum(
        (row.paper_pnl_brl for row in wins if row.paper_pnl_brl is not None),
        Decimal("0"),
    )
    loss = abs(
        sum(
            (row.paper_pnl_brl for row in losses if row.paper_pnl_brl is not None),
            Decimal("0"),
        )
    )
    win_rate = len(wins) / len(rows)
    rug_rate = len(rugs) / len(rows)
    profit_factor = float(profit) / max(float(loss), 1.0)

    score = profit_factor * 100
    score *= 1 - rug_rate * 2
    score *= max(0.5, win_rate * 2)
    return round(float(score), 6)


def _ema(values: list[float], period: int) -> list[float]:
    multiplier = 2 / (period + 1)
    result: list[float] = []
    current = values[0]
    for value in values:
        current = (value - current) * multiplier + current
        result.append(current)
    return result


def _rolling_average(values: list[float], start: int, end: int) -> float:
    window = values[start:end]
    if not window:
        return 0.0
    return sum(window) / len(window)


def build_signals(strategy_id: StrategyId, candles: list[Candle]) -> list[str]:
    closes = [candle.close for candle in candles]
    volumes = [candle.volume for candle in candles]
    signals = ["hold"] * len(candles)
    policy = execution_policy(strategy_id)

    if strategy_id == "ema_crossover":
        fast = _ema(closes, 9)
        slow = _ema(closes, 21)
        for index in range(21, len(candles)):
            regime_ok = candles[index].regime in policy.allowed_entry_regimes
            trend_confirmed = closes[index] > slow[index] * 1.0008 and fast[index] > fast[index - 1]
            if regime_ok and trend_confirmed and fast[index] > slow[index] * 1.001:
                signals[index] = "buy"
            elif fast[index] < slow[index] * 0.999 or candles[index].regime == "bear":
                signals[index] = "sell"
        return signals

    if strategy_id == "breakout":
        for index in range(20, len(candles)):
            prior_high = max(candle.high for candle in candles[index - 20 : index])
            prior_low = min(candle.low for candle in candles[index - 10 : index])
            avg_volume = _rolling_average(volumes, index - 20, index)
            regime_ok = candles[index].regime in policy.allowed_entry_regimes
            volume_confirmed = candles[index].volume >= avg_volume * 1.15
            trend_support = closes[index] >= _rolling_average(closes, index - 8, index) * 1.001
            if (
                regime_ok
                and volume_confirmed
                and trend_support
                and candles[index].close > prior_high * 1.0008
            ):
                signals[index] = "buy"
            elif candles[index].close < prior_low or candles[index].regime == "bear":
                signals[index] = "sell"
        return signals

    anchor = _ema(closes, 8)
    for index in range(20, len(candles)):
        window = closes[index - 20 : index]
        mean = sum(window) / len(window)
        variance = sum((value - mean) ** 2 for value in window) / len(window)
        stddev = math.sqrt(variance) or 1.0
        zscore = (closes[index] - mean) / stddev
        avg_volume = _rolling_average(volumes, index - 20, index)
        quiet_tape = candles[index].volume <= avg_volume * 1.05
        reversal_candle = closes[index] > candles[index].open and closes[index] > closes[index - 1]
        reclaiming_anchor = closes[index] >= anchor[index] * 0.9985
        if (
            candles[index].regime in policy.allowed_entry_regimes
            and quiet_tape
            and reversal_candle
            and reclaiming_anchor
            and zscore < -2.05
        ):
            signals[index] = "buy"
        elif (
            zscore > 0.85
            or closes[index] >= anchor[index] * 1.003
            or candles[index].regime == "bull"
        ):
            signals[index] = "sell"

    return signals
