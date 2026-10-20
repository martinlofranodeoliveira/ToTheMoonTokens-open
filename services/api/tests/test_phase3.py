import hashlib
import hmac
import json
import time
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tothemoon_api.database import SessionLocal
from tothemoon_api.db_models import (
    ApiKey,
    BillingWebhookEvent,
    Membership,
    NanopaymentReceipt,
    Organization,
    Plan,
    Subscription,
    User,
)
from tothemoon_api.main import app
from tothemoon_api.quota import enforce_quota
from tothemoon_api.routers.billing import STRIPE_SIGNATURE_TOLERANCE_SECONDS

client = TestClient(app)
PASSWORD = "correct horse battery staple"
STRIPE_FUTURE_PERIOD_END = int(datetime(2099, 1, 1, tzinfo=UTC).timestamp())


def _signup_login_key(
    prefix: str = "phase3",
    *,
    scopes: list[str] | None = None,
) -> tuple[str, str, int]:
    email = f"{prefix}-{uuid4().hex}@example.com"
    signup = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert signup.status_code == 201
    org_id = int(signup.json()["org_id"])
    login = client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["access_token"]
    payload: dict[str, object] = {"name": "phase3"}
    if scopes is not None:
        payload["scopes"] = scopes
    created = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert created.status_code == 201
    return token, created.json()["plaintext"], org_id


def _stripe_signature(
    payload: bytes,
    secret: str = "whsec_dev_secret",
    *,
    timestamp: int | None = None,
) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    timestamp_value = str(timestamp)
    digest = hmac.new(
        secret.encode("utf-8"),
        timestamp_value.encode("utf-8") + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp_value},v1={digest}"


def test_quota_blocks_when_exceeded():
    _token, api_key, org_id = _signup_login_key("quota")
    with SessionLocal() as db:
        tiny = Plan(
            code=f"tiny-{uuid4().hex}",
            name="Tiny",
            monthly_request_limit=1,
            monthly_token_audit_limit=1,
            active_api_key_limit=1,
        )
        db.add(tiny)
        db.flush()
        org = db.query(Organization).filter(Organization.id == org_id).one()
        org.plan_id = tiny.id
        db.commit()

    first = client.post(
        "/api/v1/simulate/order",
        headers={"X-API-Key": api_key},
        json={"token_address": "0xSAFE", "amount": 100, "side": "BUY"},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/v1/simulate/order",
        headers={"X-API-Key": api_key},
        json={"token_address": "0xSAFE", "amount": 100, "side": "BUY"},
    )
    assert second.status_code == 429
    assert second.json()["detail"] == "Plan quota exceeded"


