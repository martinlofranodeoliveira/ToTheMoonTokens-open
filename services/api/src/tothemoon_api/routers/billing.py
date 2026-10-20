from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import record_audit_log
from ..auth import current_user_from_jwt, require_api_key_scope, verify_api_key
from ..config import get_settings
from ..database import get_db
from ..db_models import ApiKey, BillingWebhookEvent, Organization, Subscription, User, utcnow_naive
from ..tenancy import plan_by_code, primary_org_for_user, user_is_tenant_admin

router = APIRouter(prefix="/api/v1", tags=["Billing"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user_from_jwt)]
JwtToken = Annotated[str | None, Depends(oauth2_scheme)]
ApiKeyValue = Annotated[str | None, Security(api_key_header)]


class CheckoutRequest(BaseModel):
    plan_code: str = Field("pro", pattern="^(pro|enterprise)$")


def _billing_actor(
    *,
    required_scope: str,
    request: Request,
    db: Session,
    api_key_value: str | None,
    bearer_token: str | None,
) -> tuple[User, int, ApiKey | None]:
    if api_key_value:
        api_key = verify_api_key(api_key_value, db, request)
        require_api_key_scope(api_key, required_scope)
        if api_key.org_id is None:
            raise HTTPException(status_code=403, detail="API key is not scoped to an organization")
        return api_key.owner, api_key.org_id, api_key

    user = current_user_from_jwt(bearer_token, db, request)
    org = primary_org_for_user(user, db)
    if not user_is_tenant_admin(user, org.id, db):
        raise HTTPException(status_code=403, detail="Tenant admin role required")
    return user, org.id, None


STRIPE_SUBSCRIPTION_STATUS_MAP = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "unpaid": "past_due",
    "canceled": "canceled",
    "incomplete_expired": "canceled",
    "paused": "paused",
}
TERMINAL_SUBSCRIPTION_STATUSES = {"canceled", "incomplete_expired"}
STRIPE_SIGNATURE_TOLERANCE_SECONDS = 300


