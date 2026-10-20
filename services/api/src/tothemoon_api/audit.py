from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from .db_models import AuditLog

REDACTED_VALUE = "[REDACTED]"
SENSITIVE_AUDIT_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "apikeyheader",
        "authorization",
        "bearertoken",
        "geminiapikey",
        "keyhash",
        "password",
        "privatekey",
        "refreshtoken",
        "secret",
        "token",
        "xapikey",
    }
)


def _key_fingerprint(key: object) -> str:
    return "".join(character for character in str(key).lower() if character.isalnum())


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): REDACTED_VALUE
            if _key_fingerprint(key) in SENSITIVE_AUDIT_KEYS
            else _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def record_audit_log(
    db: Session,
    *,
    action: str,
    target_type: str,
    org_id: int | None = None,
    actor_id: int | None = None,
    actor_type: str = "user",
    target_id: str | int | None = None,
    request: Request | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        org_id=org_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        ip=_client_ip(request),
        ua=request.headers.get("User-Agent") if request is not None else None,
        before=_json_safe(before) if before is not None else None,
        after=_json_safe(after) if after is not None else None,
    )
    db.add(row)
    db.flush()
    return row


def audit_log_response(row: AuditLog) -> dict[str, object]:
    return {
        "id": row.id,
        "org_id": row.org_id,
        "actor_id": row.actor_id,
        "actor_type": row.actor_type,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "ip": row.ip,
        "ua": row.ua,
        "before": row.before,
        "after": row.after,
        "created_at": row.created_at,
    }
