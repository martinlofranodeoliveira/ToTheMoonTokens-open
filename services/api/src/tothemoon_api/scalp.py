from __future__ import annotations

from .models import MarketContext, ScalpSetup, ValidationResult


def validate_scalp_setup(setup: ScalpSetup, context: MarketContext) -> ValidationResult:
    reasons: list[str] = []

    if context.has_high_impact_news:
        reasons.append("Blocked: High impact news window")
    if context.extreme_volatility:
        reasons.append("Blocked: Extreme volatility")
    if not context.trend_aligned:
        reasons.append("Blocked: Trend not aligned")
    if not context.volume_above_baseline:
        reasons.append("Blocked: Volume not above baseline")
    if not context.at_support_or_resistance:
        reasons.append("Blocked: Not at clear support/resistance level")

    if setup.stop_loss <= 0:
        reasons.append("Blocked: Invalid stop loss level")

    risk = abs(setup.entry_price - setup.stop_loss)
    reward = abs(setup.target_price - setup.entry_price)
    rr_ratio = reward / risk if risk > 0 else 0.0

    if setup.target_price > setup.entry_price:
        if setup.stop_loss >= setup.entry_price:
            reasons.append("Blocked: Stop loss must be below entry for long setups")
    elif setup.target_price < setup.entry_price:
        if setup.stop_loss <= setup.entry_price:
            reasons.append("Blocked: Stop loss must be above entry for short setups")
    else:
        reasons.append("Blocked: Target price must be different from entry price")

    total_cost_bps = context.spread_bps + context.slippage_bps
    if total_cost_bps > setup.strategy_limit_bps:
        reasons.append(
            "Blocked: Spread and slippage "
            f"({total_cost_bps} bps) exceed strategy limit ({setup.strategy_limit_bps} bps)"
        )

    if context.timeframe not in {"1m", "3m", "5m"}:
        reasons.append(
            f"Blocked: Timeframe {context.timeframe} not suitable for scalp (requires 1m, 3m, 5m)"
        )

    if setup.risk_tier == "low":
        if context.regime != "trend":
            reasons.append(
                f"Blocked: Low risk tier requires 'trend' regime, found '{context.regime}'"
            )
        if rr_ratio < 1.5:
            reasons.append(
                f"Blocked: Low risk tier requires minimum R:R of 1.5, found {rr_ratio:.2f}"
            )
    elif setup.risk_tier == "medium":
        if rr_ratio < 1.0:
            reasons.append(
                f"Blocked: Medium risk tier requires minimum R:R of 1.0, found {rr_ratio:.2f}"
            )

    is_eligible = len(reasons) == 0
    if is_eligible:
        reasons.append("Eligible: Setup meets all criteria")

    return ValidationResult(is_eligible=is_eligible, reasons=reasons)