def _utc_from_stripe_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(str(value)), tz=UTC).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def _extract_org_id(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def _event_object(event: dict[str, object]) -> dict[str, object]:
    data = event.get("data")
    if not isinstance(data, dict):
        return {}
    event_object = data.get("object")
    return event_object if isinstance(event_object, dict) else {}


def _metadata(event_object: dict[str, object]) -> dict[str, object]:
    metadata = event_object.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _subscription_status(provider_status: object) -> str:
    status = str(provider_status or "active")
    return STRIPE_SUBSCRIPTION_STATUS_MAP.get(status, status)


def _provider_reference_hint(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "..." + value[-4:]
    return f"{value[:4]}...{value[-4:]}"


def _find_subscription(
    db: Session,
    *,
    provider_subscription_id: str | None,
    provider_customer_id: str | None,
    org_id: int | None,
) -> Subscription | None:
    if provider_subscription_id:
        subscription = (
            db.query(Subscription)
            .filter(
                Subscription.provider == "stripe",
                Subscription.provider_subscription_id == provider_subscription_id,
            )
            .first()
        )
        if subscription:
            return subscription
    if org_id is not None:
        subscription = (
            db.query(Subscription)
            .filter(Subscription.provider == "stripe", Subscription.org_id == org_id)
            .first()
        )
        if subscription:
            return subscription
    if provider_customer_id:
        return (
            db.query(Subscription)
            .filter(
                Subscription.provider == "stripe",
                Subscription.provider_customer_id == provider_customer_id,
            )
            .first()
        )
    return None


def _ensure_subscription(
    db: Session,
    *,
    org: Organization,
    provider_subscription_id: str | None,
    provider_customer_id: str | None,
) -> Subscription:
    subscription = _find_subscription(
        db,
        provider_subscription_id=provider_subscription_id,
        provider_customer_id=provider_customer_id,
        org_id=org.id,
    )
    if subscription is None:
        subscription = Subscription(org_id=org.id, provider="stripe")
        db.add(subscription)
    subscription.provider = "stripe"
    if provider_subscription_id:
        subscription.provider_subscription_id = provider_subscription_id
    if provider_customer_id:
        subscription.provider_customer_id = provider_customer_id
    return subscription


def _record_event_claim(
    db: Session,
    *,
    event_id: object,
    event_type: object,
) -> tuple[BillingWebhookEvent, bool]:
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing Stripe event id")
    claim = BillingWebhookEvent(
        provider="stripe",
        event_id=str(event_id),
        event_type=str(event_type or "unknown"),
        status="processing",
    )
    db.add(claim)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(BillingWebhookEvent)
            .filter(BillingWebhookEvent.event_id == str(event_id))
            .one()
        )
        if existing.status == "failed":
            existing.status = "processing"
            existing.processed_at = None
            db.flush()
            return existing, True
        return existing, False
    return claim, True


def _mark_event_claim_failed(db: Session, *, event_id: str) -> None:
    db.rollback()
    claim = db.query(BillingWebhookEvent).filter(BillingWebhookEvent.event_id == event_id).one()
    claim.status = "failed"
    claim.processed_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()


def _revert_to_free_plan(db: Session, org: Organization) -> str:
    previous_plan = org.plan.code
    org.plan_id = plan_by_code(db, "free").id
    return previous_plan


def _stripe_webhook_secrets(secret: str) -> list[str]:
    return [value.strip() for value in secret.split(",") if value.strip()]


def _verify_stripe_signature(
    payload: bytes,
    signature_header: str | None,
    secret: str,
    *,
    now: int | None = None,
) -> None:
    if not signature_header:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature")
    secrets = _stripe_webhook_secrets(secret)
    if not secrets:
        raise HTTPException(status_code=503, detail="Stripe webhook secret is not configured")
    values: dict[str, list[str]] = {}
    for part in signature_header.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values.setdefault(key, []).append(value)
    timestamp = values.get("t", [""])[0]
    signatures = values.get("v1", [])
    if not timestamp or not signatures:
        raise HTTPException(status_code=400, detail="Invalid Stripe-Signature")
    try:
        signed_at = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe-Signature") from exc
    current_time = int(time.time()) if now is None else now
    if abs(current_time - signed_at) > STRIPE_SIGNATURE_TOLERANCE_SECONDS:
        raise HTTPException(status_code=400, detail="Invalid Stripe-Signature")
    expected_signatures = [
        hmac.new(
            secret_value.encode("utf-8"),
            timestamp.encode("utf-8") + b"." + payload,
            hashlib.sha256,
        ).hexdigest()
        for secret_value in secrets
    ]
    if not any(
        hmac.compare_digest(expected, signature)
        for expected in expected_signatures
        for signature in signatures
    ):
        raise HTTPException(status_code=400, detail="Invalid Stripe-Signature")


@router.post("/billing/checkout")
def create_checkout(
    payload: CheckoutRequest,
    db: DbSession,
    request: Request,
    api_key_value: ApiKeyValue = None,
    bearer_token: JwtToken = None,
) -> dict[str, object]:
    user, org_id, _api_key = _billing_actor(
        required_scope="billing:write",
        request=request,
        db=db,
        api_key_value=api_key_value,
        bearer_token=bearer_token,
    )
    org = db.query(Organization).filter(Organization.id == org_id).one()
    plan = plan_by_code(db, payload.plan_code)
    record_audit_log(
        db,
        org_id=org.id,
        actor_id=user.id,
        action="billing.checkout.created",
        target_type="organization",
        target_id=org.id,
        request=request,
        before={"plan": org.plan.code},
        after={"requested_plan": plan.code},
    )
    db.commit()
    settings = get_settings()
    return {
        "url": (
            f"{settings.stripe_checkout_base_url}?client_reference_id={org.id}&plan={plan.code}"
        ),
        "org_id": org.id,
        "plan": plan.code,
    }


@router.get("/billing/status")
def billing_status(
    request: Request,
    db: DbSession,
    api_key_value: ApiKeyValue = None,
    bearer_token: JwtToken = None,
) -> dict[str, object]:
    _user, org_id, _api_key = _billing_actor(
        required_scope="billing:read",
        request=request,
        db=db,
        api_key_value=api_key_value,
        bearer_token=bearer_token,
    )
    org = db.query(Organization).filter(Organization.id == org_id).one()
    subscription = (
        db.query(Subscription)
        .filter(Subscription.org_id == org.id)
        .order_by(Subscription.updated_at.desc(), Subscription.id.desc())
        .first()
    )
    db.commit()
    return {
        "org_id": org.id,
        "plan": org.plan.code,
        "provider": subscription.provider if subscription else None,
        "subscription_status": subscription.status if subscription else "none",
        "current_period_end": subscription.current_period_end if subscription else None,
    }


@router.get("/saas/invoices")
def list_invoices(
    request: Request,
    db: DbSession,
    api_key_value: ApiKeyValue = None,
    bearer_token: JwtToken = None,
) -> dict[str, object]:
    _user, org_id, _api_key = _billing_actor(
        required_scope="billing:read",
        request=request,
        db=db,
        api_key_value=api_key_value,
        bearer_token=bearer_token,
    )
    org = db.query(Organization).filter(Organization.id == org_id).one()
    subscriptions = (
        db.query(Subscription)
        .filter(Subscription.org_id == org.id)
        .order_by(Subscription.updated_at.desc(), Subscription.id.desc())
        .all()
    )
    db.commit()
    return {
        "org_id": org.id,
        "invoices": [
            {
                "id": f"subscription-{subscription.id}",
                "provider": subscription.provider,
                "status": subscription.status,
                "plan": org.plan.code,
                "provider_customer_id_hint": _provider_reference_hint(
                    subscription.provider_customer_id
                ),
                "provider_subscription_id_hint": _provider_reference_hint(
                    subscription.provider_subscription_id
                ),
                "current_period_end": subscription.current_period_end,
                "created_at": subscription.created_at,
                "updated_at": subscription.updated_at,
            }
            for subscription in subscriptions
        ],
    }


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    db: DbSession,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, object]:
    payload = await request.body()
    settings = get_settings()
    _verify_stripe_signature(payload, stripe_signature, settings.stripe_webhook_secret)
    try:
        event = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc

    event_type = str(event.get("type") or "")
    claim, should_process = _record_event_claim(
        db,
        event_id=event.get("id"),
        event_type=event_type,
    )
    if not should_process:
        return {"received": True, "duplicate": True, "event_id": claim.event_id}
    event_id = claim.event_id
    db.commit()

    event_object = _event_object(event)
    try:
        if event_type == "checkout.session.completed":
            result = _handle_checkout_session_completed(db, request, event_object)
        elif event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            result = _handle_customer_subscription_event(db, request, event_type, event_object)
        elif event_type == "invoice.payment_failed":
            result = _handle_invoice_payment_failed(db, request, event_object)
        else:
            result = {"received": True, "ignored": True}
    except HTTPException:
        _mark_event_claim_failed(db, event_id=event_id)
        raise

    claim.status = "processed"
    claim.org_id = _extract_org_id(result.get("org_id"))
    claim.processed_at = utcnow_naive()
    db.commit()
    return result


