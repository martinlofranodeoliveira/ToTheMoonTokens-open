from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .db_models import ApiKey, Subscription, UsageRecord

PAID_PLAN_CODES = {"pro", "enterprise"}
ENTITLED_SUBSCRIPTION_STATUSES = {"active", "trialing"}


def has_active_paid_entitlement(org_id: int, db: Session) -> bool:
    return (
        db.query(Subscription.id)
        .filter(
            Subscription.org_id == org_id,
            Subscription.status.in_(ENTITLED_SUBSCRIPTION_STATUSES),
        )
        .first()
        is not None
    )


def enforce_subscription_entitlement(api_key: ApiKey, db: Session) -> None:
    if api_key.organization is None:
        raise HTTPException(status_code=403, detail="API key is not scoped to an organization")
    org = api_key.organization
    if org.plan.code in PAID_PLAN_CODES and not has_active_paid_entitlement(org.id, db):
        raise HTTPException(status_code=402, detail="Active subscription required")


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    if start.month == 12:
        return start, start.replace(year=start.year + 1, month=1)
    return start, start.replace(month=start.month + 1)


def enforce_quota(
    api_key: ApiKey,
    resource: str,
    db: Session,
    *,
    request_units: int = 1,
    simulated_volume_usd: Decimal = Decimal("0"),
    today: date | None = None,
) -> UsageRecord:
    if api_key.organization is None:
        raise HTTPException(status_code=403, detail="API key is not scoped to an organization")
    org = api_key.organization
    enforce_subscription_entitlement(api_key, db)
    plan = org.plan
    limit = plan.limit_for(resource)
    day = today or date.today()
    month_start, next_month = _month_bounds(day)
    used = (
        db.query(func.coalesce(func.sum(UsageRecord.requests), 0))
        .filter(
            UsageRecord.org_id == org.id,
            UsageRecord.resource == resource,
            UsageRecord.day >= month_start,
            UsageRecord.day < next_month,
        )
        .scalar()
    )
    used_int = int(used or 0)
    if limit >= 0 and used_int + request_units > limit:
        raise HTTPException(status_code=429, detail="Plan quota exceeded")

    record = (
        db.query(UsageRecord)
        .filter(
            UsageRecord.org_id == org.id,
            UsageRecord.resource == resource,
            UsageRecord.day == day,
        )
        .first()
    )
    if record is None:
        record = UsageRecord(
            org_id=org.id,
            day=day,
            resource=resource,
            requests=0,
            simulated_volume_usd=Decimal("0"),
        )
        db.add(record)
    record.requests += request_units
    record.simulated_volume_usd += simulated_volume_usd
    db.commit()
    db.refresh(record)
    return record


def usage_summary(org_id: int, db: Session, *, today: date | None = None) -> dict[str, int]:
    day = today or date.today()
    month_start, next_month = _month_bounds(day)
    today_requests = (
        db.query(func.coalesce(func.sum(UsageRecord.requests), 0))
        .filter(UsageRecord.org_id == org_id, UsageRecord.day == day)
        .scalar()
    )
    month_requests = (
        db.query(func.coalesce(func.sum(UsageRecord.requests), 0))
        .filter(
            UsageRecord.org_id == org_id,
            UsageRecord.day >= month_start,
            UsageRecord.day < next_month,
        )
        .scalar()
    )
    return {
        "requests_today": int(today_requests or 0),
        "requests_month": int(month_requests or 0),
    }
