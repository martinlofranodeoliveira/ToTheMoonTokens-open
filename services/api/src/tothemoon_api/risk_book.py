from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .config import Settings


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True)
class RiskPolicy:
    total_capital_brl: Decimal
    max_position_pct: Decimal = Decimal("10")
    max_open_positions: int = 10
    max_daily_new_capital_pct: Decimal = Decimal("100")
    max_drawdown_pct: Decimal = Decimal("30")
    take_profit_first_leg_multiplier: Decimal = Decimal("2.0")
    take_profit_first_leg_close_pct: Decimal = Decimal("50")
    stop_loss_pct: Decimal = Decimal("30")
    moonbag_take_profit_5x_alert: bool = True
    moonbag_take_profit_10x_alert: bool = True
    moonbag_take_profit_50x_alert: bool = True

    @property
    def max_position_brl(self) -> Decimal:
        return self.total_capital_brl * self.max_position_pct / Decimal("100")

    @property
    def max_daily_new_capital_brl(self) -> Decimal:
        return self.total_capital_brl * self.max_daily_new_capital_pct / Decimal("100")


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    policy: RiskPolicy
    requested_brl: Decimal
    open_positions_count: int
    today_new_capital_brl: Decimal


def policy_from_settings(settings: Settings) -> RiskPolicy:
    return RiskPolicy(
        total_capital_brl=_decimal(settings.paper_capital_brl),
        max_position_pct=_decimal(settings.paper_max_position_pct),
        max_open_positions=int(settings.paper_max_open_positions),
        max_daily_new_capital_pct=_decimal(settings.paper_max_daily_new_capital_pct),
    )


def decide_new_paper_position(
    *,
    policy: RiskPolicy,
    requested_brl: Decimal,
    open_positions_count: int,
    today_new_capital_brl: Decimal,
    current_drawdown_pct: Decimal = Decimal("0"),
) -> RiskDecision:
    if requested_brl <= 0:
        return RiskDecision(
            False,
            "position_size_must_be_positive",
            policy,
            requested_brl,
            open_positions_count,
            today_new_capital_brl,
        )
    if open_positions_count >= policy.max_open_positions:
        return RiskDecision(
            False,
            "max_open_positions reached",
            policy,
            requested_brl,
            open_positions_count,
            today_new_capital_brl,
        )
    if requested_brl > policy.max_position_brl:
        return RiskDecision(
            False,
            "max_position_pct exceeded",
            policy,
            requested_brl,
            open_positions_count,
            today_new_capital_brl,
        )
    if today_new_capital_brl + requested_brl > policy.max_daily_new_capital_brl:
        return RiskDecision(
            False,
            "max_daily_new_capital_pct exceeded",
            policy,
            requested_brl,
            open_positions_count,
            today_new_capital_brl,
        )
    if current_drawdown_pct >= policy.max_drawdown_pct:
        return RiskDecision(
            False,
            "max_drawdown_pct reached",
            policy,
            requested_brl,
            open_positions_count,
            today_new_capital_brl,
        )
    return RiskDecision(
        True,
        "allowed",
        policy,
        requested_brl,
        open_positions_count,
        today_new_capital_brl,
    )


def risk_policy_payload(policy: RiskPolicy) -> dict[str, object]:
    return {
        "total_capital_brl": float(policy.total_capital_brl),
        "max_position_pct": float(policy.max_position_pct),
        "max_position_brl": float(policy.max_position_brl),
        "max_open_positions": policy.max_open_positions,
        "max_daily_new_capital_pct": float(policy.max_daily_new_capital_pct),
        "max_daily_new_capital_brl": float(policy.max_daily_new_capital_brl),
        "max_drawdown_pct": float(policy.max_drawdown_pct),
        "take_profit_first_leg_multiplier": float(policy.take_profit_first_leg_multiplier),
        "take_profit_first_leg_close_pct": float(policy.take_profit_first_leg_close_pct),
        "stop_loss_pct": float(policy.stop_loss_pct),
    }
