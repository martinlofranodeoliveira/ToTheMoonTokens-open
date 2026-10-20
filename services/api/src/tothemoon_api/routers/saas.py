from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..audit import audit_log_response, record_audit_log
from ..auth import (
    TENANT_ADMIN_ROLES,
    create_jwt_for_user,
    current_user_from_jwt,
    generate_api_key,
    hash_password,
    normalize_api_key_scopes,
    require_membership_role,
    verify_password,
)
from ..database import get_db
from ..db_models import (
    ApiKey,
    AuditLog,
    CopilotProposal,
    Membership,
    NanopaymentReceipt,
    OrgProviderCredential,
    SimulatedTrade,
    Subscription,
    TokenDiscoveryHistory,
    UsageRecord,
    User,
    utcnow_naive,
)
from ..observability import enforce_rate_limit
from ..provider_credentials import (
    GEMINI_PROVIDER,
    get_provider_credential,
    provider_credential_summary,
    upsert_gemini_credential,
)
from ..quota import usage_summary
from ..simulation import position_response
from ..tenancy import create_default_org_for_user, primary_org_for_user, user_is_tenant_admin

router = APIRouter(prefix="/api/v1", tags=["SaaS"])
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user_from_jwt)]


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] | None = Field(default=None, min_length=1)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("API key name must not be blank")
        return cleaned