def _handle_checkout_session_completed(
    db: Session,
    request: Request,
    session: dict[str, object],
) -> dict[str, object]:
    metadata = _metadata(session)
    org_id = _extract_org_id(session.get("client_reference_id") or metadata.get("org_id"))
    plan_code = str(metadata.get("plan_code") or "pro")
    if org_id is None:
        raise HTTPException(status_code=400, detail="Missing organization reference")

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    previous_plan = org.plan.code
    plan = plan_by_code(db, plan_code)
    org.plan_id = plan.id

    provider_subscription_id = str(session.get("subscription") or "") or None
    provider_customer_id = str(session.get("customer") or "") or None
    subscription = _ensure_subscription(
        db,
        org=org,
        provider_subscription_id=provider_subscription_id,
        provider_customer_id=provider_customer_id,
    )
    previous_status = subscription.status
    subscription.status = "active"
    subscription.updated_at = utcnow_naive()

    record_audit_log(
        db,
        org_id=org.id,
        actor_type="system",
        action="billing.subscription.updated",
        target_type="organization",
        target_id=org.id,
        request=request,
        before={"plan": previous_plan, "subscription_status": previous_status},
        after={
            "plan": plan.code,
            "subscription_status": subscription.status,
            "provider_customer_id": subscription.provider_customer_id,
            "provider_subscription_id": subscription.provider_subscription_id,
        },
    )
    return {"received": True, "org_id": org.id, "plan": plan.code}


