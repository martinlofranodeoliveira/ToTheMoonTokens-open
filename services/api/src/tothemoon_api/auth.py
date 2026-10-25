import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .db_models import ApiKey, Membership, User, utcnow_naive
from .observability import RATE_LIMIT_REJECTIONS_TOTAL, bind_trace_context, get_logger, rate_limiter

ph = PasswordHasher()
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
DbSession = Annotated[Session, Depends(get_db)]
JwtToken = Annotated[str | None, Depends(oauth2_scheme)]
ApiKeyValue = Annotated[str | None, Security(api_key_header)]

MembershipRole = str
TENANT_ADMIN_ROLES: frozenset[MembershipRole] = frozenset({"owner", "admin"})
API_KEY_SCOPES = frozenset(
    {
        "billing:read",
        "billing:write",
        "copilot:proposals",
        "payments:read",
        "payments:write",
        "tokens:audit",
        "simulate:order",
    }
)
DEFAULT_API_KEY_SCOPES = frozenset({"copilot:proposals", "tokens:audit", "simulate:order"})


def hash_password(plain: str) -> str:
    return ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        ph.verify(hashed, plain)
        return True
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def generate_api_key() -> tuple[str, str, str]:
    raw = "ttm_sk_live_" + secrets.token_urlsafe(32)
    return raw, raw[:16], ph.hash(raw)


def normalize_api_key_scopes(scopes: list[str] | set[str] | tuple[str, ...] | None) -> str:
    requested = DEFAULT_API_KEY_SCOPES if scopes is None else set(scopes)
    normalized = {scope.strip().lower() for scope in requested if scope and scope.strip()}
    invalid = sorted(normalized - API_KEY_SCOPES)
    if invalid:
        raise HTTPException(status_code=422, detail=f"Invalid API key scopes: {', '.join(invalid)}")
    if not normalized:
        raise HTTPException(status_code=422, detail="At least one API key scope is required")
    return " ".join(sorted(normalized))


def api_key_scope_set(api_key: ApiKey) -> set[str]:
    scopes = {scope.strip().lower() for scope in api_key.scopes.split() if scope.strip()}
    if not scopes or scopes == {"default"}:
        return set(DEFAULT_API_KEY_SCOPES)
    return scopes


def require_api_key_scope(api_key: ApiKey, scope: str) -> None:
    if scope not in api_key_scope_set(api_key):
        raise HTTPException(status_code=403, detail="API key scope denied")


def enforce_api_key_rate_limit(api_key: ApiKey, request: Request) -> None:
    settings = get_settings()
    limit = settings.rate_limit_api_key_per_minute
    window_seconds = 60.0
    key = f"org:{api_key.org_id}:key:{api_key.id}"
    scope = f"api-key:{request.url.path}"
    if rate_limiter.allow(key=key, scope=scope, limit=limit, window_seconds=window_seconds):
        return
    RATE_LIMIT_REJECTIONS_TOTAL.labels(path=request.url.path).inc()
    get_logger().warning(
        "api_key_rate_limit_rejected",
        org_id=api_key.org_id,
        api_key_id=api_key.id,
        path=request.url.path,
        limit=limit,
        window_seconds=window_seconds,
    )
    retry_after = max(1, int(window_seconds))
    raise HTTPException(
        status_code=429,
        detail={
            "error": "rate_limited",
            "message": "Too many API key requests. Slow down and try again shortly.",
            "retry_after_seconds": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


def membership_for_user(user: User, org_id: int, db: Session) -> Membership | None:
    return db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_id).first()


def require_membership_role(
    user: User,
    org_id: int,
    db: Session,
    allowed_roles: set[MembershipRole] | frozenset[MembershipRole],
) -> Membership:
    membership = membership_for_user(user, org_id, db)
    if membership is None:
        raise HTTPException(status_code=403, detail="Tenant access denied")
    if membership.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Tenant role denied")
    return membership


def _require_jwt_signing_secret(secret: str) -> str:
    if not isinstance(secret, str) or len(secret.strip()) < 32:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    return secret


def create_jwt_for_user(user: User) -> str:
    settings = get_settings()
    signing_secret = _require_jwt_signing_secret(settings.jwt_secret)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "exp": datetime.now(UTC) + timedelta(minutes=settings.jwt_expires_minutes),
    }
    return jwt.encode(payload, signing_secret, algorithm=settings.jwt_algorithm)


def current_user_from_jwt(
    token: JwtToken,
    db: DbSession,
    request: Request,
) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    settings = get_settings()
    signing_secret = _require_jwt_signing_secret(settings.jwt_secret)
    try:
        data = jwt.decode(token, signing_secret, algorithms=[settings.jwt_algorithm])
        user_id = int(data["sub"])
    except (JWTError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User inactive")
    membership = user.memberships[0] if user.memberships else None
    bind_trace_context(
        endpoint=request.url.path,
        user_id=user.id,
        org_id=membership.org_id if membership else None,
    )
    return user


def verify_api_key(
    api_key: ApiKeyValue,
    db: DbSession,
    request: Request,
) -> ApiKey:
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key")
    # Include random characters in the selector; the first 12 are only a marker.
    # Old rows remain usable and move out of the legacy bucket after verification.
    for prefix in dict.fromkeys((api_key[:16], api_key[:12])):
        candidates = db.query(ApiKey).filter(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None)).all()
        for candidate in candidates:
            try:
                ph.verify(candidate.key_hash, api_key)
            except (InvalidHashError, VerificationError, VerifyMismatchError):
                continue
            if candidate.org_id is None:
                continue
            owner_membership = (
                db.query(Membership)
                .filter(
                    Membership.user_id == candidate.user_id,
                    Membership.org_id == candidate.org_id,
                )
                .first()
            )
            if owner_membership is None or not candidate.owner.is_active:
                continue
            if candidate.prefix == api_key[:12]:
                candidate.prefix = api_key[:16]
            candidate.last_used_at = utcnow_naive()
            db.commit()
            bind_trace_context(
                endpoint=request.url.path,
                org_id=candidate.org_id,
                api_key_id=candidate.id,
            )
            enforce_api_key_rate_limit(candidate, request)
            return candidate
    raise HTTPException(status_code=403, detail="Invalid API Key")