class TokenDiscoveryHistoryCreateRequest(BaseModel):
    token_address: str = Field(min_length=1, max_length=128)
    chain: str = Field(default="unknown", min_length=1, max_length=40)
    symbol: str | None = Field(default=None, max_length=40)
    score: float | None = Field(default=None, ge=0, le=100)
    recommendation: str | None = Field(default=None, max_length=40)
    rationale: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, object] | None = None

    @field_validator("token_address", "chain")
    @classmethod
    def require_non_blank_trimmed_value(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned

    @field_validator("symbol", "recommendation")
    @classmethod
    def normalize_optional_trimmed_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


_FORBIDDEN_METADATA_KEYS = {
    "apikey",
    "apikeyvalue",
    "geminiapikey",
    "geminiapikeyvalue",
    "circleapikey",
    "openaiapikey",
    "anthropicapikey",
    "privatekey",
    "privatekeypem",
    "secret",
    "clientsecret",
    "circleentitysecret",
    "password",
    "token",
    "accesstoken",
    "accesstokenhint",
    "authorization",
    "xapikey",
    "apikeyheader",
    "bearertoken",
    "refreshtoken",
}

_FORBIDDEN_METADATA_KEY_PARTS = {
    "accesstoken",
    "apikey",
    "bearertoken",
    "clientsecret",
    "privatekey",
    "refreshtoken",
}


def _metadata_key_fingerprint(key: object) -> str:
    return "".join(character for character in str(key).lower() if character.isalnum())


def _metadata_key_is_forbidden(key: object) -> bool:
    fingerprint = _metadata_key_fingerprint(key)
    return fingerprint in _FORBIDDEN_METADATA_KEYS or any(
        forbidden_part in fingerprint for forbidden_part in _FORBIDDEN_METADATA_KEY_PARTS
    )


def _sanitize_metadata(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_metadata(nested)
            for key, nested in value.items()
            if not _metadata_key_is_forbidden(key)
        }
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    return value


def _token_discovery_history_response(record: TokenDiscoveryHistory) -> dict[str, object]:
    return {
        "id": record.id,
        "org_id": record.org_id,
        "token_address": record.token_address,
        "chain": record.chain,
        "symbol": record.symbol,
        "score": record.score,
        "recommendation": record.recommendation,
        "rationale": record.rationale,
        "metadata": _sanitize_metadata(record.metadata_json or {}),
        "created_at": record.created_at,
    }


def _api_key_export_response(api_key: ApiKey) -> dict[str, object]:
    return {
        "id": api_key.id,
        "org_id": api_key.org_id,
        "name": api_key.name,
        "prefix": api_key.prefix,
        "scopes": api_key.scopes,
        "revoked_at": api_key.revoked_at,
        "last_used_at": api_key.last_used_at,
        "created_at": api_key.created_at,
    }


class GeminiCredentialRequest(BaseModel):
    api_key: str = Field(
        min_length=10,
        max_length=4096,
        validation_alias=AliasChoices("api_key", "gemini_api_key", "key"),
    )
    model: str = Field(default="gemini-3.1-pro-preview", min_length=1, max_length=120)

    @field_validator("model")
    @classmethod
    def model_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Gemini model must not be blank")
        return cleaned


def _rate_limit_auth(request: Request) -> Response | None:
    return enforce_rate_limit(request, limit=10, window_seconds=60)


def _account_provider_credentials(db: Session, org_id: int) -> dict[str, object]:
    return {
        GEMINI_PROVIDER: provider_credential_summary(
            get_provider_credential(db, org_id, GEMINI_PROVIDER)
        )
    }


def _export_value(value: object) -> object:
    if isinstance(value, dict | list):
        return _sanitize_metadata(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _export_row(row: object, fields: tuple[str, ...]) -> dict[str, object]:
    return {field: _export_value(getattr(row, field)) for field in fields}


def _provider_reference_hint(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "..." + value[-4:]
    return f"{value[:4]}...{value[-4:]}"


def _audit_log_export(row: AuditLog) -> dict[str, object]:
    return {
        **audit_log_response(row),
        "before": _sanitize_metadata(row.before or {}),
        "after": _sanitize_metadata(row.after or {}),
    }


def _require_tenant_admin(user: User, db: Session, org_id: int) -> None:
    if not user_is_tenant_admin(user, org_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin role required",
        )


def _record_provider_credential_rejected(
    db: Session,
    *,
    org_id: int,
    actor_id: int,
    action: str,
    request: Request,
    status_code: int,
    detail: object,
    reason: str,
) -> None:
    record_audit_log(
        db,
        org_id=org_id,
        actor_id=actor_id,
        action=action,
        target_type="provider_credential",
        target_id=GEMINI_PROVIDER,
        request=request,
        after={
            "provider": GEMINI_PROVIDER,
            "status_code": status_code,
            "detail": detail,
            "reason": reason,
        },
    )


@router.post("/auth/signup", response_model=None, status_code=status.HTTP_201_CREATED)
def signup(
    payload: SignupRequest,
    request: Request,
    db: DbSession,
) -> dict[str, object] | Response:
    limited = _rate_limit_auth(request)
    if limited:
        return limited

    email = str(payload.email).lower()
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(email=email, password_hash=hash_password(payload.password))
    db.add(user)
    db.flush()
    org = create_default_org_for_user(user, db)
    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="auth.signup",
        target_type="user",
        target_id=user.id,
        request=request,
        after={"email": user.email, "org_id": org.id, "plan": org.plan.code},
    )
    db.commit()
    db.refresh(user)
    return {
        "id": user.id,
        "email": user.email,
        "org_id": org.id,
        "plan": org.plan.code,
        "created_at": user.created_at,
    }


@router.post("/auth/login", response_model=None)
async def login(
    request: Request,
    db: DbSession,
) -> dict[str, str] | Response:
    limited = _rate_limit_auth(request)
    if limited:
        return limited

    form = parse_qs((await request.body()).decode())
    username = form.get("username", [""])[0].strip().lower()
    password = form.get("password", [""])[0]
    if not username or not password:
        raise HTTPException(status_code=400, detail="Missing username or password")

    user = db.query(User).filter(User.email == username).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    org = primary_org_for_user(user, db)
    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="auth.login",
        target_type="user",
        target_id=user.id,
        request=request,
        after={"email": user.email, "org_id": org.id},
    )
    db.commit()
    return {"access_token": create_jwt_for_user(user), "token_type": "bearer"}


@router.get("/saas/account")
def account(user: CurrentUser, db: DbSession) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    provider_credentials = _account_provider_credentials(db, org.id)
    db.commit()
    return {
        "email": user.email,
        "org_id": org.id,
        "organization": org.name,
        "plan": org.plan.code,
        "created_at": user.created_at,
        "real_mode_enabled": org.real_mode_enabled,
        "real_mode_approved_at": org.real_mode_approved_at,
        "real_mode_daily_limit_usd": _export_value(org.real_mode_daily_limit_usd),
        "bot_consecutive_failures": org.bot_consecutive_failures,
        "provider_credentials": provider_credentials,
    }


@router.get("/saas/account/export")
def export_account_data(
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    _require_tenant_admin(user, db, org.id)
    api_keys = (
        db.query(ApiKey)
        .filter(ApiKey.org_id == org.id)
        .order_by(ApiKey.created_at.asc(), ApiKey.id.asc())
        .all()
    )
    provider_credentials = (
        db.query(OrgProviderCredential)
        .filter(OrgProviderCredential.org_id == org.id)
        .order_by(OrgProviderCredential.created_at.asc(), OrgProviderCredential.id.asc())
        .all()
    )
    memberships = (
        db.query(Membership)
        .filter(Membership.org_id == org.id)
        .order_by(Membership.created_at.asc(), Membership.id.asc())
        .all()
    )
    usage_records = (
        db.query(UsageRecord)
        .filter(UsageRecord.org_id == org.id)
        .order_by(UsageRecord.day.asc(), UsageRecord.id.asc())
        .all()
    )
    simulated_trades = (
        db.query(SimulatedTrade)
        .filter(SimulatedTrade.org_id == org.id)
        .order_by(SimulatedTrade.created_at.asc(), SimulatedTrade.id.asc())
        .all()
    )
    token_history = (
        db.query(TokenDiscoveryHistory)
        .filter(TokenDiscoveryHistory.org_id == org.id)
        .order_by(TokenDiscoveryHistory.created_at.asc(), TokenDiscoveryHistory.id.asc())
        .all()
    )
    subscriptions = (
        db.query(Subscription)
        .filter(Subscription.org_id == org.id)
        .order_by(Subscription.created_at.asc(), Subscription.id.asc())
        .all()
    )
    nanopayment_receipts = (
        db.query(NanopaymentReceipt)
        .filter(NanopaymentReceipt.org_id == org.id)
        .order_by(NanopaymentReceipt.created_at.asc(), NanopaymentReceipt.id.asc())
        .all()
    )
    copilot_proposals = (
        db.query(CopilotProposal)
        .filter(CopilotProposal.org_id == org.id)
        .order_by(CopilotProposal.created_at.asc(), CopilotProposal.id.asc())
        .all()
    )
    audit_rows = (
        db.query(AuditLog)
        .filter(AuditLog.org_id == org.id)
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        .all()
    )

    export = {
        "schema_version": 1,
        "exported_at": utcnow_naive(),
        "account": {
            "id": user.id,
            "email": user.email,
            "is_active": user.is_active,
            "created_at": user.created_at,
        },
        "organization": {
            "id": org.id,
            "name": org.name,
            "plan": org.plan.code,
            "real_mode_enabled": org.real_mode_enabled,
            "real_mode_approved_at": org.real_mode_approved_at,
            "real_mode_daily_limit_usd": _export_value(org.real_mode_daily_limit_usd),
            "bot_consecutive_failures": org.bot_consecutive_failures,
            "created_at": org.created_at,
        },
        "memberships": [
            _export_row(row, ("id", "user_id", "org_id", "role", "created_at"))
            for row in memberships
        ],
        "api_keys": [
            _export_row(
                row,
                (
                    "id",
                    "user_id",
                    "org_id",
                    "name",
                    "prefix",
                    "scopes",
                    "revoked_at",
                    "last_used_at",
                    "created_at",
                ),
            )
            for row in api_keys
        ],
        "provider_credentials": [
            {
                **provider_credential_summary(row),
                "id": row.id,
                "org_id": row.org_id,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in provider_credentials
        ],
        "usage_records": [
            _export_row(
                row,
                ("id", "org_id", "day", "resource", "requests", "simulated_volume_usd"),
            )
            for row in usage_records
        ],
        "simulated_trades": [
            _export_row(
                row,
                (
                    "id",
                    "org_id",
                    "api_key_id",
                    "token_address",
                    "side",
                    "amount",
                    "entry_price",
                    "exit_price",
                    "fees_total",
                    "slippage_bps",
                    "realized_pnl_usd",
                    "status",
                    "created_at",
                    "closed_at",
                ),
            )
            for row in simulated_trades
        ],
        "token_discovery_history": [
            {**_token_discovery_history_response(row), "metadata": _sanitize_metadata(row.metadata_json or {})}
            for row in token_history
        ],
        "subscriptions": [
            {
                **_export_row(
                    row,
                    (
                        "id",
                        "org_id",
                        "status",
                        "provider",
                        "current_period_end",
                        "created_at",
                        "updated_at",
                    ),
                ),
                "provider_customer_id_hint": _provider_reference_hint(row.provider_customer_id),
                "provider_subscription_id_hint": _provider_reference_hint(
                    row.provider_subscription_id
                ),
            }
            for row in subscriptions
        ],
        "nanopayment_receipts": [
            _export_row(
                row,
                ("id", "org_id", "resource_id", "amount_usd", "status", "tx_hash", "created_at"),
            )
            for row in nanopayment_receipts
        ],
        "copilot_proposals": [
            {
                **_export_row(
                    row,
                    (
                        "id",
                        "org_id",
                        "api_key_id",
                        "token_address",
                        "chain",
                        "symbol",
                        "side",
                        "amount_usd",
                        "score",
                        "rationale",
                        "mode",
                        "status",
                        "execution_payload",
                        "created_at",
                        "updated_at",
                        "approved_at",
                    ),
                ),
                "execution_payload": _sanitize_metadata(row.execution_payload or {}),
            }
            for row in copilot_proposals
        ],
        "audit_logs": [_audit_log_export(row) for row in audit_rows],
    }
    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="account.data_exported",
        target_type="organization",
        target_id=org.id,
        request=request,
        after={
            "api_keys": len(api_keys),
            "provider_credentials": len(provider_credentials),
            "audit_logs": len(audit_rows),
        },
    )
    db.commit()
    return export


@router.get("/saas/dashboard")
def dashboard(user: CurrentUser, db: DbSession) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    db.commit()
    summary = usage_summary(org.id, db)
    trades = db.query(SimulatedTrade).filter(SimulatedTrade.org_id == org.id).all()
    open_trades = [trade for trade in trades if trade.status == "OPEN"]
    total_volume = sum((trade.amount for trade in trades), Decimal("0"))
    total_fees = sum((trade.fees_total for trade in trades), Decimal("0"))
    realized_pnl = sum(
        (trade.realized_pnl_usd for trade in trades if trade.realized_pnl_usd is not None),
        Decimal("0"),
    )
    unrealized_pnl = sum(
        (Decimal(str(position_response(trade).unrealized_pnl_usd)) for trade in open_trades),
        Decimal("0"),
    )
    chart_start = date.today() - timedelta(days=29)
    chart_points: list[dict[str, object]] = []
    for offset in range(30):
        chart_day = chart_start + timedelta(days=offset)
        day_trades = [trade for trade in trades if trade.created_at.date() == chart_day]
        day_volume = sum((trade.amount for trade in day_trades), Decimal("0"))
        chart_points.append(
            {
                "date": chart_day.isoformat(),
                "trades": len(day_trades),
                "volume_usd": round(float(day_volume), 6),
            }
        )
    return {
        "email": user.email,
        "org_id": org.id,
        "plan": org.plan.code,
        "requests_this_month": summary["requests_month"],
        "simulated_volume_usd": round(float(total_volume), 6),
        "total_simulated_trades": len(trades),
        "total_volume_usd": round(float(total_volume), 6),
        "total_fees_usd": round(float(total_fees), 6),
        "realized_pnl_usd": round(float(realized_pnl), 6),
        "unrealized_pnl_usd": round(float(unrealized_pnl), 6),
        "last_30_days_chart_points": chart_points,
        "active_api_keys": (
            db.query(func.count(ApiKey.id))
            .filter(ApiKey.org_id == org.id, ApiKey.revoked_at.is_(None))
            .scalar()
            or 0
        ),
    }


@router.get("/saas/usage")
def usage(user: CurrentUser, db: DbSession) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    db.commit()
    summary = usage_summary(org.id, db)
    return {
        **summary,
        "plan": org.plan.code,
        "quota": {
            "simulate_order": org.plan.limit_for("simulate_order"),
            "token_audit": org.plan.limit_for("token_audit"),
            "active_api_keys": org.plan.active_api_key_limit,
        },
    }


@router.get("/saas/export")
def export_saas_data(user: CurrentUser, db: DbSession) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    _require_tenant_admin(user, db, org.id)
    provider_credentials = _account_provider_credentials(db, org.id)
    api_keys = (
        db.query(ApiKey)
        .filter(ApiKey.org_id == org.id)
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        .all()
    )
    history = (
        db.query(TokenDiscoveryHistory)
        .filter(TokenDiscoveryHistory.org_id == org.id)
        .order_by(TokenDiscoveryHistory.created_at.desc(), TokenDiscoveryHistory.id.desc())
        .all()
    )
    audit_events = (
        db.query(AuditLog)
        .filter(AuditLog.org_id == org.id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .all()
    )
    summary = usage_summary(org.id, db)
    db.commit()
    return {
        "account": {
            "email": user.email,
            "org_id": org.id,
            "organization": org.name,
            "plan": org.plan.code,
            "created_at": user.created_at,
        },
        "usage": {
            **summary,
            "quota": {
                "simulate_order": org.plan.limit_for("simulate_order"),
                "token_audit": org.plan.limit_for("token_audit"),
                "active_api_keys": org.plan.active_api_key_limit,
            },
        },
        "api_keys": [_api_key_export_response(api_key) for api_key in api_keys],
        "provider_credentials": provider_credentials,
        "token_discovery_history": [_token_discovery_history_response(record) for record in history],
        "audit_events": [audit_log_response(row) for row in audit_events],
    }


@router.get("/saas/token-discovery-history")
def list_token_discovery_history(
    user: CurrentUser,
    db: DbSession,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    bounded_limit = min(max(limit, 1), 100)
    bounded_offset = max(offset, 0)
    scoped_query = db.query(TokenDiscoveryHistory).filter(TokenDiscoveryHistory.org_id == org.id)
    total = scoped_query.count()
    records = (
        scoped_query
        .order_by(TokenDiscoveryHistory.created_at.desc(), TokenDiscoveryHistory.id.desc())
        .offset(bounded_offset)
        .limit(bounded_limit)
        .all()
    )
    db.commit()
    return {
        "org_id": org.id,
        "history": [_token_discovery_history_response(record) for record in records],
        "limit": bounded_limit,
        "offset": bounded_offset,
        "total": total,
    }


@router.post("/saas/token-discovery-history", status_code=status.HTTP_201_CREATED)
def create_token_discovery_history(
    payload: TokenDiscoveryHistoryCreateRequest,
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    metadata = _sanitize_metadata(payload.metadata or {})
    record = TokenDiscoveryHistory(
        org_id=org.id,
        token_address=payload.token_address.strip(),
        chain=payload.chain.strip().lower(),
        symbol=payload.symbol.strip().upper() if payload.symbol else None,
        score=payload.score,
        recommendation=payload.recommendation.strip() if payload.recommendation else None,
        rationale=payload.rationale,
        metadata_json=metadata,
    )
    db.add(record)
    db.flush()
    response = _token_discovery_history_response(record)
    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="token_discovery_history.created",
        target_type="token_discovery_history",
        target_id=str(record.id),
        request=request,
        after=response,
    )
    db.commit()
    db.refresh(record)
    return {"org_id": org.id, "history": _token_discovery_history_response(record)}


@router.get("/saas/token-discovery-history/{history_id}")
def get_token_discovery_history(
    history_id: int,
    user: CurrentUser,
    db: DbSession,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    record = (
        db.query(TokenDiscoveryHistory)
        .filter(TokenDiscoveryHistory.id == history_id, TokenDiscoveryHistory.org_id == org.id)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Token discovery history record not found")
    db.commit()
    return {"org_id": org.id, "history": _token_discovery_history_response(record)}


@router.delete("/saas/token-discovery-history/{history_id}")
def delete_token_discovery_history(
    history_id: int,
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    _require_tenant_admin(user, db, org.id)
    record = (
        db.query(TokenDiscoveryHistory)
        .filter(TokenDiscoveryHistory.id == history_id, TokenDiscoveryHistory.org_id == org.id)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Token discovery history record not found")

    before = _token_discovery_history_response(record)
    db.delete(record)
    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="token_discovery_history.deleted",
        target_type="token_discovery_history",
        target_id=str(history_id),
        request=request,
        before=before,
    )
    db.commit()
    return {"org_id": org.id, "deleted": True, "id": history_id}


@router.get("/saas/api-keys")
def list_api_keys(user: CurrentUser, db: DbSession) -> list[dict[str, object]]:
    org = primary_org_for_user(user, db)
    require_membership_role(user, org.id, db, TENANT_ADMIN_ROLES)
    api_keys = (
        db.query(ApiKey)
        .filter(ApiKey.org_id == org.id)
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        .all()
    )
    db.commit()
    return [
        {
            "id": key.id,
            "org_id": key.org_id,
            "name": key.name,
            "prefix": key.prefix,
            "scopes": key.scopes,
            "last_used_at": key.last_used_at,
            "revoked_at": key.revoked_at,
            "created_at": key.created_at,
        }
        for key in api_keys
    ]


@router.post("/saas/api-keys", status_code=status.HTTP_201_CREATED)
def create_api_key(
    payload: ApiKeyCreateRequest,
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    _require_tenant_admin(user, db, org.id)
    try:
        scopes = normalize_api_key_scopes(payload.scopes)
    except HTTPException as exc:
        record_audit_log(
            db,
            org_id=org.id,
            actor_id=user.id,
            action="api_key.create_rejected",
            target_type="api_key",
            request=request,
            after={
                "name": payload.name,
                "scopes": payload.scopes,
                "status_code": exc.status_code,
                "detail": exc.detail,
            },
        )
        db.commit()
        raise
    active_key_limit = org.plan.active_api_key_limit
    active_keys = (
        db.query(func.count(ApiKey.id))
        .filter(ApiKey.org_id == org.id, ApiKey.revoked_at.is_(None))
        .scalar()
        or 0
    )
    if active_key_limit >= 0 and active_keys >= active_key_limit:
        raise HTTPException(status_code=409, detail="Active API key limit reached")
    plaintext, prefix, key_hash = generate_api_key()
    api_key = ApiKey(
        user_id=user.id,
        org_id=org.id,
        name=payload.name,
        prefix=prefix,
        key_hash=key_hash,
        scopes=scopes,
    )
    db.add(api_key)
    db.flush()
    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="api_key.created",
        target_type="api_key",
        target_id=api_key.id,
        request=request,
        before={"active_keys": active_keys},
        after={
            "id": api_key.id,
            "name": api_key.name,
            "prefix": api_key.prefix,
            "scopes": api_key.scopes,
        },
    )
    db.commit()
    db.refresh(api_key)
    return {
        "id": api_key.id,
        "org_id": api_key.org_id,
        "name": api_key.name,
        "prefix": api_key.prefix,
        "scopes": api_key.scopes,
        "plaintext": plaintext,
    }


@router.delete("/saas/api-keys/{api_key_id}")
def revoke_api_key(
    api_key_id: int,
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    require_membership_role(user, org.id, db, TENANT_ADMIN_ROLES)
    api_key = db.query(ApiKey).filter(ApiKey.id == api_key_id, ApiKey.org_id == org.id).first()
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    before = {"revoked_at": api_key.revoked_at, "prefix": api_key.prefix}
    if api_key.revoked_at is None:
        api_key.revoked_at = utcnow_naive()
    record_audit_log(
        db,
        org_id=api_key.org_id,
        actor_id=user.id,
        action="api_key.revoked",
        target_type="api_key",
        target_id=api_key.id,
        request=request,
        before=before,
        after={"revoked_at": api_key.revoked_at, "prefix": api_key.prefix},
    )
    db.commit()
    db.refresh(api_key)
    return {"id": api_key.id, "revoked_at": api_key.revoked_at}


@router.get("/saas/provider-credentials")
def list_provider_credentials(user: CurrentUser, db: DbSession) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    _require_tenant_admin(user, db, org.id)
    provider_credentials = _account_provider_credentials(db, org.id)
    db.commit()
    return {"org_id": org.id, "provider_credentials": provider_credentials}


@router.post("/account/settings")
@router.post("/settings")
@router.post("/providers/gemini/credential")
@router.post("/credentials/gemini")
@router.post("/saas/account/settings")
@router.post("/saas/settings")
@router.post("/saas/credentials/gemini")
@router.post("/saas/provider-credentials/gemini")
def save_gemini_credential(
    payload: GeminiCredentialRequest,
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    if not user_is_tenant_admin(user, org.id, db):
        detail = "Tenant admin role required"
        _record_provider_credential_rejected(
            db,
            org_id=org.id,
            actor_id=user.id,
            action="provider_credential.upsert_rejected",
            request=request,
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            reason="tenant_admin_required",
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    existing = get_provider_credential(db, org.id, GEMINI_PROVIDER)
    before = provider_credential_summary(existing) if existing else None
    credential = upsert_gemini_credential(db, org.id, payload.api_key, payload.model)
    db.flush()
    summary = provider_credential_summary(credential)
    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="provider_credential.upserted",
        target_type="provider_credential",
        target_id=GEMINI_PROVIDER,
        request=request,
        before=before,
        after=summary,
    )
    db.commit()
    db.refresh(credential)
    return {
        "org_id": org.id,
        "credential": provider_credential_summary(credential),
    }


@router.delete("/providers/gemini/credential")
@router.delete("/credentials/gemini")
@router.delete("/saas/credentials/gemini")
@router.delete("/saas/provider-credentials/gemini")
def delete_gemini_credential(
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    if not user_is_tenant_admin(user, org.id, db):
        detail = "Tenant admin role required"
        _record_provider_credential_rejected(
            db,
            org_id=org.id,
            actor_id=user.id,
            action="provider_credential.delete_rejected",
            request=request,
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            reason="tenant_admin_required",
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    credential = get_provider_credential(db, org.id, GEMINI_PROVIDER)
    if credential is None:
        detail = "Gemini credential not configured"
        _record_provider_credential_rejected(
            db,
            org_id=org.id,
            actor_id=user.id,
            action="provider_credential.delete_rejected",
            request=request,
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
            reason="not_configured",
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    before = provider_credential_summary(credential)
    db.delete(credential)
    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="provider_credential.deleted",
        target_type="provider_credential",
        target_id=GEMINI_PROVIDER,
        request=request,
        before=before,
        after={"configured": False, "provider": GEMINI_PROVIDER},
    )
    db.commit()
    return {"org_id": org.id, "credential": {"configured": False, "provider": GEMINI_PROVIDER}}


@router.get("/saas/audit-log")
def audit_log(
    user: CurrentUser,
    db: DbSession,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    org = primary_org_for_user(user, db)
    _require_tenant_admin(user, db, org.id)
    bounded_limit = max(1, min(limit, 100))
    bounded_offset = max(0, offset)
    scoped_query = db.query(AuditLog).filter(AuditLog.org_id == org.id)
    total = scoped_query.count()
    rows = (
        scoped_query
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset(bounded_offset)
        .limit(bounded_limit)
        .all()
    )
    db.commit()
    return {
        "org_id": org.id,
        "limit": bounded_limit,
        "offset": bounded_offset,
        "total": total,
        "events": [audit_log_response(row) for row in rows],
    }