def _handle_customer_subscription_event(
    db: Session,
    request: Request,
    event_type: str,
    stripe_subscription: dict[str, object],
) -> dict[str, object]:
    metadata = _metadata(stripe_subscription)
    provider_subscription_id = str(stripe_subscription.get("id") or "") or None
    provider_customer_id = str(stripe_subscription.get("customer") or "") or None
    org_id = _extract_org_id(metadata.get("org_id"))
    subscription = _find_subscription(
        db,
        provider_subscription_id=provider_subscription_id,
        provider_customer_id=provider_customer_id,
        org_id=org_id,
    )
    if subscription is None:
        if org_id is None:
            raise HTTPException(status_code=409, detail="Unable to reconcile subscription owner")
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if org is None:
            raise HTTPException(status_code=404, detail="Organization not found")
        subscription = _ensure_subscription(
            db,
            org=org,
            provider_subscription_id=provider_subscription_id,
            provider_customer_id=provider_customer_id,
        )
    else:
        org = subscription.organization
        if org_id is not None and org.id != org_id:
            raise HTTPException(status_code=409, detail="Subscription owner mismatch")
        if provider_subscription_id:
            subscription.provider_subscription_id = provider_subscription_id
        if provider_customer_id:
            subscription.provider_customer_id = provider_customer_id

    previous_plan = org.plan.code
    previous_status = subscription.status
    provider_status = str(stripe_subscription.get("status") or "")
    subscription.status = (
        "canceled"
        if event_type == "customer.subscription.deleted"
        else _subscription_status(provider_status)
    )
    subscription.current_period_end = _utc_from_stripe_timestamp(
        stripe_subscription.get("current_period_end")
    )
    subscription.updated_at = utcnow_naive()

    current_plan = previous_plan
    if subscription.status in {"active", "trialing"}:
        current_plan = str(metadata.get("plan_code") or previous_plan or "pro")
        org.plan_id = plan_by_code(db, current_plan).id
    elif (
        provider_status in TERMINAL_SUBSCRIPTION_STATUSES
        or event_type == "customer.subscription.deleted"
    ):
        previous_plan = _revert_to_free_plan(db, org)
        current_plan = "free"

    record_audit_log(
        db,
        org_id=org.id,
        actor_type="system",
        action="billing.subscription.updated",
        target_type="organization",
        target_id=org.id,
        request=request,
        before={"plan": previous_plan, "subscription_status": previous_status},
        after={
            "plan": current_plan,
            "subscription_status": subscription.status,
            "provider_customer_id": subscription.provider_customer_id,
            "provider_subscription_id": subscription.provider_subscription_id,
        },
    )
    return {"received": True, "org_id": org.id, "plan": current_plan}


def _handle_invoice_payment_failed(
    db: Session,
    request: Request,
    invoice: dict[str, object],
) -> dict[str, object]:
    provider_subscription_id = str(invoice.get("subscription") or "") or None
    provider_customer_id = str(invoice.get("customer") or "") or None
    subscription = _find_subscription(
        db,
        provider_subscription_id=provider_subscription_id,
        provider_customer_id=provider_customer_id,
        org_id=None,
    )
    if subscription is None:
        raise HTTPException(status_code=409, detail="Unable to reconcile subscription owner")
    org = subscription.organization
    previous_status = subscription.status
    subscription.status = "past_due"
    subscription.updated_at = utcnow_naive()
    record_audit_log(
        db,
        org_id=org.id,
        actor_type="system",
        action="billing.subscription.payment_failed",
        target_type="organization",
        target_id=org.id,
        request=request,
        before={"plan": org.plan.code, "subscription_status": previous_status},
        after={
            "plan": org.plan.code,
            "subscription_status": subscription.status,
            "provider_customer_id": subscription.provider_customer_id,
            "provider_subscription_id": subscription.provider_subscription_id,
        },
    )
    return {"received": True, "org_id": org.id, "plan": org.plan.code}
