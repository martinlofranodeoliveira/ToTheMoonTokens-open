from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .agent_chat import router as agent_router
from .arc import ping_arc_network
from .arc_adapter import (
    ArcJobProof,
    NexusTaskEvent,
    PaidWorkUnit,
    get_arc_jobs,
    submit_nexus_task_event,
    submit_paid_work_unit,
)
from .auth import current_user_from_jwt
from .backtesting import RISK_PROFILES, run_backtest, run_walk_forward
from .circle import circle_client
from .config import get_settings
from .database import SessionLocal, init_db
from .db_models import User
from .demo_agent import router as demo_router
from .external import get_external_adapter_contract
from .external.adapters import is_paper_only_adapter_contract
from .external.health import (
    build_operator_readiness_evidence,
    build_provider_readiness,
    get_provider_health,
    summarize_provider_health,
)
from .guards import connector_status, evaluate_guardrails, gemini_configured
from .hackathon_summary import router as hackathon_router
from .journal import (
    clear_journal,
    get_journal,
    get_performance_aggregate,
    get_performance_aggregates,
    get_recent_trades,
    record_trade,
)
from .market_data import (
    BinanceMarketData,
    ExchangeDegradationError,
    connector_state,
    get_depth,
    get_ticker,
)
from .models import (
    BacktestMetrics,
    BacktestRequest,
    Candle,
    DashboardResponse,
    Horizon,
    NewsCategory,
    NewsIngestRequest,
    OperatorReadinessEvidenceResponse,
    PaperTradeRecord,
    PerformanceAggregates,
    ProbabilityChecklist,
    ProviderReadinessResponse,
    ScalpValidationRequest,
    ValidationResult,
    WalkForwardRequest,
)
from .news import check_news_risk_filter, get_recent_news, ingest_news
from .nexus_jobs import router as jobs_router
from .nexus_jobs import task_event_belongs_to_user
from .observability import (
    PrometheusMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    configure_logging,
    configure_runtime_observability,
    enforce_rate_limit,
    get_logger,
    metrics_response,
)
from .payments import payment_intent_belongs_to_user, require_payment_intent_owner
from .payments import router as payments_router
from .reputation import router as reputation_router
from .routers.billing import router as billing_router
from .routers.copilot import router as copilot_router
from .routers.nanopayments import router as nanopayments_router
from .routers.saas import router as saas_router
from .routers.tokens import router as tokens_router
from .scalp import validate_scalp_setup
from .settlement import router as settlements_router
from .simulate import router as simulate_router
from .strategies import strategy_catalog

configure_logging()
log = get_logger(__name__)

settings = get_settings()
init_db()
CurrentUser = Annotated[User, Depends(current_user_from_jwt)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.circle_bootstrap_on_startup:
        circle_client.load_wallets()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    description=(
        "Hackathon artifact API for paid agent work, settlement verification, "
        "delivery evidence, and safe market context. Direct order submission "
        "is permanently blocked by policy."
    ),
)

configure_runtime_observability(app, settings)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(PrometheusMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
    max_age=600,
)

app.include_router(payments_router)
app.include_router(agent_router)
app.include_router(jobs_router)
if settings.enable_demo_job_routes:
    app.include_router(demo_router)
app.include_router(reputation_router)
app.include_router(settlements_router)
app.include_router(hackathon_router)
app.include_router(saas_router)
app.include_router(billing_router)
app.include_router(copilot_router)
app.include_router(nanopayments_router)
app.include_router(tokens_router)
app.include_router(simulate_router)

log.info(
    "api_startup",
    app_env=settings.app_env,
    runtime_mode=settings.runtime_mode,
    cors_origins=settings.cors_allowed_origins,
    exchange=settings.default_exchange,
)


_DASHBOARD_CACHE_LOCK = threading.Lock()
_DASHBOARD_METRICS_CACHE: BacktestMetrics | None = None
_DASHBOARD_SNAPSHOTS_CACHE: list[BacktestMetrics] | None = None


