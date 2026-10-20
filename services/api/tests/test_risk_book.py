from decimal import Decimal

from tothemoon_api.config import Settings
from tothemoon_api.risk_book import (
    RiskPolicy,
    decide_new_paper_position,
    policy_from_settings,
)


def test_policy_from_settings_uses_personal_paper_capital():
    policy = policy_from_settings(
        Settings(
            paper_capital_brl=300,
            paper_max_position_pct=10,
            paper_max_daily_new_capital_pct=100,
            paper_max_open_positions=10,
        )
    )

    assert policy.total_capital_brl == Decimal("300")
    assert policy.max_position_brl == Decimal("30")
    assert policy.max_daily_new_capital_brl == Decimal("300")
    assert policy.max_open_positions == 10


def test_position_above_ten_percent_is_rejected():
    policy = RiskPolicy(total_capital_brl=Decimal("300"))

    decision = decide_new_paper_position(
        policy=policy,
        requested_brl=Decimal("31"),
        open_positions_count=0,
        today_new_capital_brl=Decimal("0"),
    )

    assert decision.allowed is False
    assert decision.reason == "max_position_pct exceeded"


def test_daily_exposure_cap_is_rejected():
    policy = RiskPolicy(total_capital_brl=Decimal("300"))

    decision = decide_new_paper_position(
        policy=policy,
        requested_brl=Decimal("30"),
        open_positions_count=0,
        today_new_capital_brl=Decimal("280"),
    )

    assert decision.allowed is False
    assert decision.reason == "max_daily_new_capital_pct exceeded"


def test_ten_paper_slots_are_allowed_inside_cycle_caps():
    policy = RiskPolicy(total_capital_brl=Decimal("300"), max_open_positions=10)

    decision = decide_new_paper_position(
        policy=policy,
        requested_brl=Decimal("30"),
        open_positions_count=9,
        today_new_capital_brl=Decimal("270"),
    )

    assert decision.allowed is True
    assert decision.reason == "allowed"
