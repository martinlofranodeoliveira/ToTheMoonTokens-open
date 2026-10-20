from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    api_keys: Mapped[list["ApiKey"]] = relationship(
        "ApiKey",
        back_populates="owner",
        cascade="all, delete-orphan",
    )
    memberships: Mapped[list["Membership"]] = relationship(
        "Membership",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    org_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    scopes: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    owner: Mapped[User] = relationship("User", back_populates="api_keys")
    organization: Mapped["Organization | None"] = relationship(
        "Organization",
        back_populates="api_keys",
    )
    simulated_trades: Mapped[list["SimulatedTrade"]] = relationship(
        "SimulatedTrade",
        back_populates="api_key",
    )


Index("ix_api_keys_active", ApiKey.user_id, ApiKey.revoked_at)


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    monthly_request_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_token_audit_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    active_api_key_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    organizations: Mapped[list["Organization"]] = relationship(
        "Organization", back_populates="plan"
    )

    def limit_for(self, resource: str) -> int:
        if self.code == "enterprise":
            return -1
        if resource == "token_audit":
            return self.monthly_token_audit_limit
        return self.monthly_request_limit


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plans.id"), nullable=False, index=True
    )
    real_mode_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    real_mode_approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    real_mode_daily_limit_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
        default=Decimal("50.0"),
    )
    bot_consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    plan: Mapped[Plan] = relationship("Plan", back_populates="organizations")
    api_keys: Mapped[list[ApiKey]] = relationship("ApiKey", back_populates="organization")
    provider_credentials: Mapped[list["OrgProviderCredential"]] = relationship(
        "OrgProviderCredential",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    token_discovery_history: Mapped[list["TokenDiscoveryHistory"]] = relationship(
        "TokenDiscoveryHistory",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    copilot_scanner_runs: Mapped[list["CopilotScannerRun"]] = relationship(
        "CopilotScannerRun",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    strategy_outcomes: Mapped[list["StrategyOutcome"]] = relationship(
        "StrategyOutcome",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    memberships: Mapped[list["Membership"]] = relationship(
        "Membership",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    simulated_trades: Mapped[list["SimulatedTrade"]] = relationship(
        "SimulatedTrade",
        back_populates="organization",
    )
    copilot_proposals: Mapped[list["CopilotProposal"]] = relationship(
        "CopilotProposal",
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "org_id", name="uq_memberships_user_org"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    user: Mapped[User] = relationship("User", back_populates="memberships")
    organization: Mapped[Organization] = relationship("Organization", back_populates="memberships")


class OrgProviderCredential(Base):
    __tablename__ = "org_provider_credentials"
    __table_args__ = (
        UniqueConstraint("org_id", "provider", name="uq_org_provider_credentials_org_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False, default="gemini-3.1-pro-preview")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)

    organization: Mapped[Organization] = relationship(
        "Organization",
        back_populates="provider_credentials",
    )


Index("ix_org_provider_credentials_org_provider", OrgProviderCredential.org_id, OrgProviderCredential.provider)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="inactive")
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="circle")
    provider_customer_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    organization: Mapped[Organization] = relationship(
        "Organization", back_populates="subscriptions"
    )


class BillingWebhookEvent(Base):
    __tablename__ = "billing_webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="stripe")
    event_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="processing")
    org_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("org_id", "day", "resource", name="uq_usage_records_org_day_resource"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    resource: Mapped[str] = mapped_column(String(40), nullable=False, default="simulate_order")
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    simulated_volume_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
        default=Decimal("0"),
    )


class SimulatedTrade(Base):
    __tablename__ = "simulated_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    api_key_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("api_keys.id"),
        nullable=True,
        index=True,
    )
    token_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    fees_total: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    slippage_bps: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    organization: Mapped[Organization] = relationship(
        "Organization",
        back_populates="simulated_trades",
    )
    api_key: Mapped[ApiKey | None] = relationship(
        "ApiKey",
        back_populates="simulated_trades",
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ua: Mapped[str | None] = mapped_column(Text, nullable=True)
    before: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class TokenDiscoveryHistory(Base):
    __tablename__ = "token_discovery_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    token_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown", index=True)
    symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)

    organization: Mapped[Organization] = relationship(
        "Organization",
        back_populates="token_discovery_history",
    )


Index("ix_token_discovery_history_org_created", TokenDiscoveryHistory.org_id, TokenDiscoveryHistory.created_at)


class CopilotScannerRun(Base):
    __tablename__ = "copilot_scanner_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    api_key_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("api_keys.id"),
        nullable=True,
        index=True,
    )
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    query: Mapped[str] = mapped_column(String(80), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed", index=True)
    thresholds_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paper_executed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_readiness_summary: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    organization: Mapped[Organization] = relationship(
        "Organization",
        back_populates="copilot_scanner_runs",
    )
    api_key: Mapped[ApiKey | None] = relationship("ApiKey")
    proposals: Mapped[list["CopilotProposal"]] = relationship(
        "CopilotProposal",
        back_populates="scanner_run",
    )


Index("ix_copilot_scanner_runs_org_created", CopilotScannerRun.org_id, CopilotScannerRun.created_at)


class CopilotProposal(Base):
    __tablename__ = "copilot_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    api_key_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("api_keys.id"),
        nullable=True,
        index=True,
    )
    scanner_run_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("copilot_scanner_runs.id"),
        nullable=True,
        index=True,
    )
    token_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False, default="BUY")
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="paper")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    execution_payload: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    organization: Mapped[Organization] = relationship(
        "Organization",
        back_populates="copilot_proposals",
    )
    api_key: Mapped[ApiKey | None] = relationship("ApiKey")
    scanner_run: Mapped[CopilotScannerRun | None] = relationship(
        "CopilotScannerRun",
        back_populates="proposals",
    )


class StrategyOutcome(Base):
    __tablename__ = "strategy_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "scanner_run_id",
            "candidate_address",
            name="uq_strategy_outcomes_org_run_candidate",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    profile_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    family: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    scanner_run_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("copilot_scanner_runs.id"),
        nullable=True,
        index=True,
    )
    paper_position_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("simulated_trades.id"),
        nullable=True,
        index=True,
    )
    candidate_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    decision: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    price_24h: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    price_7d: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    paper_pnl_brl: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    classification: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)

    organization: Mapped[Organization] = relationship(
        "Organization",
        back_populates="strategy_outcomes",
    )
    scanner_run: Mapped[CopilotScannerRun | None] = relationship("CopilotScannerRun")
    paper_position: Mapped[SimulatedTrade | None] = relationship("SimulatedTrade")


Index("ix_strategy_outcomes_org_created", StrategyOutcome.org_id, StrategyOutcome.created_at)
Index(
    "ix_strategy_outcomes_org_profile_created",
    StrategyOutcome.org_id,
    StrategyOutcome.profile_name,
    StrategyOutcome.created_at,
)


class NanopaymentReceipt(Base):
    __tablename__ = "nanopayment_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    resource_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    tx_hash: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