def _default_metrics():
    global _DASHBOARD_METRICS_CACHE
    with _DASHBOARD_CACHE_LOCK:
        if _DASHBOARD_METRICS_CACHE is not None:
            return _DASHBOARD_METRICS_CACHE
    request = BacktestRequest(
        strategy_id="mean_reversion",
        symbol=settings.default_symbol,
        timeframe=settings.default_timeframe,
        lookback_bars=240,
        fee_bps=settings.default_fee_bps,
        slippage_bps=settings.default_slippage_bps,
        dataset_id="dashboard_synthetic",
        risk_tier="medium",
        checklist=ProbabilityChecklist(
            trend_alignment=False,
            momentum_confirm=True,
            volume_expansion=False,
            key_level_rejection=True,
            no_upcoming_news=True,
        ),
    )
    result = run_backtest(request, settings.max_position_size_pct)
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_METRICS_CACHE = result
    return result


def _dashboard_research_snapshots():
    global _DASHBOARD_SNAPSHOTS_CACHE
    with _DASHBOARD_CACHE_LOCK:
        if _DASHBOARD_SNAPSHOTS_CACHE is not None:
            return _DASHBOARD_SNAPSHOTS_CACHE
    snapshots = []
    for descriptor in strategy_catalog():
        risk_profile = RISK_PROFILES[descriptor.risk_tier]
        request = BacktestRequest(
            strategy_id=descriptor.id,
            symbol=settings.default_symbol,
            timeframe=settings.default_timeframe,
            lookback_bars=240,
            fee_bps=settings.default_fee_bps,
            slippage_bps=settings.default_slippage_bps,
            dataset_id="dashboard_synthetic",
            risk_tier=descriptor.risk_tier,
            position_size_pct=risk_profile.max_position_size_pct,
            checklist=ProbabilityChecklist(
                trend_alignment=descriptor.id != "mean_reversion",
                momentum_confirm=True,
                volume_expansion=descriptor.id != "mean_reversion",
                key_level_rejection=True,
                no_upcoming_news=True,
            ),
        )
        snapshots.append(run_backtest(request, settings.max_position_size_pct))
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_SNAPSHOTS_CACHE = snapshots
    return snapshots


_MARKET_CONNECTOR_LOCK = threading.Lock()
_MARKET_CONNECTOR: BinanceMarketData | None = None


def _market_connector() -> BinanceMarketData:
    global _MARKET_CONNECTOR
    if _MARKET_CONNECTOR is not None:
        return _MARKET_CONNECTOR
    with _MARKET_CONNECTOR_LOCK:
        if _MARKET_CONNECTOR is None:
            _MARKET_CONNECTOR = BinanceMarketData(
                base_url=settings.binance_testnet_base_url,
                ws_url=settings.binance_user_data_stream_url,
            )
    return _MARKET_CONNECTOR


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "app": settings.app_name,
        "mode": settings.runtime_mode,
        "deployment": {
            "release_id": settings.deployment_release_id or None,
            "api_image_digest": settings.deployment_image_digest or None,
        },
        "artifact_scope": "paid_agent_artifacts",
        "settlement_network": "arc_testnet",
        "wallet_provider": "circle_developer_controlled_wallets",
        "settlement_auth_mode": settings.settlement_auth_mode,
        "autonomous_payments_enabled": settings.autonomous_payments_enabled,
        "wallet_set_id": settings.circle_wallet_set_id or None,
        "orderSubmissionEnabled": False,
        "wallets_loaded": circle_client.wallets_loaded,
        "agent_chat_ready": gemini_configured(settings),
        "agent_model": settings.gemini_model or None,
        "probe": "liveness",
    }


def _database_ready() -> bool:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        log.exception("database_readiness_check_failed")
        return False


@app.get("/ready")
def ready() -> dict[str, object]:
    external_adapter_contract_safe = False
    external_adapter_contract: dict[str, object] = {}
    try:
        external_adapter_contract = get_external_adapter_contract()
        external_adapter_contract_safe = is_paper_only_adapter_contract(external_adapter_contract)
    except Exception:
        log.exception("external_adapter_contract_check_failed")
    status = "internal-only" if connector_state.last_error is None else "degraded"
    provider_readiness = build_provider_readiness(contract=external_adapter_contract)
    checks: dict[str, bool] = {
        "settings_valid": True,
        "database": _database_ready(),
        "strategies_loaded": len(strategy_catalog()) > 0,
        "mainnet_permanently_blocked": not evaluate_guardrails(settings).can_submit_mainnet_orders,
        "external_adapters_paper_only": external_adapter_contract_safe,
    }
    details = {
        "latency_ms": connector_state.last_latency_ms,
        "reconnect_count": connector_state.reconnect_count,
        "last_error": connector_state.last_error,
        "market_connector": {
            "status": status,
            "latency_ms": connector_state.last_latency_ms,
            "last_error": connector_state.last_error,
            "sample_count": 0,
            "public_exposure": False,
        },
        "providers": get_provider_health(),
        "provider_readiness": provider_readiness,
        "provider_summary": summarize_provider_health(),
        "external_adapter_contract": external_adapter_contract,
        "observability": {
            "sentry": bool(settings.sentry_dsn),
            "otlp": bool(settings.otel_exporter_otlp_endpoint),
        },
    }
    ok = all(checks.values())
    if not ok:
        log.error("readiness_failed", checks=checks)
        raise HTTPException(status_code=503, detail={"ok": False, "checks": checks, **details})
    return {"ok": True, "checks": checks, "mode": settings.runtime_mode, **details}