def test_copilot_scan_route_contract_is_registered_and_auth_gated():
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]

    assert "/api/v1/copilot/scan" in paths
    assert "post" in paths["/api/v1/copilot/scan"]

    response = client.post("/api/v1/copilot/scan", json={"query": "USDC"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


def test_quota_resets_per_month(db_session):
    plan = Plan(
        code="tiny",
        name="Tiny",
        monthly_request_limit=1,
        monthly_token_audit_limit=1,
        active_api_key_limit=1,
    )
    user = User(email="synthetic-user-33@example.com", password_hash="hashed")
    db_session.add_all([plan, user])
    db_session.flush()
    org = Organization(name="Quota Reset", plan_id=plan.id)
    db_session.add(org)
    db_session.flush()
    db_session.add(Membership(user_id=user.id, org_id=org.id, role="owner"))
    api_key = ApiKey(
        user_id=user.id,
        org_id=org.id,
        name="quota-reset",
        prefix="ttm_sk_live_",
        key_hash="hashed",
    )
    db_session.add(api_key)
    db_session.commit()
    db_session.refresh(api_key)

    enforce_quota(api_key, "simulate_order", db_session, today=date(2026, 4, 30))
    with pytest.raises(HTTPException):
        enforce_quota(api_key, "simulate_order", db_session, today=date(2026, 4, 30))
    enforce_quota(api_key, "simulate_order", db_session, today=date(2026, 5, 1))


def test_paid_plan_requires_active_subscription_entitlement(db_session):
    pro = Plan(
        code="pro",
        name="Pro",
        monthly_request_limit=10,
        monthly_token_audit_limit=10,
        active_api_key_limit=1,
    )
    user = User(email="synthetic-user-34@example.com", password_hash="hashed")
    db_session.add_all([pro, user])
    db_session.flush()
    org = Organization(name="Paid Entitlement", plan_id=pro.id)
    db_session.add(org)
    db_session.flush()
    api_key = ApiKey(
        user_id=user.id,
        org_id=org.id,
        name="paid-entitlement",
        prefix="ttm_sk_live_",
        key_hash="hashed",
    )
    db_session.add(api_key)
    db_session.commit()
    db_session.refresh(api_key)

    with pytest.raises(HTTPException) as missing_exc:
        enforce_quota(api_key, "simulate_order", db_session, today=date(2026, 5, 1))
    assert missing_exc.value.status_code == 402
    assert missing_exc.value.detail == "Active subscription required"

    subscription = Subscription(
        org_id=org.id,
        provider="stripe",
        status="past_due",
        provider_customer_id="cus_past_due_entitlement",
        provider_subscription_id="sub_past_due_entitlement",
    )
    db_session.add(subscription)
    db_session.commit()
    db_session.refresh(api_key)
    with pytest.raises(HTTPException) as past_due_exc:
        enforce_quota(api_key, "simulate_order", db_session, today=date(2026, 5, 1))
    assert past_due_exc.value.status_code == 402

    subscription.status = "active"
    db_session.commit()
    db_session.refresh(api_key)
    enforce_quota(api_key, "simulate_order", db_session, today=date(2026, 5, 1))

    subscription.status = "trialing"
    db_session.commit()
    db_session.refresh(api_key)
    enforce_quota(api_key, "token_audit", db_session, today=date(2026, 5, 1))


def test_stripe_webhook_promotes_plan():
    token, _api_key, org_id = _signup_login_key("stripe")
    payload = json.dumps(
        {
            "id": "evt_checkout_completed",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": str(org_id),
                    "customer": "cus_test_123",
                    "subscription": "sub_test_123",
                    "metadata": {"plan_code": "pro"},
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    response = client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": _stripe_signature(payload)},
    )
    assert response.status_code == 200
    assert response.json()["plan"] == "pro"

    duplicate = client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": _stripe_signature(payload)},
    )
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "received": True,
        "duplicate": True,
        "event_id": "evt_checkout_completed",
    }

    account = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200
    assert account.json()["plan"] == "pro"

    with SessionLocal() as db:
        subscription = db.query(Subscription).filter(Subscription.org_id == org_id).one()
        assert subscription.status == "active"
        assert subscription.provider == "stripe"
        assert subscription.provider_customer_id == "cus_test_123"
        assert subscription.provider_subscription_id == "sub_test_123"
        assert (
            db.query(BillingWebhookEvent).filter_by(event_id="evt_checkout_completed").count() == 1
        )


def test_billing_status_requires_auth_and_returns_scoped_subscription():
    token, _api_key, org_id = _signup_login_key("billing-status")
    unauthenticated = client.get("/api/v1/billing/status")
    assert unauthenticated.status_code == 401

    with SessionLocal() as db:
        subscription = Subscription(
            org_id=org_id,
            provider="stripe",
            provider_subscription_id="sub_status_123",
            provider_customer_id="cus_status_123",
            status="active",
            current_period_end=datetime(2099, 1, 1),
        )
        db.add(subscription)
        db.commit()

    response = client.get(
        "/api/v1/billing/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "org_id": org_id,
        "plan": "free",
        "provider": "stripe",
        "subscription_status": "active",
        "current_period_end": "2099-01-01T00:00:00",
    }


def test_saas_invoices_require_auth_and_return_scoped_subscription_summaries():
    assert client.get("/api/v1/saas/invoices").status_code == 401

    owner_token, _api_key, owner_org_id = _signup_login_key("invoices-owner")
    other_token, _other_api_key, other_org_id = _signup_login_key("invoices-other")
    with SessionLocal() as db:
        owner_subscription = Subscription(
            org_id=owner_org_id,
            provider="stripe",
            status="active",
            provider_customer_id="cus_invoices_owner",
            provider_subscription_id="sub_invoices_owner",
            current_period_end=datetime(2099, 1, 1),
        )
        other_subscription = Subscription(
            org_id=other_org_id,
            provider="stripe",
            status="past_due",
            provider_customer_id="cus_invoices_other",
            provider_subscription_id="sub_invoices_other",
            current_period_end=datetime(2099, 2, 1),
        )
        db.add_all([owner_subscription, other_subscription])
        db.commit()
        owner_invoice_id = f"subscription-{owner_subscription.id}"

    response = client.get(
        "/api/v1/saas/invoices",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["org_id"] == owner_org_id
    assert len(payload["invoices"]) == 1
    invoice = payload["invoices"][0]
    assert invoice["id"] == owner_invoice_id
    assert invoice["provider"] == "stripe"
    assert invoice["status"] == "active"
    assert invoice["plan"] == "free"
    assert invoice["provider_customer_id_hint"] == "cus_...wner"
    assert invoice["provider_subscription_id_hint"] == "sub_...wner"
    assert "provider_customer_id" not in invoice
    assert "provider_subscription_id" not in invoice
    assert invoice["current_period_end"] == "2099-01-01T00:00:00"
    assert "created_at" in invoice
    assert "updated_at" in invoice
    assert "cus_invoices_owner" not in response.text
    assert "sub_invoices_owner" not in response.text
    assert "cus_invoices_other" not in response.text
    assert "sub_invoices_other" not in response.text

    other_response = client.get(
        "/api/v1/saas/invoices",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert other_response.status_code == 200
    assert other_response.json()["org_id"] == other_org_id
    assert [
        invoice["provider_customer_id_hint"] for invoice in other_response.json()["invoices"]
    ] == [
        "cus_...ther"
    ]
    assert "cus_invoices_other" not in other_response.text
    assert "sub_invoices_other" not in other_response.text


def test_stripe_webhook_rejects_missing_signature():
    payload = b'{"id":"evt_missing_sig","type":"checkout.session.completed"}'
    response = client.post("/api/v1/webhooks/stripe", content=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing Stripe-Signature"


def test_stripe_webhook_rejects_bad_signature():
    payload = b'{"type":"checkout.session.completed"}'
    response = client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": "t=123,v1=bad"},
    )
    assert response.status_code == 400


def test_stripe_webhook_rejects_stale_signature():
    payload = b'{"id":"evt_stale_sig","type":"checkout.session.completed"}'
    stale_timestamp = int(time.time()) - STRIPE_SIGNATURE_TOLERANCE_SECONDS - 1
    response = client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": _stripe_signature(payload, timestamp=stale_timestamp)},
    )
    assert response.status_code == 400


def test_stripe_subscription_lifecycle_reconciles_and_downgrades_on_cancel():
    token, _api_key, org_id = _signup_login_key("stripe-lifecycle")
    created_payload = json.dumps(
        {
            "id": "evt_sub_created",
            "type": "customer.subscription.created",
            "data": {
                "object": {
                    "id": "sub_lifecycle_123",
                    "customer": "cus_lifecycle_123",
                    "status": "active",
                    "current_period_end": STRIPE_FUTURE_PERIOD_END,
                    "metadata": {"org_id": str(org_id), "plan_code": "enterprise"},
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    created = client.post(
        "/api/v1/webhooks/stripe",
        content=created_payload,
        headers={"Stripe-Signature": _stripe_signature(created_payload)},
    )
    assert created.status_code == 200
    assert created.json()["plan"] == "enterprise"

    account = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200
    assert account.json()["plan"] == "enterprise"

    with SessionLocal() as db:
        subscription = db.query(Subscription).filter(Subscription.org_id == org_id).one()
        assert subscription.current_period_end == datetime(2099, 1, 1)

    canceled_payload = json.dumps(
        {
            "id": "evt_sub_deleted",
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "id": "sub_lifecycle_123",
                    "customer": "cus_lifecycle_123",
                    "status": "canceled",
                    "metadata": {"org_id": str(org_id), "plan_code": "enterprise"},
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    canceled = client.post(
        "/api/v1/webhooks/stripe",
        content=canceled_payload,
        headers={"Stripe-Signature": _stripe_signature(canceled_payload)},
    )
    assert canceled.status_code == 200
    assert canceled.json()["plan"] == "free"

    account = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200
    assert account.json()["plan"] == "free"

    with SessionLocal() as db:
        subscription = db.query(Subscription).filter(Subscription.org_id == org_id).one()
        assert subscription.status == "canceled"
        assert subscription.provider_customer_id == "cus_lifecycle_123"
        assert subscription.provider_subscription_id == "sub_lifecycle_123"


def test_stripe_invoice_payment_failed_marks_subscription_past_due():
    _token, _api_key, org_id = _signup_login_key("stripe-past-due")
    payload = json.dumps(
        {
            "id": "evt_checkout_past_due",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": str(org_id),
                    "customer": "cus_past_due_123",
                    "subscription": "sub_past_due_123",
                    "metadata": {"plan_code": "pro"},
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    checkout = client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": _stripe_signature(payload)},
    )
    assert checkout.status_code == 200

    failed_payload = json.dumps(
        {
            "id": "evt_invoice_failed",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "customer": "cus_past_due_123",
                    "subscription": "sub_past_due_123",
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    failed = client.post(
        "/api/v1/webhooks/stripe",
        content=failed_payload,
        headers={"Stripe-Signature": _stripe_signature(failed_payload)},
    )
    assert failed.status_code == 200

    with SessionLocal() as db:
        subscription = db.query(Subscription).filter(Subscription.org_id == org_id).one()
        assert subscription.status == "past_due"


def test_stripe_subscription_owner_mismatch_is_rejected():
    _token_a, _api_key_a, org_id_a = _signup_login_key("stripe-owner-a")
    _token_b, _api_key_b, org_id_b = _signup_login_key("stripe-owner-b")
    with SessionLocal() as db:
        db.add(
            Subscription(
                org_id=org_id_a,
                provider="stripe",
                status="active",
                provider_customer_id="cus_owner",
                provider_subscription_id="sub_owner",
            )
        )
        db.commit()

    payload = json.dumps(
        {
            "id": "evt_owner_mismatch",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_owner",
                    "customer": "cus_owner",
                    "status": "active",
                    "metadata": {"org_id": str(org_id_b), "plan_code": "pro"},
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    response = client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": _stripe_signature(payload)},
    )
    assert response.status_code == 409

    with SessionLocal() as db:
        event = db.query(BillingWebhookEvent).filter_by(event_id="evt_owner_mismatch").one()
        assert event.status == "failed"
        assert event.processed_at is not None


def test_failed_stripe_webhook_event_can_be_retried_after_reconciliation():
    _token, _api_key, org_id = _signup_login_key("stripe-retry-failed")
    payload = json.dumps(
        {
            "id": "evt_invoice_retry_failed",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "customer": "cus_retry_failed",
                    "subscription": "sub_retry_failed",
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    failed = client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": _stripe_signature(payload)},
    )
    assert failed.status_code == 409

    with SessionLocal() as db:
        event = db.query(BillingWebhookEvent).filter_by(event_id="evt_invoice_retry_failed").one()
        assert event.status == "failed"
        assert event.processed_at is not None
        db.add(
            Subscription(
                org_id=org_id,
                provider="stripe",
                status="active",
                provider_customer_id="cus_retry_failed",
                provider_subscription_id="sub_retry_failed",
            )
        )
        db.commit()

    retry = client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": _stripe_signature(payload)},
    )
    assert retry.status_code == 200

    with SessionLocal() as db:
        event = db.query(BillingWebhookEvent).filter_by(event_id="evt_invoice_retry_failed").one()
        assert event.status == "processed"
        assert event.org_id == org_id
        subscription = db.query(Subscription).filter_by(org_id=org_id).one()
        assert subscription.status == "past_due"


def test_x402_returns_payment_required_when_missing_signature():
    token, _api_key, org_id = _signup_login_key("x402")
    created = client.post(
        "/api/v1/nanopayments/resources",
        headers={"Authorization": f"Bearer {token}"},
        json={"id": "resource-phase3", "amount_usd": 0.01, "content": "paid payload"},
    )
    assert created.status_code == 201

    response = client.get("/api/v1/nanopayments/resources/resource-phase3")
    assert response.status_code == 402
    assert "PAYMENT-REQUIREMENTS" in response.headers
    requirements = json.loads(response.headers["PAYMENT-REQUIREMENTS"])
    assert requirements["scheme"] == "x402"
    assert requirements["resource_id"] == "resource-phase3"
    assert requirements["amount_usd"] == 0.01
    assert requirements["method"] == "GET"
    assert requirements["path"] == "/api/v1/nanopayments/resources/resource-phase3"

    paid = client.get(
        "/api/v1/nanopayments/resources/resource-phase3",
        headers={"PAYMENT-SIGNATURE": "sig_test_123"},
    )
    assert paid.status_code == 200
    assert paid.json()["payment_signature"] == "sig_test_123"
    assert paid.json()["receipt_id"]
    assert paid.json()["resource"]["content"] == "paid payload"

    with SessionLocal() as db:
        receipt = (
            db.query(NanopaymentReceipt)
            .filter(NanopaymentReceipt.id == paid.json()["receipt_id"])
            .one()
        )
        assert receipt.org_id == org_id
        assert receipt.resource_id == "resource-phase3"
        assert str(receipt.amount_usd) == "0.010000"
        assert receipt.status == "paid"
        assert receipt.tx_hash == "sig_test_123"


def test_x402_resource_creation_accepts_only_payment_write_api_key_scope():
    token, audit_key, _org_id = _signup_login_key(
        "x402-api-scope-denied",
        scopes=["tokens:audit"],
    )
    denied = client.post(
        "/api/v1/nanopayments/resources",
        headers={"X-API-Key": audit_key},
        json={"id": "resource-scope-denied", "amount_usd": 0.01, "content": "paid payload"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "API key scope denied"

    keys = client.get("/api/v1/saas/api-keys", headers={"Authorization": f"Bearer {token}"})
    assert keys.status_code == 200
    client.delete(
        f"/api/v1/saas/api-keys/{keys.json()[0]['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    scoped = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "x402-writer", "scopes": ["payments:write", "payments:read"]},
    )
    assert scoped.status_code == 201

    created = client.post(
        "/api/v1/nanopayments/resources",
        headers={"X-API-Key": scoped.json()["plaintext"]},
        json={"id": "resource-scope-allowed", "amount_usd": 0.01, "content": "paid payload"},
    )
    receipts = client.get(
        "/api/v1/nanopayments/receipts",
        headers={"X-API-Key": scoped.json()["plaintext"]},
    )

    assert created.status_code == 201
    assert created.json()["resource"]["id"] == "resource-scope-allowed"
    assert receipts.status_code == 200


def test_x402_resource_responses_do_not_expose_owner_metadata():
    token, _api_key, org_id = _signup_login_key("x402-owner-redaction")
    created = client.post(
        "/api/v1/nanopayments/resources",
        headers={"Authorization": f"Bearer {token}"},
        json={"id": "resource-owner-redaction", "amount_usd": 0.01, "content": "paid payload"},
    )
    assert created.status_code == 201
    assert "owner_email" not in json.dumps(created.json())
    assert str(org_id) not in json.dumps(created.json())

    unpaid = client.get("/api/v1/nanopayments/resources/resource-owner-redaction")
    assert unpaid.status_code == 402
    requirements = json.loads(unpaid.headers["PAYMENT-REQUIREMENTS"])
    assert requirements.get("owner_org_id") is None
    assert requirements.get("owner_email") is None

    paid = client.get(
        "/api/v1/nanopayments/resources/resource-owner-redaction",
        headers={"PAYMENT-SIGNATURE": "sig_test_456"},
    )
    assert paid.status_code == 200
    assert paid.json()["resource"] == {
        "id": "resource-owner-redaction",
        "amount_usd": 0.01,
        "content": "paid payload",
    }


def test_x402_rejects_unverified_signature_without_unlocking_or_recording_receipt():
    token, _api_key, org_id = _signup_login_key("x402-invalid-signature")
    created = client.post(
        "/api/v1/nanopayments/resources",
        headers={"Authorization": f"Bearer {token}"},
        json={"id": "resource-invalid-signature", "amount_usd": 0.01, "content": "paid payload"},
    )
    assert created.status_code == 201

    response = client.get(
        "/api/v1/nanopayments/resources/resource-invalid-signature",
        headers={"PAYMENT-SIGNATURE": "not-a-valid-signature"},
    )
    assert response.status_code == 402
    assert response.json() == {"detail": "Payment signature could not be verified"}
    assert "paid payload" not in response.text
    assert "not-a-valid-signature" not in response.text

    with SessionLocal() as db:
        receipts = (
            db.query(NanopaymentReceipt)
            .filter(
                NanopaymentReceipt.org_id == org_id,
                NanopaymentReceipt.resource_id == "resource-invalid-signature",
            )
            .all()
        )
        assert receipts == []


def test_x402_reuses_existing_receipt_for_same_paper_signature():
    token, _api_key, org_id = _signup_login_key("x402-replay")
    created = client.post(
        "/api/v1/nanopayments/resources",
        headers={"Authorization": f"Bearer {token}"},
        json={"id": "resource-replay", "amount_usd": 0.01, "content": "paid payload"},
    )
    assert created.status_code == 201

    first = client.get(
        "/api/v1/nanopayments/resources/resource-replay",
        headers={"PAYMENT-SIGNATURE": "sig_paper_replay_123"},
    )
    second = client.get(
        "/api/v1/nanopayments/resources/resource-replay",
        headers={"PAYMENT-SIGNATURE": "sig_paper_replay_123"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["receipt_id"] == first.json()["receipt_id"]

    with SessionLocal() as db:
        receipts = (
            db.query(NanopaymentReceipt)
            .filter(
                NanopaymentReceipt.org_id == org_id,
                NanopaymentReceipt.resource_id == "resource-replay",
                NanopaymentReceipt.tx_hash == "sig_paper_replay_123",
            )
            .all()
        )
        assert len(receipts) == 1


def test_x402_receipt_history_filters_by_tenant_status_and_resource():
    token_a, _api_key_a, org_id_a = _signup_login_key("x402-receipts-a")
    token_b, _api_key_b, org_id_b = _signup_login_key("x402-receipts-b")
    with SessionLocal() as db:
        db.add_all(
            [
                NanopaymentReceipt(
                    org_id=org_id_a,
                    resource_id="resource-history-shared",
                    amount_usd=0.01,
                    status="paid",
                    tx_hash="sig_history_a",
                ),
                NanopaymentReceipt(
                    org_id=org_id_a,
                    resource_id="resource-history-other",
                    amount_usd=0.02,
                    status="paid",
                    tx_hash="sig_history_other",
                ),
                NanopaymentReceipt(
                    org_id=org_id_b,
                    resource_id="resource-history-shared",
                    amount_usd=0.03,
                    status="paid",
                    tx_hash="sig_history_b",
                ),
            ]
        )
        db.commit()

    response_a = client.get(
        "/api/v1/nanopayments/receipts?status=paid&resource_id=resource-history-shared",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    response_b = client.get(
        "/api/v1/nanopayments/receipts?status=paid&resource_id=resource-history-shared",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert response_a.status_code == 200
    assert response_a.json()["org_id"] == org_id_a
    assert response_a.json()["summary"] == {"total_receipts": 1, "total_amount_usd": 0.01}
    assert [row["tx_hash"] for row in response_a.json()["receipts"]] == ["sig_history_a"]
    assert response_b.status_code == 200
    assert response_b.json()["org_id"] == org_id_b
    assert response_b.json()["summary"] == {"total_receipts": 1, "total_amount_usd": 0.03}
    assert [row["tx_hash"] for row in response_b.json()["receipts"]] == ["sig_history_b"]
