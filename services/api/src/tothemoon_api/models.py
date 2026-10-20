from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from .arc_adapter import ArcJobProof

StrategyId = Literal["ema_crossover", "breakout", "mean_reversion"]
RiskTier = Literal["low", "medium", "high"]
Horizon = Literal["short", "medium", "long"]
NewsCategory = Literal[
    "regulatory",
    "macro",
    "exchange-specific",
    "asset-specific",
    "general",
]
MarketRegime = Literal["bull", "bear", "chop"]


class RiskProfile(BaseModel):
    tier: RiskTier
    max_position_size_pct: float
    max_daily_loss_pct: float
    requires_confirmation: bool
    min_probability_score: int


class ProbabilityChecklist(BaseModel):
    trend_alignment: bool = False
    momentum_confirm: bool = False
    volume_expansion: bool = False
    key_level_rejection: bool = False
    no_upcoming_news: bool = False


class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    regime: MarketRegime = "chop"


class StrategyDescriptor(BaseModel):
    id: StrategyId
    name: str
    description: str
    market_regime: str
    risk_tier: RiskTier = "medium"


class BacktestRequest(BaseModel):
    strategy_id: StrategyId = "ema_crossover"
    symbol: str = "BTCUSDT"
    timeframe: str = "1m"
    lookback_bars: int = Field(default=180, ge=60, le=1000)
    initial_capital: float = Field(default=10_000, gt=0)
    position_size_pct: float = Field(default=25, gt=0, le=100)
    fee_bps: float = Field(default=10, ge=0, le=100)
    slippage_bps: float = Field(default=5, ge=0, le=100)
    dataset_id: str = "synthetic"
    seed: int = 42
    risk_tier: RiskTier = "medium"
    checklist: ProbabilityChecklist | None = None


class WalkForwardRequest(BacktestRequest):
    train_split_pct: float = Field(default=70, gt=0, lt=100)


class BacktestMetrics(BaseModel):
    strategy_id: StrategyId | None = None
    timeframe: str | None = None
    risk_tier: RiskTier | None = None
    initial_capital: float
    ending_equity: float
    net_profit: float
    total_return_pct: float
    max_drawdown_pct: float
    trade_count: int
    win_rate_pct: float
    profit_factor: float
    edge_status: Literal["positive", "flat", "negative"]
    expectancy: float = 0.0
    payoff_ratio: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    checklist_score: int = 0
    regime_metrics: dict[str, dict[str, float]] = Field(default_factory=dict)
    is_setup_blocked: bool = False
    block_reasons: list[str] = Field(default_factory=list)


class WalkForwardResult(BaseModel):
    train_metrics: BacktestMetrics
    validation_metrics: BacktestMetrics


class GuardrailStatus(BaseModel):
    mode: str
    can_submit_testnet_orders: bool
    can_submit_mainnet_orders: bool
    requires_manual_wallet_signature: bool
    settlement_auth_mode: Literal["manual", "programmatic"] = "manual"
    autonomous_payments_enabled: bool = False
    reasons: list[str]


class ConnectorStatus(BaseModel):
    settlement_network: str
    wallet_provider: str
    wallet_mode: str
    settlement_auth_mode: Literal["manual", "programmatic"] = "manual"
    autonomous_payments_enabled: bool = False
    wallet_set_id: str | None = None
    wallets_configured: int = 0
    wallets_loaded: bool = False
    treasury_address: str | None = None
    arc_rpc_url: str
    metamask_ready: bool
    agent_chat_ready: bool = False
    agent_model: str | None = None
    latency_ms: float | None = None
    reconnect_count: int = 0
    last_error: str | None = None


ProviderReadinessStatus = Literal["ready", "stale", "skipped", "degraded", "unknown", "unsafe"]
ProviderReadinessRollup = Literal["healthy", "degraded", "not_ready", "unsafe"]
ProviderCapability = Literal["market_data", "security_audit"]


class ProviderReadinessCapability(BaseModel):
    status: ProviderReadinessRollup


class ProviderReadinessProvider(BaseModel):
    name: str
    status: ProviderReadinessStatus
    source_status: Literal["ok", "skipped", "degraded", "unknown"]
    capabilities: list[ProviderCapability]
    requires_api_key: bool
    api_key_configured: bool
    read_only: bool
    supports_order_submission: bool
    mainnet_order_submission: bool
    public_exposure: bool
    paper_only_safe: bool
    safety_issue: str | None = None
    latency_ms: float | None = None
    observed_at: str | None = None
    last_error: str | None = None


class ProviderReadinessResponse(BaseModel):
    status: ProviderReadinessRollup
    stale_after_seconds: int
    provider_count: int
    providers: list[ProviderReadinessProvider]
    capabilities: dict[ProviderCapability, ProviderReadinessCapability]
    paper_only: bool
    order_submission_enabled: bool
    mainnet_order_submission_enabled: bool