@app.get("/api/providers/readiness", response_model=ProviderReadinessResponse)
def get_provider_readiness() -> dict[str, object]:
    return build_provider_readiness(contract=get_external_adapter_contract())


@app.get("/api/operator/readiness-evidence", response_model=OperatorReadinessEvidenceResponse)
def get_operator_readiness_evidence() -> dict[str, object]:
    provider_readiness = build_provider_readiness(contract=get_external_adapter_contract())
    return build_operator_readiness_evidence(
        provider_readiness=provider_readiness,
        runtime_mode=settings.runtime_mode,
    )


@app.get("/metrics")
def metrics():
    return metrics_response()


@app.get("/api/_test/raise", include_in_schema=False)
def raise_observability_test_error():
    if settings.app_env == "production" and not settings.observability_test_raise_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    raise RuntimeError("observability test exception")


@app.get("/api/strategies")
def list_strategies():
    return strategy_catalog()


@app.post("/api/backtests/run")
def create_backtest(request: BacktestRequest, http_request: Request):
    limited = enforce_rate_limit(
        http_request,
        limit=settings.rate_limit_backtest_per_minute,
        window_seconds=60,
    )
    if limited is not None:
        return limited
    return run_backtest(request, settings.max_position_size_pct)


@app.post("/api/backtests/walk-forward")
def create_walk_forward(request: WalkForwardRequest, http_request: Request):
    limited = enforce_rate_limit(
        http_request,
        limit=settings.rate_limit_backtest_per_minute,
        window_seconds=60,
    )
    if limited is not None:
        return limited
    return run_walk_forward(request, settings.max_position_size_pct)


@app.get("/api/dashboard", response_model=DashboardResponse)
def get_dashboard():
    performance = get_performance_aggregate()
    return DashboardResponse(
        app_name=settings.app_name,
        runtime_mode=settings.runtime_mode,
        strategies=strategy_catalog(),
        metrics=_default_metrics(),
        research_snapshots=_dashboard_research_snapshots(),
        guardrails=evaluate_guardrails(settings),
        connectors=connector_status(settings),
        runtime_status=None,
        recent_trades=get_recent_trades(limit=8),
        performance=performance,
        journal_performance=performance,
        arc_jobs=get_arc_jobs(limit=5),
    )


@app.get("/api/market/health")
def get_market_health() -> dict[str, object]:
    return _market_connector().ping()


@app.get("/api/network/arc/health")
def get_arc_network_health() -> dict[str, object]:
    return ping_arc_network()


@app.get("/api/market/klines", response_model=list[Candle])
def get_market_klines(
    symbol: str | None = None,
    interval: str | None = None,
    limit: int = 180,
):
    connector = _market_connector()
    return connector.fetch_klines(
        symbol or settings.default_symbol,
        interval or settings.default_timeframe,
        limit=max(1, min(limit, 1000)),
    )


@app.get("/api/market/stream-preview")
def get_market_stream_preview(
    stream_name: str = "btcusdt@trade",
    max_messages: int = 1,
) -> dict[str, object]:
    connector = _market_connector()
    messages = connector.listen_stream(
        stream_name=stream_name,
        max_messages=max(1, min(max_messages, 3)),
    )
    return {
        "ok": True,
        "stream": stream_name,
        "message_count": len(messages),
        "messages": messages,
        "status": connector.operational_status,
    }


@app.get("/api/journal/trades", response_model=list[PaperTradeRecord])
def list_journal_trades():
    return get_journal()


