from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import current_user_from_jwt, require_api_key_scope, verify_api_key
from ..config import get_settings
from ..database import get_db
from ..db_models import NanopaymentReceipt, User
from ..tenancy import primary_org_for_user

router = APIRouter(prefix="/api/v1/nanopayments", tags=["Nanopayments"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
CurrentUser = Annotated[User, Depends(current_user_from_jwt)]
DbSession = Annotated[Session, Depends(get_db)]
JwtToken = Annotated[str | None, Depends(oauth2_scheme)]
ApiKeyValue = Annotated[str | None, Security(api_key_header)]

_RESOURCES: dict[str, dict[str, object]] = {}
_PAPER_SIGNATURE_PATTERN = re.compile(r"^(sig_test|sig_paper)_[A-Za-z0-9._:-]{3,120}$")


class ResourceCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    amount_usd: float = Field(gt=0)
    content: str = Field(min_length=1, max_length=4000)


def _nanopayment_org_id(
    *,
    required_scope: str,
    request: Request,
    db: Session,
    api_key_value: str | None,
    bearer_token: str | None,
) -> int:
    if api_key_value:
        api_key = verify_api_key(api_key_value, db, request)
        require_api_key_scope(api_key, required_scope)
        if api_key.org_id is None:
            raise HTTPException(status_code=403, detail="API key is not scoped to an organization")
        return api_key.org_id
    user = current_user_from_jwt(bearer_token, db, request)
    return primary_org_for_user(user, db).id


@router.post("/resources", status_code=201)
def create_resource(
    payload: ResourceCreateRequest,
    request: Request,
    db: DbSession,
    api_key_value: ApiKeyValue = None,
    bearer_token: JwtToken = None,
) -> dict[str, object]:
    org_id = _nanopayment_org_id(
        required_scope="payments:write",
        request=request,
        db=db,
        api_key_value=api_key_value,
        bearer_token=bearer_token,
    )
    resource = {
        "id": payload.id,
        "amount_usd": payload.amount_usd,
        "content": payload.content,
        "owner_org_id": org_id,
    }
    _RESOURCES[payload.id] = resource
    return {"resource": _public_resource(resource, include_content=True)}


def _public_resource(resource: dict[str, object], *, include_content: bool) -> dict[str, object]:
    public = {
        "id": resource["id"],
        "amount_usd": resource["amount_usd"],
    }
    if include_content:
        public["content"] = resource["content"]
    return public


def _verify_payment_signature(payment_signature: str) -> None:
    if not _PAPER_SIGNATURE_PATTERN.fullmatch(payment_signature.strip()):
        raise HTTPException(status_code=402, detail="Payment signature could not be verified")


def _paid_receipt_for_signature(
    db: Session,
    *,
    org_id: int,
    resource_id: str,
    payment_signature: str,
) -> NanopaymentReceipt | None:
    return (
        db.query(NanopaymentReceipt)
        .filter(
            NanopaymentReceipt.org_id == org_id,
            NanopaymentReceipt.resource_id == resource_id,
            NanopaymentReceipt.tx_hash == payment_signature,
            NanopaymentReceipt.status == "paid",
        )
        .order_by(NanopaymentReceipt.id.asc())
        .first()
    )


def _receipt_response(receipt: NanopaymentReceipt) -> dict[str, object]:
    return {
        "id": receipt.id,
        "resource_id": receipt.resource_id,
        "amount_usd": float(receipt.amount_usd),
        "status": receipt.status,
        "tx_hash": receipt.tx_hash,
        "created_at": receipt.created_at,
    }


@router.get("/receipts")
def list_receipts(
    request: Request,
    db: DbSession,
    api_key_value: ApiKeyValue = None,
    bearer_token: JwtToken = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, min_length=1, max_length=40),
    resource_id: str | None = Query(default=None, min_length=1, max_length=120),
) -> dict[str, object]:
    org_id = _nanopayment_org_id(
        required_scope="payments:read",
        request=request,
        db=db,
        api_key_value=api_key_value,
        bearer_token=bearer_token,
    )
    query = db.query(NanopaymentReceipt).filter(NanopaymentReceipt.org_id == org_id)
    if status is not None:
        query = query.filter(NanopaymentReceipt.status == status)
    if resource_id is not None:
        query = query.filter(NanopaymentReceipt.resource_id == resource_id)
    total_receipts = query.count()
    total_amount_usd = query.with_entities(
        func.coalesce(func.sum(NanopaymentReceipt.amount_usd), 0)
    ).scalar()
    rows = (
        query.order_by(NanopaymentReceipt.created_at.desc(), NanopaymentReceipt.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "org_id": org_id,
        "limit": limit,
        "offset": offset,
        "summary": {
            "total_receipts": int(total_receipts),
            "total_amount_usd": float(total_amount_usd or 0),
        },
        "receipts": [_receipt_response(row) for row in rows],
    }


@router.get("/resources/{resource_id}")
def get_resource(
    resource_id: str,
    request: Request,
    db: DbSession,
    payment_signature: Annotated[str | None, Header(alias="PAYMENT-SIGNATURE")] = None,
):
    resource = _RESOURCES.get(resource_id)
    if not resource:
        return JSONResponse(status_code=404, content={"detail": "Resource not found"})
    if not payment_signature:
        settings = get_settings()
        requirements = {
            "scheme": "x402",
            "network": "arc_testnet",
            "resource_id": resource_id,
            "amount_usd": resource["amount_usd"],
            "pay_to": settings.x402_payment_address,
            "method": request.method,
            "path": request.url.path,
        }
        return JSONResponse(
            status_code=402,
            content={"detail": "Payment required"},
            headers={"PAYMENT-REQUIREMENTS": json.dumps(requirements, separators=(",", ":"))},
        )
    _verify_payment_signature(payment_signature)
    org_id = int(str(resource["owner_org_id"]))
    existing_receipt = _paid_receipt_for_signature(
        db,
        org_id=org_id,
        resource_id=resource_id,
        payment_signature=payment_signature,
    )
    if existing_receipt:
        return {
            "resource": _public_resource(resource, include_content=True),
            "payment_signature": payment_signature,
            "receipt_id": existing_receipt.id,
        }
    receipt = NanopaymentReceipt(
        org_id=org_id,
        resource_id=resource_id,
        amount_usd=Decimal(str(resource["amount_usd"])),
        status="paid",
        tx_hash=payment_signature,
    )
    db.add(receipt)
    db.commit()
    return {
        "resource": _public_resource(resource, include_content=True),
        "payment_signature": payment_signature,
        "receipt_id": receipt.id,
    }
