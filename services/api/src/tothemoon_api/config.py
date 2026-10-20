from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsError(ValueError):
    """Raised when environment configuration is invalid and must be corrected."""


_DEFAULT_CORS_ORIGINS = [
    "http://127.0.0.1:4173",
    "http://localhost:4173",
]

_ROOT_DIR = Path(__file__).parent.parent.parent.parent.parent


class Settings(BaseSettings):
    app_name: str = "ToTheMoonTokens"
    app_env: str = Field("local", alias="APP_ENV")
    deployment_release_id: str = Field("", alias="TTM_RELEASE_ID")
    deployment_image_digest: str = Field("", alias="TTM_API_IMAGE")
    host: str = Field("127.0.0.1", alias="API_HOST")
    port: int = Field(8010, alias="API_PORT")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    enable_live_trading: bool = Field(False, alias="ENABLE_LIVE_TRADING")
    allow_mainnet_trading: bool = Field(False, alias="ALLOW_MAINNET_TRADING")
    live_trading_acknowledgement: str = Field("", alias="LIVE_TRADING_ACKNOWLEDGEMENT")
    live_trading_approval_token: str = Field("", alias="LIVE_TRADING_APPROVAL_TOKEN")
    wallet_mode: str = Field("manual_only", alias="WALLET_MODE")
    autonomous_payments_enabled: bool = Field(False, alias="AUTONOMOUS_PAYMENTS_ENABLED")
    default_exchange: str = Field("binance_spot_testnet", alias="DEFAULT_EXCHANGE")
    default_symbol: str = Field("BTCUSDT", alias="DEFAULT_SYMBOL")
    default_timeframe: str = Field("1m", alias="DEFAULT_TIMEFRAME")
    max_position_size_pct: float = Field(25.0, alias="MAX_POSITION_SIZE_PCT")
    max_daily_loss_pct: float = Field(3.0, alias="MAX_DAILY_LOSS_PCT")
    max_open_positions: int = Field(1, alias="MAX_OPEN_POSITIONS")
    default_fee_bps: float = Field(10.0, alias="DEFAULT_FEE_BPS")
    default_slippage_bps: float = Field(5.0, alias="DEFAULT_SLIPPAGE_BPS")
    binance_testnet_base_url: str = Field(
        "https://testnet.binance.vision", alias="BINANCE_TESTNET_BASE_URL"
    )
    binance_user_data_stream_url: str = Field(
        "wss://stream.testnet.binance.vision/ws", alias="BINANCE_USER_DATA_STREAM_URL"
    )
    arc_testnet_rpc_url: str = Field("https://rpc.testnet.arc.network", alias="ARC_TESTNET_RPC_URL")
    arc_native_usdc_token_address: str = Field(
        "0x0000000000000000000000000000000000000006",
        alias="ARC_NATIVE_USDC_TOKEN_ADDRESS",
    )
    cors_allowed_origins: str | list[str] = Field(
        _DEFAULT_CORS_ORIGINS, alias="CORS_ALLOWED_ORIGINS"
    )
    rate_limit_live_arm_per_minute: int = Field(5, alias="RATE_LIMIT_LIVE_ARM_PER_MINUTE")
    rate_limit_backtest_per_minute: int = Field(30, alias="RATE_LIMIT_BACKTEST_PER_MINUTE")
    rate_limit_api_key_per_minute: int = Field(60, alias="RATE_LIMIT_API_KEY_PER_MINUTE")
    marketplace_settlement_timeout_s: float = Field(
        3.0,
        alias="MARKETPLACE_SETTLEMENT_TIMEOUT_S",
    )

    circle_api_key: str = Field("", alias="CIRCLE_API_KEY")
    circle_entity_secret: str = Field("", alias="CIRCLE_ENTITY_SECRET")
    circle_wallet_set_id: str = Field("", alias="CIRCLE_WALLET_SET_ID")
    circle_billing_webhook_secret: str = Field("", alias="CIRCLE_BILLING_WEBHOOK_SECRET")
    circle_bootstrap_on_startup: bool = Field(False, alias="CIRCLE_BOOTSTRAP_ON_STARTUP")
    demo_buyer_wallet_role: str = Field("RESEARCH_03", alias="DEMO_BUYER_WALLET_ROLE")
    enable_demo_job_routes: bool = Field(False, alias="ENABLE_DEMO_JOB_ROUTES")

    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-3.1-pro-preview", alias="GEMINI_MODEL")
    gemini_strategy_model: str = Field("gemini-3.1-pro-preview", alias="GEMINI_STRATEGY_MODEL")
    gemini_fast_model: str = Field("gemini-3.1-flash-lite", alias="GEMINI_FAST_MODEL")
    gemini_use_vertexai: bool = Field(False, alias="GEMINI_USE_VERTEXAI")
    gemini_vertex_project: str = Field("", alias="GEMINI_VERTEX_PROJECT")
    gemini_vertex_location: str = Field("us-central1", alias="GEMINI_VERTEX_LOCATION")
    database_url: str = Field("sqlite:///./saas.db", alias="DATABASE_URL")
    allow_import_time_init_db: bool = Field(False, alias="ALLOW_IMPORT_TIME_INIT_DB")
    jwt_secret: str = Field("", alias="JWT_SECRET")
    jwt_algorithm: str = Field("HS256", alias="JWT_ALGORITHM")
    jwt_expires_minutes: int = Field(60, alias="JWT_EXPIRES_MINUTES")
    stripe_api_key: str = Field("", alias="STRIPE_API_KEY")
    stripe_pro_price_id: str = Field("", alias="STRIPE_PRO_PRICE_ID")
    stripe_enterprise_price_id: str = Field("", alias="STRIPE_ENTERPRISE_PRICE_ID")
    stripe_webhook_secret: str = Field("", alias="STRIPE_WEBHOOK_SECRET")
    stripe_checkout_base_url: str = Field(
        "https://checkout.stripe.com/c/pay/cs_test_ttm",
        alias="STRIPE_CHECKOUT_BASE_URL",
    )
    x402_payment_address: str = Field(
        "0x0000000000000000000000000000000000000007",
        alias="X402_PAYMENT_ADDRESS",
    )
    redis_url: str = Field("", alias="REDIS_URL")
    external_http_timeout_s: float = Field(5.0, alias="EXTERNAL_HTTP_TIMEOUT_S")
    goplus_api_key: str = Field("", alias="GOPLUS_API_KEY")
    tokensniffer_api_key: str = Field("", alias="TOKENSNIFFER_API_KEY")
    birdeye_api_key: str = Field("", alias="BIRDEYE_API_KEY")
    otel_service_name: str = Field("tothemoontokens-api", alias="OTEL_SERVICE_NAME")
    otel_exporter_otlp_endpoint: str = Field("", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_exporter_otlp_headers: str = Field("", alias="OTEL_EXPORTER_OTLP_HEADERS")
    sentry_dsn: str = Field("", alias="SENTRY_DSN")
    sentry_environment: str = Field("", alias="SENTRY_ENVIRONMENT")
    sentry_traces_sample_rate: float = Field(0.0, alias="SENTRY_TRACES_SAMPLE_RATE")
    observability_test_raise_enabled: bool = Field(
        False,
        alias="OBSERVABILITY_TEST_RAISE_ENABLED",
    )
    personal_trading_mode: bool = Field(False, alias="TTM_PERSONAL_TRADING_MODE")
    paper_capital_brl: float = Field(300.0, alias="TTM_PAPER_CAPITAL_BRL")
    paper_trade_amount_brl: float = Field(30.0, alias="TTM_PAPER_TRADE_AMOUNT_BRL")
    paper_max_position_pct: float = Field(10.0, alias="TTM_PAPER_MAX_POSITION_PCT")
    paper_max_daily_new_capital_pct: float = Field(
        100.0,
        alias="TTM_PAPER_MAX_DAILY_NEW_CAPITAL_PCT",
    )
    paper_max_open_positions: int = Field(10, alias="TTM_PAPER_MAX_OPEN_POSITIONS")
    paper_max_open_per_symbol: int = Field(2, alias="TTM_PAPER_MAX_OPEN_PER_SYMBOL")
    paper_liquid_slots: int = Field(7, alias="TTM_PAPER_LIQUID_SLOTS")
    paper_onchain_scout_slots: int = Field(3, alias="TTM_PAPER_ONCHAIN_SCOUT_SLOTS")
    paper_accelerated_research: bool = Field(True, alias="TTM_PAPER_ACCELERATED_RESEARCH")
    paper_high_cost_scout_pct: float = Field(
        12.0,
        alias="TTM_PAPER_HIGH_COST_SCOUT_PCT",
    )
    paper_max_roundtrip_cost_pct: float = Field(
        25.0,
        alias="TTM_PAPER_MAX_ROUNDTRIP_COST_PCT",
    )
    real_min_trade_amount_brl: float = Field(30.0, alias="TTM_REAL_MIN_TRADE_AMOUNT_BRL")
    scanner_free_discovery_enabled: bool = Field(
        True,
        alias="TTM_SCANNER_FREE_DISCOVERY_ENABLED",
    )
    real_mode_armed: bool = Field(False, alias="REAL_MODE_ARMED")
    discord_webhook_url: str = Field("", alias="DISCORD_WEBHOOK_URL")
    discord_bot_token: str = Field("", alias="DISCORD_BOT_TOKEN")
    discord_channel_id: str = Field("", alias="DISCORD_CHANNEL_ID")
    discord_allowed_user_id: str = Field("", alias="DISCORD_ALLOWED_USER_ID")
    discord_polling_enabled: bool = Field(True, alias="DISCORD_POLLING_ENABLED")

    model_config = SettingsConfigDict(
        env_file=(str(_ROOT_DIR / ".env"), str(_ROOT_DIR / ".env.hackathon")),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("cors_allowed_origins", mode="before")
    def _parse_cors(cls, v):
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @field_validator("enable_live_trading", "allow_mainnet_trading", mode="before")
    def _parse_bool(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        return v

    @property
    def settlement_auth_mode(self) -> Literal["manual", "programmatic"]:
        return "programmatic" if self.autonomous_payments_enabled else "manual"

    @property
    def runtime_mode(self) -> str:
        if self.enable_live_trading and not self.allow_mainnet_trading:
            return "guarded_testnet"
        if self.enable_live_trading and self.allow_mainnet_trading:
            return "blocked_mainnet"
        return "paper"

    def validate(self) -> None:  # type: ignore[override]
        errors: list[str] = []
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            errors.append(
                f"LOG_LEVEL must be one of DEBUG/INFO/WARNING/ERROR/CRITICAL, got {self.log_level!r}"
            )
        if not 1 <= int(self.port) <= 65535:
            errors.append(f"API_PORT must be between 1 and 65535, got {self.port}")
        if self.wallet_mode not in {"manual_only", "custodial", "disabled"}:
            errors.append(
                f"WALLET_MODE must be one of manual_only/custodial/disabled, got {self.wallet_mode!r}"
            )
        if self.autonomous_payments_enabled and self.wallet_mode == "disabled":
            errors.append(
                "AUTONOMOUS_PAYMENTS_ENABLED=true requires WALLET_MODE to allow Circle wallets."
            )
        if self.autonomous_payments_enabled and not self.circle_api_key:
            errors.append("AUTONOMOUS_PAYMENTS_ENABLED=true requires CIRCLE_API_KEY.")
        if self.autonomous_payments_enabled and not self.circle_entity_secret:
            errors.append("AUTONOMOUS_PAYMENTS_ENABLED=true requires CIRCLE_ENTITY_SECRET.")
        if self.autonomous_payments_enabled and not self.circle_wallet_set_id:
            errors.append("AUTONOMOUS_PAYMENTS_ENABLED=true requires CIRCLE_WALLET_SET_ID.")
        if (
            self.autonomous_payments_enabled
            and not self.gemini_api_key
            and not self.gemini_use_vertexai
        ):
            errors.append(
                "AUTONOMOUS_PAYMENTS_ENABLED=true requires GEMINI_API_KEY "
                "or GEMINI_USE_VERTEXAI=true."
            )
        if self.gemini_use_vertexai and not (
            self.gemini_vertex_project
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCLOUD_PROJECT")
        ):
            errors.append(
                "GEMINI_USE_VERTEXAI=true requires GEMINI_VERTEX_PROJECT or GOOGLE_CLOUD_PROJECT."
            )
        if not 0 < float(self.max_position_size_pct) <= 100:
            errors.append(
                f"MAX_POSITION_SIZE_PCT must be in (0, 100], got {self.max_position_size_pct}"
            )
        if not 0 < float(self.max_daily_loss_pct) <= 100:
            errors.append(f"MAX_DAILY_LOSS_PCT must be in (0, 100], got {self.max_daily_loss_pct}")
        if int(self.max_open_positions) < 1:
            errors.append(f"MAX_OPEN_POSITIONS must be >= 1, got {self.max_open_positions}")
        if float(self.default_fee_bps) < 0 or float(self.default_slippage_bps) < 0:
            errors.append("DEFAULT_FEE_BPS and DEFAULT_SLIPPAGE_BPS must be >= 0")
        if int(self.rate_limit_live_arm_per_minute) < 1:
            errors.append(
                f"RATE_LIMIT_LIVE_ARM_PER_MINUTE must be >= 1, got {self.rate_limit_live_arm_per_minute}"
            )
        if int(self.rate_limit_backtest_per_minute) < 1:
            errors.append(
                f"RATE_LIMIT_BACKTEST_PER_MINUTE must be >= 1, got {self.rate_limit_backtest_per_minute}"
            )
        if float(self.marketplace_settlement_timeout_s) <= 0:
            errors.append(
                "MARKETPLACE_SETTLEMENT_TIMEOUT_S must be > 0, "
                f"got {self.marketplace_settlement_timeout_s}"
            )
        if float(self.external_http_timeout_s) <= 0:
            errors.append(
                f"EXTERNAL_HTTP_TIMEOUT_S must be > 0, got {self.external_http_timeout_s}"
            )
        if not 0 <= float(self.sentry_traces_sample_rate) <= 1:
            errors.append(
                "SENTRY_TRACES_SAMPLE_RATE must be between 0 and 1, "
                f"got {self.sentry_traces_sample_rate}"
            )
        if float(self.paper_capital_brl) <= 0:
            errors.append(f"TTM_PAPER_CAPITAL_BRL must be > 0, got {self.paper_capital_brl}")
        if float(self.paper_trade_amount_brl) <= 0:
            errors.append(
                f"TTM_PAPER_TRADE_AMOUNT_BRL must be > 0, got {self.paper_trade_amount_brl}"
            )
        if not 0 < float(self.paper_max_position_pct) <= 100:
            errors.append(
                "TTM_PAPER_MAX_POSITION_PCT must be in (0, 100], "
                f"got {self.paper_max_position_pct}"
            )
        if not 0 < float(self.paper_max_daily_new_capital_pct) <= 100:
            errors.append(
                "TTM_PAPER_MAX_DAILY_NEW_CAPITAL_PCT must be in (0, 100], "
                f"got {self.paper_max_daily_new_capital_pct}"
            )
        if int(self.paper_max_open_positions) < 1:
            errors.append(
                f"TTM_PAPER_MAX_OPEN_POSITIONS must be >= 1, got {self.paper_max_open_positions}"
            )
        if int(self.paper_max_open_per_symbol) < 1:
            errors.append(
                f"TTM_PAPER_MAX_OPEN_PER_SYMBOL must be >= 1, got {self.paper_max_open_per_symbol}"
            )
        if int(self.paper_liquid_slots) < 0:
            errors.append(f"TTM_PAPER_LIQUID_SLOTS must be >= 0, got {self.paper_liquid_slots}")
        if int(self.paper_onchain_scout_slots) < 0:
            errors.append(
                "TTM_PAPER_ONCHAIN_SCOUT_SLOTS must be >= 0, "
                f"got {self.paper_onchain_scout_slots}"
            )
        paper_slot_total = int(self.paper_liquid_slots) + int(self.paper_onchain_scout_slots)
        if paper_slot_total <= 0:
            errors.append("TTM_PAPER_LIQUID_SLOTS + TTM_PAPER_ONCHAIN_SCOUT_SLOTS must be > 0")
        if paper_slot_total > int(self.paper_max_open_positions):
            errors.append(
                "TTM_PAPER_LIQUID_SLOTS + TTM_PAPER_ONCHAIN_SCOUT_SLOTS must be <= "
                "TTM_PAPER_MAX_OPEN_POSITIONS"
            )
        if not 0 < float(self.paper_high_cost_scout_pct) <= 100:
            errors.append(
                "TTM_PAPER_HIGH_COST_SCOUT_PCT must be in (0, 100], "
                f"got {self.paper_high_cost_scout_pct}"
            )
        if not 0 < float(self.paper_max_roundtrip_cost_pct) <= 100:
            errors.append(
                "TTM_PAPER_MAX_ROUNDTRIP_COST_PCT must be in (0, 100], "
                f"got {self.paper_max_roundtrip_cost_pct}"
            )
        if float(self.paper_high_cost_scout_pct) > float(self.paper_max_roundtrip_cost_pct):
            errors.append(
                "TTM_PAPER_HIGH_COST_SCOUT_PCT must be <= "
                "TTM_PAPER_MAX_ROUNDTRIP_COST_PCT"
            )
        if float(self.real_min_trade_amount_brl) <= 0:
            errors.append(
                "TTM_REAL_MIN_TRADE_AMOUNT_BRL must be > 0, "
                f"got {self.real_min_trade_amount_brl}"
            )
        if self.real_mode_armed and self.app_env.strip().lower() == "production":
            errors.append("REAL_MODE_ARMED=true is forbidden in production until MVP graduation")
        app_env = self.app_env.strip().lower()
        if app_env == "production" and self.allow_mainnet_trading:
            errors.append("ALLOW_MAINNET_TRADING=true is forbidden in production by project policy")
        if app_env == "production" and (not self.jwt_secret or len(self.jwt_secret) < 32):
            errors.append("JWT_SECRET >= 32 chars is required in production")
        if app_env == "production" and self.stripe_webhook_secret.strip() in {
            "",
            "whsec_dev_secret",
        }:
            errors.append("STRIPE_WEBHOOK_SECRET must be externally configured in production")
        if app_env == "production" and not self.stripe_api_key.strip():
            errors.append("STRIPE_API_KEY must be externally configured in production")
        if app_env == "production" and not self.stripe_pro_price_id.strip():
            errors.append("STRIPE_PRO_PRICE_ID must be externally configured in production")
        if app_env == "production" and not self.circle_billing_webhook_secret.strip():
            errors.append("CIRCLE_BILLING_WEBHOOK_SECRET must be externally configured in production")
        if app_env == "production" and self.allow_import_time_init_db:
            errors.append(
                "ALLOW_IMPORT_TIME_INIT_DB=true is forbidden in production; "
                "run Alembic migrations explicitly after backup/snapshot verification."
            )
        if app_env == "production" and self.enable_demo_job_routes:
            errors.append(
                "ENABLE_DEMO_JOB_ROUTES=true is forbidden in production; "
                "use authenticated SaaS job and payment routes instead."
            )
        if errors:
            raise SettingsError("\n".join(errors))


def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.validate()
    # Normalize types after parsing if needed, pydantic handles basic coercions though.
    settings.port = int(settings.port)
    settings.max_position_size_pct = float(settings.max_position_size_pct)
    settings.max_daily_loss_pct = float(settings.max_daily_loss_pct)
    settings.max_open_positions = int(settings.max_open_positions)
    settings.default_fee_bps = float(settings.default_fee_bps)
    settings.default_slippage_bps = float(settings.default_slippage_bps)
    settings.rate_limit_live_arm_per_minute = int(settings.rate_limit_live_arm_per_minute)
    settings.rate_limit_backtest_per_minute = int(settings.rate_limit_backtest_per_minute)
    settings.rate_limit_api_key_per_minute = int(settings.rate_limit_api_key_per_minute)
    settings.marketplace_settlement_timeout_s = float(settings.marketplace_settlement_timeout_s)
    settings.circle_bootstrap_on_startup = bool(settings.circle_bootstrap_on_startup)
    settings.autonomous_payments_enabled = bool(settings.autonomous_payments_enabled)
    settings.allow_import_time_init_db = bool(settings.allow_import_time_init_db)
    settings.app_env = settings.app_env.strip().lower()
    settings.log_level = settings.log_level.upper()
    settings.demo_buyer_wallet_role = settings.demo_buyer_wallet_role.strip().upper()
    settings.gemini_model = settings.gemini_model.strip()
    settings.gemini_strategy_model = (
        settings.gemini_strategy_model.strip() or "gemini-3.1-pro-preview"
    )
    settings.gemini_fast_model = settings.gemini_fast_model.strip() or "gemini-3.1-flash-lite"
    settings.gemini_vertex_project = (
        settings.gemini_vertex_project.strip()
        or os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        or os.getenv("GCLOUD_PROJECT", "").strip()
    )
    settings.gemini_vertex_location = settings.gemini_vertex_location.strip() or "us-central1"
    settings.database_url = settings.database_url.strip()
    settings.jwt_secret = settings.jwt_secret.strip()
    settings.jwt_algorithm = settings.jwt_algorithm.strip() or "HS256"
    settings.jwt_expires_minutes = int(settings.jwt_expires_minutes)
    settings.stripe_api_key = settings.stripe_api_key.strip()
    settings.stripe_pro_price_id = settings.stripe_pro_price_id.strip()
    settings.stripe_enterprise_price_id = settings.stripe_enterprise_price_id.strip()
    settings.stripe_webhook_secret = settings.stripe_webhook_secret.strip()
    settings.circle_billing_webhook_secret = settings.circle_billing_webhook_secret.strip()
    settings.stripe_checkout_base_url = settings.stripe_checkout_base_url.strip()
    settings.x402_payment_address = settings.x402_payment_address.strip()
    settings.redis_url = settings.redis_url.strip()
    settings.external_http_timeout_s = float(settings.external_http_timeout_s)
    settings.goplus_api_key = settings.goplus_api_key.strip()
    settings.tokensniffer_api_key = settings.tokensniffer_api_key.strip()
    settings.birdeye_api_key = settings.birdeye_api_key.strip()
    settings.otel_service_name = settings.otel_service_name.strip() or "tothemoontokens-api"
    settings.otel_exporter_otlp_endpoint = settings.otel_exporter_otlp_endpoint.strip()
    settings.otel_exporter_otlp_headers = settings.otel_exporter_otlp_headers.strip()
    settings.sentry_dsn = settings.sentry_dsn.strip()
    settings.sentry_environment = settings.sentry_environment.strip() or settings.app_env
    settings.sentry_traces_sample_rate = float(settings.sentry_traces_sample_rate)
    settings.observability_test_raise_enabled = bool(settings.observability_test_raise_enabled)
    settings.personal_trading_mode = bool(settings.personal_trading_mode)
    settings.paper_capital_brl = float(settings.paper_capital_brl)
    settings.paper_trade_amount_brl = float(settings.paper_trade_amount_brl)
    settings.paper_max_position_pct = float(settings.paper_max_position_pct)
    settings.paper_max_daily_new_capital_pct = float(settings.paper_max_daily_new_capital_pct)
    settings.paper_max_open_positions = int(settings.paper_max_open_positions)
    settings.paper_max_open_per_symbol = int(settings.paper_max_open_per_symbol)
    settings.paper_liquid_slots = int(settings.paper_liquid_slots)
    settings.paper_onchain_scout_slots = int(settings.paper_onchain_scout_slots)
    settings.paper_accelerated_research = bool(settings.paper_accelerated_research)
    settings.paper_high_cost_scout_pct = float(settings.paper_high_cost_scout_pct)
    settings.paper_max_roundtrip_cost_pct = float(settings.paper_max_roundtrip_cost_pct)
    settings.real_min_trade_amount_brl = float(settings.real_min_trade_amount_brl)
    settings.scanner_free_discovery_enabled = bool(settings.scanner_free_discovery_enabled)
    settings.real_mode_armed = bool(settings.real_mode_armed)
    settings.discord_webhook_url = settings.discord_webhook_url.strip()
    settings.discord_bot_token = settings.discord_bot_token.strip()
    settings.discord_channel_id = settings.discord_channel_id.strip()
    settings.discord_allowed_user_id = settings.discord_allowed_user_id.strip()
    settings.discord_polling_enabled = bool(settings.discord_polling_enabled)
    return settings