@app.post("/api/journal/trades", response_model=PaperTradeRecord)
def add_journal_trade(trade: PaperTradeRecord):
    return record_trade(trade)


@app.get("/api/journal/performance", response_model=PerformanceAggregates)
def get_journal_performance(
    strategy_id: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
):
    return get_performance_aggregates(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
    )


@app.post("/api/paper/journal", response_model=PaperTradeRecord)
def create_paper_journal_entry(record: PaperTradeRecord) -> PaperTradeRecord:
    return record_trade(record)


@app.get("/api/paper/journal", response_model=list[PaperTradeRecord])
def list_paper_journal(limit: int = 20) -> list[PaperTradeRecord]:
    return get_journal(limit=max(1, min(limit, 200)))


@app.delete("/api/paper/journal")
def delete_paper_journal() -> dict[str, object]:
    clear_journal()
    return {"ok": True, "message": "Paper trading journal cleared."}


@app.get("/api/paper/performance", response_model=PerformanceAggregates)
def get_paper_performance() -> PerformanceAggregates:
    return get_performance_aggregate()


@app.post("/api/journal/entries", response_model=PaperTradeRecord)
def create_journal_entry_alias(record: PaperTradeRecord) -> PaperTradeRecord:
    return create_paper_journal_entry(record)


@app.get("/api/journal/entries", response_model=list[PaperTradeRecord])
def list_journal_entries_alias(limit: int = 20) -> list[PaperTradeRecord]:
    return list_paper_journal(limit=limit)


@app.delete("/api/journal/entries")
def delete_journal_entries_alias() -> dict[str, object]:
    return delete_paper_journal()


@app.post("/api/news/ingest")
def api_ingest_news(request: NewsIngestRequest):
    item = ingest_news(request)
    if not item:
        return {"status": "ignored", "reason": "duplicate"}
    return {"status": "ingested", "item": item}


@app.get("/api/news")
def api_get_news(
    limit: int = Query(default=50, ge=1, le=200),
    horizon: Horizon | None = None,
    category: NewsCategory | None = None,
):
    return get_recent_news(limit=limit, horizon=horizon, category=category)


@app.get("/api/news/risk")
def api_news_risk(symbol: str = "BTCUSDT"):
    return check_news_risk_filter(symbol)


@app.post("/api/scalp/validate", response_model=ValidationResult)
def validate_scalp(request: ScalpValidationRequest):
    return validate_scalp_setup(request.setup, request.context)


@app.get("/api/market/ticker")
def api_get_ticker(symbol: str = "BTCUSDT"):
    try:
        return get_ticker(symbol)
    except ExchangeDegradationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/market/depth")
def api_get_depth(symbol: str = "BTCUSDT", limit: int = Query(default=20, ge=5, le=100)):
    try:
        return get_depth(symbol, limit=limit)
    except ExchangeDegradationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/arc/jobs", response_model=ArcJobProof, response_model_exclude={"owner_user_id"})
def create_arc_job(event: NexusTaskEvent, user: CurrentUser):
    if not task_event_belongs_to_user(event.task_id, user.id):
        raise HTTPException(status_code=404, detail="Job not found")
    if event.payment_id is not None:
        require_payment_intent_owner(event.payment_id, user.id)
    return submit_nexus_task_event(event, owner_user_id=user.id)


@app.post(
    "/api/arc/paid-work-units",
    response_model=ArcJobProof,
    response_model_exclude={"owner_user_id"},
)
def create_arc_paid_work_unit(work_unit: PaidWorkUnit, user: CurrentUser):
    require_payment_intent_owner(work_unit.payment_id, user.id)
    if not task_event_belongs_to_user(work_unit.work_unit_id, user.id):
        raise HTTPException(status_code=404, detail="Job not found")
    return submit_paid_work_unit(work_unit, owner_user_id=user.id)


@app.get(
    "/api/arc/jobs",
    response_model=list[ArcJobProof],
    response_model_exclude={"__all__": {"owner_user_id"}},
)
def list_arc_jobs(user: CurrentUser, limit: int = 20):
    return [
        job
        for job in get_arc_jobs(limit=max(limit * 5, 100))
        if job.owner_user_id == user.id or payment_intent_belongs_to_user(job.payment_id, user.id)
    ][:limit]
