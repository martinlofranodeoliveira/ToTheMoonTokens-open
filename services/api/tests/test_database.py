from datetime import date, datetime
from decimal import Decimal

from tothemoon_api.db_models import (
    ApiKey,
    AuditLog,
    Membership,
    NanopaymentReceipt,
    Organization,
    Plan,
    SimulatedTrade,
    Subscription,
    UsageRecord,
    User,
)


def test_create_user_and_api_key(db_session):
    # Test User creation
    test_user = User(email="synthetic-user-26@example.com", password_hash="hashed-password")
    db_session.add(test_user)
    db_session.commit()
    db_session.refresh(test_user)

    assert test_user.id is not None
    assert test_user.email == "synthetic-user-26@example.com"

    # Test ApiKey creation
    test_api_key = ApiKey(
        user_id=test_user.id,
        name="test",
        prefix="ttm_sk_live_",
        key_hash="hashed-api-key",
    )
    db_session.add(test_api_key)
    db_session.commit()
    db_session.refresh(test_api_key)

    assert test_api_key.id is not None
    assert test_api_key.name == "test"
    assert test_api_key.prefix == "ttm_sk_live_"
    assert test_api_key.user_id == test_user.id

    # Test relationship
    assert len(test_user.api_keys) == 1
    assert test_user.api_keys[0].key_hash == "hashed-api-key"
    assert test_api_key.owner.email == "synthetic-user-26@example.com"


def test_phase2_models_create_production_persistence_tables(db_session):
    plan = Plan(
        code="free",
        name="Free",
        monthly_request_limit=1000,
        monthly_token_audit_limit=100,
        active_api_key_limit=1,
    )
    user = User(email="synthetic-user-27@example.com", password_hash="hashed-password")
    db_session.add_all([plan, user])
    db_session.commit()
    db_session.refresh(plan)
    db_session.refresh(user)

    organization = Organization(name="Owner Org", plan_id=plan.id)
    db_session.add(organization)
    db_session.commit()
    db_session.refresh(organization)

    api_key = ApiKey(
        user_id=user.id,
        name="phase2",
        prefix="ttm_sk_live_",
        key_hash="hashed-phase2-key",
    )
    db_session.add_all(
        [
            Membership(user_id=user.id, org_id=organization.id, role="owner"),
            Subscription(
                org_id=organization.id,
                status="active",
                current_period_end=datetime(2026, 5, 30),
            ),
            UsageRecord(
                org_id=organization.id,
                day=date(2026, 4, 30),
                requests=1,
                simulated_volume_usd=Decimal("100.00"),
            ),
            api_key,
        ]
    )
    db_session.commit()
    db_session.refresh(api_key)

    db_session.add_all(
        [
            SimulatedTrade(
                org_id=organization.id,
                api_key_id=api_key.id,
                token_address="0xSAFE",
                side="BUY",
                amount=Decimal("100.00"),
                entry_price=Decimal("1.01000000"),
                fees_total=Decimal("2.00000000"),
                slippage_bps=100.0,
                status="open",
            ),
            AuditLog(
                actor_id=user.id,
                actor_type="user",
                action="api_key.create",
                target_type="api_key",
                target_id=str(api_key.id),
                ip="127.0.0.1",
                ua="pytest",
            ),
            NanopaymentReceipt(
                org_id=organization.id,
                resource_id="artifact_delivery_packet",
                amount_usd=Decimal("0.001"),
                status="verified",
                tx_hash="0xabc",
            ),
        ]
    )
    db_session.commit()

    assert db_session.query(Organization).count() == 1
    assert db_session.query(Membership).filter_by(role="owner").count() == 1
    assert db_session.query(SimulatedTrade).filter_by(token_address="0xSAFE").count() == 1
    assert db_session.query(AuditLog).filter_by(action="api_key.create").count() == 1
    assert db_session.query(NanopaymentReceipt).filter_by(status="verified").count() == 1