OperatorReadinessModeStatus = Literal["ready", "limited", "blocked", "locked"]


class OperatorReadinessMode(BaseModel):
    id: Literal["mode_1", "mode_2", "mode_3"]
    name: str
    status: OperatorReadinessModeStatus
    evidence: list[str]
    blocking_reasons: list[str] = Field(default_factory=list)


class OperatorReadinessSafety(BaseModel):
    paper_only: bool
    order_submission_enabled: bool
    mainnet_order_submission_enabled: bool
    autonomous_signing_enabled: bool
    live_funds_enabled: bool


class OperatorReadinessEvidenceResponse(BaseModel):
    status: Literal["ready", "limited", "blocked"]
    runtime_mode: str
    generated_at: str
    safety: OperatorReadinessSafety
    modes: list[OperatorReadinessMode]
    provider_readiness: ProviderReadinessResponse
class AggregateBucket(BaseModel):
    total_trades: int
    total_pnl: float
    win_rate_pct: float
    average_drawdown: float


class PaperTradeEntry(BaseModel):
    timestamp: datetime
    price: float = Field(gt=0)
    size: float = Field(gt=0)
    reason: str
    risk_assumed: float = Field(ge=0)


class PaperTradeExit(BaseModel):
    timestamp: datetime
    price: float = Field(gt=0)
    reason: str


class PaperTradeRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    strategy_id: str
    symbol: str
    timeframe: str
    market_regime: str = Field(
        validation_alias=AliasChoices("market_regime", "regime"),
        serialization_alias="market_regime",
    )
    entry_time: datetime
    exit_time: datetime | None = None
    entry_price: float = Field(gt=0)
    exit_price: float | None = Field(default=None, gt=0)
    position_size: float = Field(gt=0)
    direction: Literal["long", "short"] = Field(
        default="long",
        validation_alias=AliasChoices("direction", "side"),
        serialization_alias="direction",
    )
    risk_taken: float = Field(ge=0)
    setup_reason: str
    exit_reason: str | None = None
    pnl: float | None = None
    mae: float | None = Field(default=None, ge=0)
    mfe: float | None = Field(default=None, ge=0)
    drawdown: float | None = Field(default=None, ge=0)
    time_in_trade_ms: int | None = Field(default=None, ge=0)
    time_in_trade_seconds: int | None = Field(default=None, ge=0)
    outcome: Literal["win", "loss", "breakeven", "open"] = "open"
    status: Literal["open", "closed"] = "open"

    @model_validator(mode="before")
    @classmethod
    def normalize_nested_payload(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        payload = dict(data)
        entry = payload.get("entry")
        exit_payload = payload.get("exit")

        if isinstance(entry, dict):
            payload.setdefault("entry_time", entry.get("timestamp"))
            payload.setdefault("entry_price", entry.get("price"))
            payload.setdefault("position_size", entry.get("size"))
            payload.setdefault("setup_reason", entry.get("reason"))
            payload.setdefault("risk_taken", entry.get("risk_assumed"))

        if isinstance(exit_payload, dict):
            payload.setdefault("exit_time", exit_payload.get("timestamp"))
            payload.setdefault("exit_price", exit_payload.get("price"))
            payload.setdefault("exit_reason", exit_payload.get("reason"))

        return payload

    @model_validator(mode="after")
    def infer_status_and_outcome(self) -> PaperTradeRecord:
        if self.status == "open" and (
            self.exit_time is not None
            or self.exit_price is not None
            or self.pnl is not None
            or self.outcome != "open"
        ):
            self.status = "closed"

        if self.status == "closed" and self.outcome == "open" and self.pnl is not None:
            if self.pnl > 0:
                self.outcome = "win"
            elif self.pnl < 0:
                self.outcome = "loss"
            else:
                self.outcome = "breakeven"

        if self.time_in_trade_ms is None and self.time_in_trade_seconds is not None:
            self.time_in_trade_ms = self.time_in_trade_seconds * 1000
        if self.time_in_trade_seconds is None and self.time_in_trade_ms is not None:
            self.time_in_trade_seconds = self.time_in_trade_ms // 1000

        return self


JournalEntry = PaperTradeRecord


class PerformanceAggregates(BaseModel):
    total_trades: int
    total_pnl: float
    win_rate: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    average_drawdown: float = 0.0
    outcomes_by_regime: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_strategy: dict[str, AggregateBucket] = Field(default_factory=dict)
    by_symbol: dict[str, AggregateBucket] = Field(default_factory=dict)
    by_timeframe: dict[str, AggregateBucket] = Field(default_factory=dict)


class DashboardResponse(BaseModel):
    app_name: str
    runtime_mode: str
    strategies: list[StrategyDescriptor]
    metrics: BacktestMetrics
    research_snapshots: list[BacktestMetrics] = Field(default_factory=list)
    guardrails: GuardrailStatus
    connectors: ConnectorStatus
    runtime_status: dict[str, Any] | None = None
    recent_trades: list[PaperTradeRecord] = Field(default_factory=list)
    performance: PerformanceAggregates | None = None
    journal_performance: PerformanceAggregates | None = None
    arc_jobs: list[ArcJobProof] = Field(default_factory=list)


class NewsItem(BaseModel):
    id: str
    headline: str
    timestamp: datetime
    horizon: Horizon
    category: NewsCategory
    impact_score: int = Field(ge=1, le=10)
    justification: str


class NewsIngestRequest(BaseModel):
    headline: str
    timestamp: datetime
    source: str
    body: str | None = None


class MarketContext(BaseModel):
    timeframe: str
    regime: Literal["trend", "range", "expansion"]
    has_high_impact_news: bool
    extreme_volatility: bool
    trend_aligned: bool
    volume_above_baseline: bool
    at_support_or_resistance: bool
    spread_bps: float
    slippage_bps: float


class ScalpSetup(BaseModel):
    symbol: str
    entry_price: float
    stop_loss: float
    target_price: float
    strategy_limit_bps: float = 10.0
    risk_tier: RiskTier = "low"


class ValidationResult(BaseModel):
    is_eligible: bool
    reasons: list[str]


class ScalpValidationRequest(BaseModel):
    setup: ScalpSetup
    context: MarketContext


class BillableArtifact(BaseModel):
    id: str
    name: str
    description: str
    price_usd: float
    type: Literal["delivery_packet", "review_bundle", "market_intel_brief"]


class PaymentIntentRequest(BaseModel):
    artifact_id: str
    buyer_address: str
    job_id: str | None = None

    @field_validator("artifact_id", "buyer_address", "job_id")
    @classmethod
    def payment_intent_values_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("payment intent values must not be blank")
        return cleaned


class PaymentIntentResponse(BaseModel):
    payment_id: str
    amount_usd: float
    currency: Literal["USDC"] = "USDC"
    deposit_address: str
    payment_requirement: dict[str, Any]
    status: Literal["pending", "verified", "failed"]
    job_id: str | None = None


class PaymentVerificationRequest(BaseModel):
    payment_id: str
    tx_hash: str

    @field_validator("payment_id", "tx_hash")
    @classmethod
    def verification_values_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("payment verification values must not be blank")
        return cleaned


class PaymentVerificationResponse(BaseModel):
    payment_id: str
    status: Literal["pending", "verified", "failed"]
    unlocked_artifact_id: str | None = None
    settlement_status: str | None = None
    reason: str | None = None


class PaymentPayRequest(BaseModel):
    payment_id: str

    @field_validator("payment_id")
    @classmethod
    def payment_id_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("payment_id must not be blank")
        return cleaned


class PaymentPayResponse(BaseModel):
    payment_id: str
    status: Literal["pending", "verified", "failed"]
    settlement_status: str | None = None
    tx_hash: str | None = None
    circle_transaction_id: str | None = None
    reason: str | None = None


class PaymentIntentRecord(BaseModel):
    payment_id: str
    artifact_id: str
    artifact_name: str
    artifact_type: str
    amount_usd: float
    buyer_address: str
    deposit_address: str
    job_id: str | None = None
    status: Literal["pending", "verified", "failed"]
    settlement_status: str | None = None
    reason: str | None = None
    tx_hash: str | None = None
    circle_transaction_id: str | None = None
    executed: bool = False
    download_url: str | None = None
    execution_message: str | None = None
    created_at: str
    updated_at: str


class JobExecutionRequest(BaseModel):
    artifact_id: str
    payment_id: str

    @field_validator("artifact_id", "payment_id")
    @classmethod
    def execution_values_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("job execution values must not be blank")
        return cleaned


class JobExecutionResponse(BaseModel):
    artifact_id: str
    status: Literal["completed", "failed"]
    message: str
    download_url: str | None = None


class AgentChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class AgentToolEvent(BaseModel):
    id: str | None = None
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] | None = None


class AgentChatRequest(BaseModel):
    message: str
    history: list[AgentChatTurn] = Field(default_factory=list)
    authorized_artifact_id: str | None = None


class AgentChatResponse(BaseModel):
    reply: str
    events: list[AgentToolEvent] = Field(default_factory=list)
    orders: list[PaymentIntentRecord] = Field(default_factory=list)
    settlement_auth_mode: Literal["manual", "programmatic"] = "manual"
    autonomous_payments_enabled: bool = False
    gemini: dict[str, object] | None = None
