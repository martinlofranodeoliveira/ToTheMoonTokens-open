from __future__ import annotations

import pytest

from tothemoon_api.config import Settings, SettingsError


def test_default_settings_validate_in_paper_mode():
    settings = Settings()
    settings.validate()
    assert settings.runtime_mode == "paper"
    assert settings.gemini_model == "gemini-3.1-pro-preview"
    assert settings.gemini_strategy_model == "gemini-3.1-pro-preview"
    assert settings.gemini_fast_model == "gemini-3.1-flash-lite"


def test_invalid_log_level_rejected():
    settings = Settings(log_level="VERBOSE")
    with pytest.raises(SettingsError, match="LOG_LEVEL"):
        settings.validate()


def test_invalid_wallet_mode_rejected():
    settings = Settings(wallet_mode="shared_hot_wallet")
    with pytest.raises(SettingsError, match="WALLET_MODE"):
        settings.validate()


def test_port_out_of_range_rejected():
    settings = Settings(port=0)
    with pytest.raises(SettingsError, match="API_PORT"):
        settings.validate()


def test_position_size_out_of_range_rejected():
    settings = Settings(max_position_size_pct=150)
    with pytest.raises(SettingsError, match="MAX_POSITION_SIZE_PCT"):
        settings.validate()


def test_negative_fees_rejected():
    settings = Settings(default_fee_bps=-1.0)
    with pytest.raises(SettingsError):
        settings.validate()


def test_personal_paper_risk_settings_validate():
    settings = Settings(
        personal_trading_mode=True,
        paper_capital_brl=300,
        paper_trade_amount_brl=30,
        paper_max_position_pct=10,
        paper_max_daily_new_capital_pct=100,
        paper_max_open_positions=10,
        paper_max_open_per_symbol=2,
        paper_liquid_slots=7,
        paper_onchain_scout_slots=3,
        paper_high_cost_scout_pct=12,
        paper_max_roundtrip_cost_pct=25,
        real_min_trade_amount_brl=30,
    )

    settings.validate()
    assert settings.personal_trading_mode is True
    assert settings.paper_max_open_per_symbol == 2
    assert settings.paper_liquid_slots == 7
    assert settings.paper_onchain_scout_slots == 3
    assert settings.paper_high_cost_scout_pct == 12
    assert settings.paper_max_roundtrip_cost_pct == 25
    assert settings.real_min_trade_amount_brl == 30


def test_invalid_personal_paper_capital_rejected():
    settings = Settings(paper_capital_brl=0)
    with pytest.raises(SettingsError, match="TTM_PAPER_CAPITAL_BRL"):
        settings.validate()


def test_invalid_real_min_trade_amount_rejected():
    settings = Settings(real_min_trade_amount_brl=0)
    with pytest.raises(SettingsError, match="TTM_REAL_MIN_TRADE_AMOUNT_BRL"):
        settings.validate()


def test_high_cost_scout_threshold_must_not_exceed_max_cost():
    settings = Settings(paper_high_cost_scout_pct=30, paper_max_roundtrip_cost_pct=25)
    with pytest.raises(SettingsError, match="TTM_PAPER_HIGH_COST_SCOUT_PCT"):
        settings.validate()


def test_paper_slot_budgets_must_fit_open_position_cap():
    settings = Settings(
        paper_max_open_positions=10,
        paper_liquid_slots=8,
        paper_onchain_scout_slots=3,
    )
    with pytest.raises(SettingsError, match="TTM_PAPER_LIQUID_SLOTS"):
        settings.validate()


def test_paper_symbol_position_cap_must_be_positive():
    settings = Settings(paper_max_open_per_symbol=0)
    with pytest.raises(SettingsError, match="TTM_PAPER_MAX_OPEN_PER_SYMBOL"):
        settings.validate()


def test_cors_origins_parse_from_default_list():
    settings = Settings()
    assert "http://127.0.0.1:4173" in settings.cors_allowed_origins


def test_backtest_rate_limit_must_be_positive():
    settings = Settings(rate_limit_backtest_per_minute=0)
    with pytest.raises(SettingsError, match="RATE_LIMIT_BACKTEST_PER_MINUTE"):
        settings.validate()


