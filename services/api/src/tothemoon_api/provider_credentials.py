from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db_models import OrgProviderCredential, utcnow_naive

GEMINI_PROVIDER = "gemini"
DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"
MASKED_GEMINI_KEY_DETAIL = "Cole a Gemini API key completa, nao o hint mascarado."
GEMINI_CREDENTIAL_REQUIRED_DETAIL = "Gemini credential is required"
INVALID_GEMINI_CREDENTIAL_DETAIL = "Cole uma credencial Gemini valida."


@dataclass(frozen=True)
class GeminiExecutionCredential:
    source: Literal["organization", "global", "vertexai", "unconfigured"]
    model: str
    api_key: str | None = field(default=None, repr=False)
    key_hint: str | None = None
    vertex_project: str | None = None
    vertex_location: str | None = None


def _fernet() -> Fernet:
    settings = get_settings()
    seed = settings.jwt_secret.encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    return Fernet(key)


def key_hint(api_key: str) -> str:
    value = api_key.strip()
    if len(value) <= 10:
        return f"{value[:2]}...{value[-2:]}"
    return f"{value[:6]}...{value[-4:]}"


def validate_gemini_api_key(api_key: str) -> str:
    value = api_key.strip()
    if not value:
        raise HTTPException(status_code=422, detail=GEMINI_CREDENTIAL_REQUIRED_DETAIL)
    if "..." in value or "\u2026" in value or "•••" in value:
        raise HTTPException(status_code=422, detail=MASKED_GEMINI_KEY_DETAIL)
    if len(value) < 10 or any(character.isspace() for character in value):
        raise HTTPException(status_code=422, detail=INVALID_GEMINI_CREDENTIAL_DETAIL)
    return value


def encrypt_provider_secret(api_key: str) -> str:
    value = validate_gemini_api_key(api_key)
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_provider_secret(credential: OrgProviderCredential) -> str:
    try:
        return _fernet().decrypt(credential.encrypted_api_key.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise HTTPException(status_code=409, detail="Stored provider credential cannot be decrypted") from exc


def provider_credential_summary(
    credential: OrgProviderCredential | None,
) -> dict[str, object]:
    if credential is None:
        return {"configured": False, "provider": GEMINI_PROVIDER}
    return {
        "configured": True,
        "provider": credential.provider,
        "key_hint": credential.key_hint,
        "model": credential.model,
        "created_at": credential.created_at,
        "updated_at": credential.updated_at,
    }


def get_provider_credential(
    db: Session,
    org_id: int,
    provider: str = GEMINI_PROVIDER,
) -> OrgProviderCredential | None:
    return (
        db.query(OrgProviderCredential)
        .filter(
            OrgProviderCredential.org_id == org_id,
            OrgProviderCredential.provider == provider,
        )
        .first()
    )


def get_org_gemini_api_key(db: Session, org_id: int) -> str | None:
    credential = get_provider_credential(db, org_id, GEMINI_PROVIDER)
    if credential is None:
        return None
    return decrypt_provider_secret(credential)


def resolve_gemini_execution_credential(
    db: Session,
    org_id: int,
    settings: Settings,
) -> GeminiExecutionCredential:
    credential = get_provider_credential(db, org_id, GEMINI_PROVIDER)
    if credential is not None:
        return GeminiExecutionCredential(
            source="organization",
            api_key=decrypt_provider_secret(credential),
            key_hint=credential.key_hint,
            model=(credential.model or settings.gemini_model).strip() or settings.gemini_model,
        )
    if settings.gemini_api_key:
        return GeminiExecutionCredential(
            source="global",
            api_key=settings.gemini_api_key,
            key_hint=key_hint(settings.gemini_api_key),
            model=settings.gemini_model,
        )
    if settings.gemini_use_vertexai:
        return GeminiExecutionCredential(
            source="vertexai",
            model=settings.gemini_model,
            vertex_project=settings.gemini_vertex_project,
            vertex_location=settings.gemini_vertex_location,
        )
    return GeminiExecutionCredential(source="unconfigured", model=settings.gemini_model)


def gemini_execution_credential_status(
    credential: GeminiExecutionCredential,
) -> dict[str, object]:
    return {
        "configured": credential.source != "unconfigured",
        "source": credential.source,
        "model": credential.model,
        "key_hint": credential.key_hint,
        "vertex_project_configured": bool(credential.vertex_project),
        "vertex_location": credential.vertex_location,
    }


def public_gemini_execution_credential_status(
    credential: GeminiExecutionCredential,
) -> dict[str, object]:
    status = gemini_execution_credential_status(credential)
    if status["source"] != "organization":
        status.pop("key_hint")
    if status["source"] != "vertexai":
        status.pop("vertex_project_configured")
        status.pop("vertex_location")
    return status


def upsert_gemini_credential(
    db: Session,
    org_id: int,
    api_key: str,
    model: str | None = None,
) -> OrgProviderCredential:
    now = utcnow_naive()
    api_key = validate_gemini_api_key(api_key)
    credential = get_provider_credential(db, org_id, GEMINI_PROVIDER)
    clean_model = (model or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    if credential is None:
        credential = OrgProviderCredential(
            org_id=org_id,
            provider=GEMINI_PROVIDER,
            encrypted_api_key=encrypt_provider_secret(api_key),
            key_hint=key_hint(api_key),
            model=clean_model,
            created_at=now,
            updated_at=now,
        )
        db.add(credential)
    else:
        credential.encrypted_api_key = encrypt_provider_secret(api_key)
        credential.key_hint = key_hint(api_key)
        credential.model = clean_model
        credential.updated_at = now
    return credential
