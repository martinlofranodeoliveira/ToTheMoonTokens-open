from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .arc_adapter import PaidWorkUnit, submit_paid_work_unit
from .auth import current_user_from_jwt, require_api_key_scope, verify_api_key
from .circle import circle_client
from .config import get_settings
from .database import get_db
from .db_models import User
from .demo_agent import DemoJobRequest, advance_job_to_delivered, ensure_job_paid, request_job
from .models import (
    BillableArtifact,
    JobExecutionRequest,
    JobExecutionResponse,
    PaymentIntentRecord,
    PaymentIntentRequest,
    PaymentIntentResponse,
    PaymentPayRequest,
    PaymentPayResponse,
    PaymentVerificationRequest,
    PaymentVerificationResponse,
)
from .nexus_jobs import (
    create_or_update_paid_job,
    get_job,
    job_belongs_to_user,
    record_payment_unlocked,
    reserve_paid_work_for_review,
    unlock_delivery_after_review,
)
from .settlement import SettlementRequest, clear_seen_payment_intents, verify_settlement

router = APIRouter(prefix="/api/payments", tags=["payments"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
CurrentUser = Annotated[User, Depends(current_user_from_jwt)]
DbSession = Annotated[Session, Depends(get_db)]
JwtToken = Annotated[str | None, Depends(oauth2_scheme)]
ApiKeyValue = Annotated[str | None, Security(api_key_header)]


def _payment_actor(
    *,
    required_scope: str,
    request: Request,
    db: Session,
    api_key_value: str | None,
    bearer_token: str | None,
) -> User:
    if api_key_value:
        api_key = verify_api_key(api_key_value, db, request)
        require_api_key_scope(api_key, required_scope)
        return api_key.owner
    return current_user_from_jwt(bearer_token, db, request)


def _looks_like_evm_address(value: str | None) -> bool:
    if not value or not value.startswith("0x") or len(value) != 42:
        return False
    try:
        int(value[2:], 16)
        return True
    except ValueError:
        return False


def _to_native_usdc_units(amount: float) -> int:
    scaled = Decimal(str(amount)) * (Decimal(10) ** 18)
    return int(scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _format_usdc_amount(amount: float) -> str:
    normalized = Decimal(str(amount)).normalize()
    return format(normalized, "f")


def _payment_requirement(intent: PaymentIntentRecord) -> dict[str, object]:
    return {
        "asset": "USDC",
        "network": "arc_testnet",
        "amount": _format_usdc_amount(intent.amount_usd),
        "amount_native_units": str(_to_native_usdc_units(intent.amount_usd)),
        "pay_to": intent.deposit_address,
        "payment_id": intent.payment_id,
        "verification_endpoint": "/api/payments/verify",
    }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


ARTIFACT_CATALOG: dict[str, BillableArtifact] = {
    "artifact_delivery_packet": BillableArtifact(
        id="artifact_delivery_packet",
        name="Delivery Packet",
        description="Reviewed delivery packet with execution summary and unlock metadata.",
        price_usd=0.001,
        type="delivery_packet",
    ),
    "artifact_review_bundle": BillableArtifact(
        id="artifact_review_bundle",
        name="Review Bundle",
        description="Reviewer-approved evidence bundle with settlement and quality checkpoints.",
        price_usd=0.005,
        type="review_bundle",
    ),
    "artifact_market_intel_brief": BillableArtifact(
        id="artifact_market_intel_brief",
        name="Market Intelligence Brief",
        description="Premium machine-generated brief unlocked only after payment verification.",
        price_usd=0.01,
        type="market_intel_brief",
    ),
}

_PAYMENT_INTENTS: dict[str, PaymentIntentRecord] = {}
_PAYMENT_INTENT_OWNERS: dict[str, int] = {}


def clear_payment_intents() -> None:
    _PAYMENT_INTENTS.clear()
    _PAYMENT_INTENT_OWNERS.clear()
    clear_seen_payment_intents()


def list_payment_intents(owner_user_id: int | None = None) -> list[PaymentIntentRecord]:
    intents = list(_PAYMENT_INTENTS.values())
    if owner_user_id is not None:
        intents = [
            intent
            for intent in intents
            if _PAYMENT_INTENT_OWNERS.get(intent.payment_id) == owner_user_id
        ]
    return sorted(intents, key=lambda intent: intent.updated_at, reverse=True)


def get_payment_intent_record(payment_id: str) -> PaymentIntentRecord | None:
    return _PAYMENT_INTENTS.get(payment_id)


def payment_intent_belongs_to_user(payment_id: str | None, owner_user_id: int) -> bool:
    return bool(payment_id) and _PAYMENT_INTENT_OWNERS.get(str(payment_id)) == owner_user_id


def require_payment_intent_owner(payment_id: str, owner_user_id: int) -> PaymentIntentRecord:
    intent = _require_payment_intent(payment_id)
    if _PAYMENT_INTENT_OWNERS.get(payment_id) != owner_user_id:
        raise HTTPException(status_code=404, detail="Payment intent not found")
    return intent


def _require_payment_intent(payment_id: str) -> PaymentIntentRecord:
    intent = get_payment_intent_record(payment_id)
    if not intent:
        raise HTTPException(status_code=404, detail="Payment intent not found")
    return intent


def _require_owned_payment_intent(payment_id: str, user: User) -> PaymentIntentRecord:
    return require_payment_intent_owner(payment_id, user.id)


def _save_payment_intent(intent: PaymentIntentRecord) -> PaymentIntentRecord:
    _PAYMENT_INTENTS[intent.payment_id] = intent
    return intent


def _update_payment_intent(intent: PaymentIntentRecord, **changes: object) -> PaymentIntentRecord:
    payload = intent.model_dump(mode="json")
    payload.update(changes)
    payload["updated_at"] = str(changes.get("updated_at") or _now_iso())
    updated = PaymentIntentRecord(**payload)
    _PAYMENT_INTENTS[updated.payment_id] = updated
    return updated


def _treasury_address() -> str:
    circle_client.ensure_wallets_loaded()
    return circle_client.get_wallet_address("TREASURY") or "0xMockDepositAddressForTestnetOnly"


def create_checkout(
    artifact_id: str,
    buyer_address: str,
    *,
    job_id: str | None = None,
    owner_user_id: int | None = None,
) -> PaymentIntentRecord:
    if artifact_id not in ARTIFACT_CATALOG:
        raise HTTPException(status_code=404, detail="Artifact not found")

    artifact = ARTIFACT_CATALOG[artifact_id]
    now = _now_iso()
    payment_id = str(uuid.uuid4())

    nexus_job_id = job_id or f"nexus-{payment_id}"
    if (
        owner_user_id is not None
        and job_id
        and get_job(job_id) is not None
        and not job_belongs_to_user(job_id, owner_user_id)
    ):
        raise HTTPException(status_code=404, detail="Nexus job not found")

    intent = PaymentIntentRecord(
        payment_id=payment_id,
        artifact_id=artifact.id,
        artifact_name=artifact.name,
        artifact_type=artifact.type,
        amount_usd=artifact.price_usd,
        buyer_address=buyer_address,
        deposit_address=_treasury_address(),
        job_id=nexus_job_id,
        status="pending",
        settlement_status="PENDING",
        reason=None,
        tx_hash=None,
        circle_transaction_id=None,
        executed=False,
        download_url=None,
        execution_message=None,
        created_at=now,
        updated_at=now,
    )
    create_or_update_paid_job(
        job_id=nexus_job_id,
        payment_id=payment_id,
        artifact_id=artifact.id,
        artifact_type=artifact.type,
        amount_usdc=artifact.price_usd,
        buyer_address=buyer_address,
        owner_user_id=owner_user_id,
    )
    if owner_user_id is not None:
        _PAYMENT_INTENT_OWNERS[payment_id] = owner_user_id
    return _save_payment_intent(intent)


def create_demo_checkout(
    artifact_id: str,
    buyer_address: str,
    *,
    owner_user_id: int | None = None,
) -> PaymentIntentRecord:
    artifact = ARTIFACT_CATALOG.get(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    demo_job = request_job(DemoJobRequest(artifact_type=artifact.type))
    return create_checkout(
        artifact_id,
        buyer_address,
        job_id=demo_job.id,
        owner_user_id=owner_user_id,
    )


def _try_mark_demo_job_paid(job_id: str) -> None:
    try:
        ensure_job_paid(job_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise


def _try_advance_demo_job_to_delivered(job_id: str) -> None:
    try:
        advance_job_to_delivered(job_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise


def _record_arc_paid_work_unit(
    intent: PaymentIntentRecord, execution: JobExecutionResponse
) -> None:
    if not intent.job_id:
        return

    submit_paid_work_unit(
        PaidWorkUnit(
            work_unit_id=intent.job_id,
            payment_id=intent.payment_id,
            artifact_id=intent.artifact_id,
            agent_id="nexus_delivery_agent",
            amount_usdc=float(intent.amount_usd),
            status="completed",
            tx_hash=intent.tx_hash,
            evidence={
                "artifact_id": intent.artifact_id,
                "artifact_type": intent.artifact_type,
                "download_url": execution.download_url or "",
                "review": "approved",
            },
        ),
        owner_user_id=_PAYMENT_INTENT_OWNERS.get(intent.payment_id),
    )


def _mock_receipt(
    tx_hash: str,
    *,
    sender: str,
    receiver: str,
    native_amount: int,
) -> dict[str, object]:
    return {
        "status": "0x1",
        "from": sender,
        "to": receiver,
        "transactionHash": tx_hash,
        "value": hex(native_amount),
        "logs": [
            {
                "address": "0x0000000000000000000000000000000000000014",
                "topics": [
                    "0x0000000000000000000000000000000000000000000000000000000000000015",
                    f"0x{'0' * 24}{sender[2:]}",
                    f"0x{'0' * 24}{receiver[2:]}",
                ],
                "data": hex(native_amount),
            }
        ],
    }


def _mock_transaction(
    tx_hash: str,
    *,
    sender: str,
    receiver: str,
    native_amount: int,
) -> dict[str, object]:
    return {
        "hash": tx_hash,
        "from": sender,
        "to": receiver,
        "value": hex(native_amount),
    }


def verify_payment_intent(payment_id: str, tx_hash: str) -> PaymentVerificationResponse:
    intent = _require_payment_intent(payment_id)

    if intent.status == "verified" and intent.tx_hash == tx_hash:
        return PaymentVerificationResponse(
            payment_id=payment_id,
            status="verified",
            unlocked_artifact_id=intent.artifact_id,
            settlement_status=intent.settlement_status,
            reason=intent.reason,
        )

    settings = get_settings()
    work_proof = f"payment-intent:{payment_id}:{intent.artifact_id}"
    native_amount = _to_native_usdc_units(intent.amount_usd)
    buyer_address = intent.buyer_address
    deposit_address = intent.deposit_address
    mock_sender = (
        buyer_address
        if _looks_like_evm_address(buyer_address)
        else "0x0000000000000000000000000000000000000016"
    )

    receipt_fetcher = None
    transaction_fetcher = None
    if "mock" in tx_hash.lower():

        def receipt_fetcher(value: str) -> dict[str, object]:
            return _mock_receipt(
                value,
                sender=mock_sender,
                receiver=deposit_address,
                native_amount=native_amount,
            )

        def transaction_fetcher(value: str) -> dict[str, object]:
            return _mock_transaction(
                value,
                sender=mock_sender,
                receiver=deposit_address,
                native_amount=native_amount,
            )

    settlement_result = verify_settlement(
        SettlementRequest(
            payment_intent_id=payment_id,
            job_id=str(intent.job_id or intent.artifact_id),
            agent_id="consumer_01",
            amount=float(intent.amount_usd),
            tx_hash=tx_hash,
            expected_sender=buyer_address if _looks_like_evm_address(buyer_address) else None,
            expected_receiver=deposit_address,
            expected_token_address=settings.arc_native_usdc_token_address,
            work_proof=work_proof,
            timeout_s=settings.marketplace_settlement_timeout_s,
        ),
        receipt_fetcher=receipt_fetcher,
        transaction_fetcher=transaction_fetcher,
    )

    updated = _update_payment_intent(
        intent,
        status="verified" if settlement_result.status == "SETTLED" else "failed",
        settlement_status=settlement_result.status,
        reason=settlement_result.reason or None,
        tx_hash=tx_hash,
    )
    if updated.status == "verified" and updated.job_id:
        record_payment_unlocked(
            job_id=updated.job_id,
            payment_id=updated.payment_id,
            settlement_status=updated.settlement_status,
            tx_hash=updated.tx_hash,
        )

    return PaymentVerificationResponse(
        payment_id=payment_id,
        status=updated.status,
        unlocked_artifact_id=updated.artifact_id if updated.status == "verified" else None,
        settlement_status=updated.settlement_status,
        reason=updated.reason,
    )


def _resolve_programmatic_source_address(intent: PaymentIntentRecord) -> str:
    settings = get_settings()
    circle_client.ensure_wallets_loaded()

    candidate = intent.buyer_address.strip()
    if _looks_like_evm_address(candidate):
        return candidate

    if candidate:
        wallet_by_role = circle_client.get_wallet_address(candidate)
        if wallet_by_role:
            return wallet_by_role

    fallback = circle_client.get_wallet_address(settings.demo_buyer_wallet_role)
    if fallback:
        return fallback

    raise HTTPException(
        status_code=409,
        detail=(
            "Programmatic settlement requires a known Circle wallet address or "
            "a configured demo buyer wallet role."
        ),
    )


def pay_payment_intent(payment_id: str) -> PaymentPayResponse:
    settings = get_settings()
    if not settings.autonomous_payments_enabled:
        raise HTTPException(
            status_code=409,
            detail="Autonomous payments are disabled. Use the manual settlement flow.",
        )

    intent = _require_payment_intent(payment_id)
    if intent.status == "verified" and intent.tx_hash:
        if intent.job_id:
            record_payment_unlocked(
                job_id=intent.job_id,
                payment_id=intent.payment_id,
                settlement_status=intent.settlement_status,
                tx_hash=intent.tx_hash,
            )
        return PaymentPayResponse(
            payment_id=intent.payment_id,
            status=intent.status,
            settlement_status=intent.settlement_status,
            tx_hash=intent.tx_hash,
            circle_transaction_id=intent.circle_transaction_id,
            reason=intent.reason,
        )

    source_address = _resolve_programmatic_source_address(intent)
    circle_transaction_id = intent.circle_transaction_id

    try:
        transfer = circle_client.create_transfer(
            wallet_address=source_address,
            destination_address=intent.deposit_address,
            amount=_format_usdc_amount(intent.amount_usd),
            token_address=settings.arc_native_usdc_token_address,
        )

        circle_transaction_id = str(transfer.get("data", {}).get("id", "")).strip() or None
        if not circle_transaction_id:
            raise RuntimeError("Circle did not return a transaction id.")

        updated = _update_payment_intent(
            intent,
            circle_transaction_id=circle_transaction_id,
            settlement_status=str(transfer.get("data", {}).get("state", "PENDING")).upper(),
        )

        state = str(transfer.get("data", {}).get("state", "")).upper()
        tx_hash = str(transfer.get("data", {}).get("txHash", "")).strip()
        if state not in circle_client.TERMINAL_STATES or not tx_hash:
            final_transaction = circle_client.wait_for_transaction(circle_transaction_id)
            state = str(final_transaction.get("state", "")).upper()
            tx_hash = str(final_transaction.get("txHash", "")).strip() or tx_hash

        if state != "COMPLETE" or not tx_hash:
            failed = _update_payment_intent(
                updated,
                status="failed",
                settlement_status=state or "FAILED",
                tx_hash=tx_hash or None,
                reason=(
                    "Circle transfer did not complete successfully. "
                    f"Final state: {state or 'unknown'}"
                ),
            )
            return PaymentPayResponse(
                payment_id=failed.payment_id,
                status=failed.status,
                settlement_status=failed.settlement_status,
                tx_hash=failed.tx_hash,
                circle_transaction_id=failed.circle_transaction_id,
                reason=failed.reason,
            )

        verification = verify_payment_intent(payment_id, tx_hash)
        paid_intent = _require_payment_intent(payment_id)
        if verification.status == "verified" and paid_intent.job_id:
            _try_mark_demo_job_paid(paid_intent.job_id)
            record_payment_unlocked(
                job_id=paid_intent.job_id,
                payment_id=paid_intent.payment_id,
                settlement_status=paid_intent.settlement_status,
                tx_hash=paid_intent.tx_hash,
            )

        return PaymentPayResponse(
            payment_id=paid_intent.payment_id,
            status=paid_intent.status,
            settlement_status=paid_intent.settlement_status,
            tx_hash=paid_intent.tx_hash,
            circle_transaction_id=paid_intent.circle_transaction_id,
            reason=paid_intent.reason,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _update_payment_intent(
            intent,
            status="failed",
            settlement_status="FAILED",
            reason=str(exc),
            circle_transaction_id=circle_transaction_id,
        )
        raise HTTPException(status_code=502, detail=f"Programmatic payment failed: {exc}") from exc


def unlock_payment_intent(payment_id: str, artifact_id: str) -> JobExecutionResponse:
    intent = _require_payment_intent(payment_id)

    if intent.artifact_id != artifact_id:
        raise HTTPException(
            status_code=409,
            detail="Payment intent does not match requested artifact.",
        )

    if intent.status != "verified":
        raise HTTPException(status_code=402, detail="Payment required to unlock this job.")

    if intent.executed and intent.download_url:
        return JobExecutionResponse(
            artifact_id=artifact_id,
            status="completed",
            message=intent.execution_message or "Artifact already unlocked.",
            download_url=intent.download_url,
        )

    if intent.job_id and reserve_paid_work_for_review(intent.job_id) is None:
        raise HTTPException(status_code=409, detail="Review gate is not ready for delivery unlock.")

    execution = JobExecutionResponse(
        artifact_id=artifact_id,
        status="completed",
        message="Job executed successfully after payment verification and review.",
        download_url=f"/api/artifacts/{artifact_id}/download",
    )
    download_url = execution.download_url
    if not download_url:
        raise HTTPException(status_code=500, detail="Artifact download URL was not generated")
    if intent.job_id and unlock_delivery_after_review(intent.job_id, download_url) is None:
        raise HTTPException(status_code=409, detail="Review required before delivery unlock.")

    _record_arc_paid_work_unit(intent, execution)
    _update_payment_intent(
        intent,
        executed=True,
        download_url=execution.download_url,
        execution_message=execution.message,
    )
    return execution


def unlock_and_deliver_payment_intent(payment_id: str) -> PaymentIntentRecord:
    intent = _require_payment_intent(payment_id)
    unlock_payment_intent(payment_id, intent.artifact_id)
    if intent.job_id:
        _try_advance_demo_job_to_delivered(intent.job_id)
    return _require_payment_intent(payment_id)


@router.get("/catalog", response_model=list[BillableArtifact])
def get_catalog():
    return list(ARTIFACT_CATALOG.values())


@router.get("/orders", response_model=list[PaymentIntentRecord])
def get_orders(
    request: Request,
    db: DbSession,
    api_key_value: ApiKeyValue = None,
    bearer_token: JwtToken = None,
):
    user = _payment_actor(
        required_scope="payments:read",
        request=request,
        db=db,
        api_key_value=api_key_value,
        bearer_token=bearer_token,
    )
    return list_payment_intents(owner_user_id=user.id)


@router.post("/intent", response_model=PaymentIntentResponse)
def create_payment_intent(
    payload: PaymentIntentRequest,
    request: Request,
    db: DbSession,
    api_key_value: ApiKeyValue = None,
    bearer_token: JwtToken = None,
):
    user = _payment_actor(
        required_scope="payments:write",
        request=request,
        db=db,
        api_key_value=api_key_value,
        bearer_token=bearer_token,
    )
    intent = create_checkout(
        payload.artifact_id,
        payload.buyer_address,
        job_id=payload.job_id,
        owner_user_id=user.id,
    )
    return PaymentIntentResponse(
        payment_id=intent.payment_id,
        amount_usd=intent.amount_usd,
        currency="USDC",
        deposit_address=intent.deposit_address,
        payment_requirement=_payment_requirement(intent),
        status=intent.status,
        job_id=intent.job_id,
    )


@router.post("/pay", response_model=PaymentPayResponse)
def pay_payment(
    payload: PaymentPayRequest,
    request: Request,
    db: DbSession,
    api_key_value: ApiKeyValue = None,
    bearer_token: JwtToken = None,
):
    user = _payment_actor(
        required_scope="payments:write",
        request=request,
        db=db,
        api_key_value=api_key_value,
        bearer_token=bearer_token,
    )
    _require_owned_payment_intent(payload.payment_id, user)
    return pay_payment_intent(payload.payment_id)


@router.post("/verify", response_model=PaymentVerificationResponse)
def verify_payment(
    payload: PaymentVerificationRequest,
    request: Request,
    db: DbSession,
    api_key_value: ApiKeyValue = None,
    bearer_token: JwtToken = None,
):
    user = _payment_actor(
        required_scope="payments:write",
        request=request,
        db=db,
        api_key_value=api_key_value,
        bearer_token=bearer_token,
    )
    _require_owned_payment_intent(payload.payment_id, user)
    return verify_payment_intent(payload.payment_id, payload.tx_hash)


@router.post("/execute", response_model=JobExecutionResponse)
def execute_job(
    payload: JobExecutionRequest,
    request: Request,
    db: DbSession,
    api_key_value: ApiKeyValue = None,
    bearer_token: JwtToken = None,
):
    user = _payment_actor(
        required_scope="payments:write",
        request=request,
        db=db,
        api_key_value=api_key_value,
        bearer_token=bearer_token,
    )
    _require_owned_payment_intent(payload.payment_id, user)
    return unlock_payment_intent(payload.payment_id, payload.artifact_id)