def test_autonomous_payments_require_circle_and_gemini_config():
    settings = Settings(
        autonomous_payments_enabled=True,
        wallet_mode="custodial",
        circle_api_key="",
        circle_entity_secret="",
        circle_wallet_set_id="",
        gemini_api_key="",
    )
    with pytest.raises(SettingsError) as exc:
        settings.validate()
    message = str(exc.value)
    assert "CIRCLE_API_KEY" in message
    assert "CIRCLE_ENTITY_SECRET" in message
    assert "CIRCLE_WALLET_SET_ID" in message
    assert "GEMINI_API_KEY" in message
    assert "GEMINI_USE_VERTEXAI" in message


def test_autonomous_payments_enable_programmatic_settlement_mode():
    settings = Settings(
        autonomous_payments_enabled=True,
        wallet_mode="custodial",
        circle_api_key="api-key",
        circle_entity_secret="entity-secret",
        circle_wallet_set_id="wallet-set",
        gemini_api_key="gemini-key",
    )
    settings.validate()
    assert settings.settlement_auth_mode == "programmatic"


def test_autonomous_payments_can_use_vertexai_for_gemini():
    settings = Settings(
        autonomous_payments_enabled=True,
        wallet_mode="custodial",
        circle_api_key="api-key",
        circle_entity_secret="entity-secret",
        circle_wallet_set_id="wallet-set",
        gemini_api_key="",
        gemini_use_vertexai=True,
        gemini_vertex_project="demo-project",
    )
    settings.validate()
    assert settings.settlement_auth_mode == "programmatic"


def test_multiple_errors_are_reported_together():
    settings = Settings(
        log_level="NOPE",
        wallet_mode="bogus",
        max_position_size_pct=0,
    )
    with pytest.raises(SettingsError) as exc:
        settings.validate()
    message = str(exc.value)
    assert "LOG_LEVEL" in message
    assert "WALLET_MODE" in message
    assert "MAX_POSITION_SIZE_PCT" in message


def test_production_env_validation_is_case_insensitive():
    settings = Settings(app_env="Production", jwt_secret="short")
    with pytest.raises(SettingsError, match="JWT_SECRET"):
        settings.validate()


def test_production_rejects_import_time_init_db():
    settings = Settings(
        app_env="production",
        jwt_secret="production-jwt-secret-with-32-characters",
        stripe_webhook_secret="whsec_production_secret",
        circle_billing_webhook_secret="circle-billing-secret-ref",
        allow_import_time_init_db=True,
    )
    with pytest.raises(SettingsError, match="ALLOW_IMPORT_TIME_INIT_DB"):
        settings.validate()


def test_production_rejects_demo_job_routes():
    settings = Settings(
        app_env="production",
        jwt_secret="production-jwt-secret-with-32-characters",
        stripe_webhook_secret="whsec_production_secret",
        circle_billing_webhook_secret="circle-billing-secret-ref",
        enable_demo_job_routes=True,
    )
    with pytest.raises(SettingsError, match="ENABLE_DEMO_JOB_ROUTES"):
        settings.validate()


def test_production_rejects_default_stripe_webhook_secret():
    settings = Settings(
        app_env="production",
        jwt_secret="production-jwt-secret-with-32-characters",
    )
    with pytest.raises(SettingsError, match="STRIPE_WEBHOOK_SECRET"):
        settings.validate()


def test_production_requires_stripe_operator_configuration():
    settings = Settings(
        app_env="production",
        jwt_secret="production-jwt-secret-with-32-characters",
        metrics_bearer_token="production-metrics-token-with-32-chars",
        stripe_webhook_secret="whsec_production_secret",
        circle_billing_webhook_secret="circle-billing-secret-ref",
    )
    with pytest.raises(SettingsError) as exc:
        settings.validate()
    message = str(exc.value)
    assert "STRIPE_API_KEY" in message
    assert "STRIPE_PRO_PRICE_ID" in message


def test_production_requires_circle_billing_webhook_secret():
    settings = Settings(
        app_env="production",
        jwt_secret="production-jwt-secret-with-32-characters",
        stripe_webhook_secret="whsec_production_secret",
    )
    with pytest.raises(SettingsError, match="CIRCLE_BILLING_WEBHOOK_SECRET"):
        settings.validate()


def test_get_settings_normalizes_app_env(monkeypatch):
    from tothemoon_api.config import get_settings

    monkeypatch.setenv("APP_ENV", "LOCAL")
    assert get_settings().app_env == "local"
