from __future__ import annotations

import threading
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from .config import get_settings
from .observability import get_logger

router = APIRouter(prefix="/api/settlements", tags=["settlement"])
log = get_logger(__name__)

JobStatus = Literal["PENDING", "SETTLED", "REJECTED", "REFUND_REQUIRED"]


class AgentReputation(BaseModel):
    agent_id: str
    successful_jobs: int = 0
    failed_jobs: int = 0
    is_verified: bool = True
    reputation_score_override: float | None = None

    @property
    def reputation_score(self) -> float:
        if self.reputation_score_override is not None:
            return self.reputation_score_override
        total_jobs = self.successful_jobs + self.failed_jobs
        if total_jobs <= 0:
            return 0.0
        return self.successful_jobs / total_jobs


class JobSignal(BaseModel):
    signal_type: str
    description: str


class SettlementRequest(BaseModel):
    job_id: str
    agent_id: str
    amount: float = Field(gt=0)
    tx_hash: str
    payment_intent_id: str | None = None
    expected_sender: str | None = None
    expected_receiver: str | None = None
    expected_token_address: str | None = None
    work_proof: str = ""
    timeout_s: float = Field(default=3.0, gt=0, le=30.0)
    signals: list[JobSignal] = Field(default_factory=list)


class SettlementResult(BaseModel):
    job_id: str
    status: JobStatus
    reason: str
    payment_intent_id: str | None = None
    tx_hash: str | None = None
    auditable_signals: list[JobSignal] = Field(default_factory=list)
    receipt_status: str | None = None


_SEEN_PAYMENT_INTENTS: set[str] = set()
_NATIVE_USDC_DECIMALS = 18
_ERC20_USDC_DECIMALS = 6
_ERC20_TRANSFER_TOPIC = "0x0000000000000000000000000000000000000000000000000000000000000017"


def clear_seen_payment_intents() -> None:
    _SEEN_PAYMENT_INTENTS.clear()


def _normalize_address(value: str | None) -> str | None:
    if not value:
        return None
    return value.lower()


def _parse_quantity(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        if stripped.lower().startswith("0x"):
            return int(stripped, 16)
        return int(stripped)
    except ValueError:
        return None


def _to_base_units(amount: float, decimals: int) -> int:
    scaled = Decimal(str(amount)) * (Decimal(10) ** decimals)
    return int(scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _extract_topic_address(topic: object) -> str | None:
    if not isinstance(topic, str):
        return None
    normalized = topic.lower()
    if not normalized.startswith("0x") or len(normalized) < 42:
        return None
    return _normalize_address(f"0x{normalized[-40:]}")


def _as_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _find_native_movement_log(
    logs: object,
    expected_sender: str | None,
    expected_receiver: str | None,
    expected_amount: int,
) -> dict[str, object] | None:
    if not isinstance(logs, list):
        return None

    for entry in logs:
        if not isinstance(entry, dict):
            continue
        topics = entry.get("topics")
        if not isinstance(topics, list) or len(topics) < 3:
            continue

        log_sender = _extract_topic_address(topics[1])
        log_receiver = _extract_topic_address(topics[2])
        log_amount = _parse_quantity(entry.get("data"))

        if expected_sender and log_sender != expected_sender:
            continue
        if expected_receiver and log_receiver != expected_receiver:
            continue
        if log_amount != expected_amount:
            continue
        return entry

    return None


def _find_erc20_transfer_log(
    logs: object,
    expected_token_address: str | None,
    expected_sender: str | None,
    expected_receiver: str | None,
    expected_amount: int,
) -> dict[str, object] | None:
    if not expected_token_address or not isinstance(logs, list):
        return None

    for entry in logs:
        if not isinstance(entry, dict):
            continue
        if _normalize_address(entry.get("address")) != expected_token_address:
            continue

        topics = entry.get("topics")
        if not isinstance(topics, list) or len(topics) < 3:
            continue
        if str(topics[0]).lower() != _ERC20_TRANSFER_TOPIC:
            continue

        log_sender = _extract_topic_address(topics[1])
        log_receiver = _extract_topic_address(topics[2])
        log_amount = _parse_quantity(entry.get("data"))

        if expected_sender and log_sender != expected_sender:
            continue
        if expected_receiver and log_receiver != expected_receiver:
            continue
        if log_amount != expected_amount:
            continue
        return entry

    return None


_ARC_RPC_CLIENT: httpx.Client | None = None
_ARC_RPC_CLIENT_LOCK = threading.Lock()


def _arc_rpc_client() -> httpx.Client:
    global _ARC_RPC_CLIENT
    if _ARC_RPC_CLIENT is not None:
        return _ARC_RPC_CLIENT
    with _ARC_RPC_CLIENT_LOCK:
        if _ARC_RPC_CLIENT is None:
            _ARC_RPC_CLIENT = httpx.Client(
                timeout=5.0,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            )
    return _ARC_RPC_CLIENT


def fetch_transaction_receipt(
    tx_hash: str,
    timeout_s: float = 3.0,
    rpc_url: str | None = None,
) -> dict[str, object] | None:
    settings = get_settings()
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
        "id": 1,
    }
    response = _arc_rpc_client().post(
        rpc_url or settings.arc_testnet_rpc_url,
        json=payload,
        timeout=timeout_s,
    )
    response.raise_for_status()
    result = response.json().get("result")
    return result if isinstance(result, dict) else None


def fetch_transaction_by_hash(
    tx_hash: str,
    timeout_s: float = 3.0,
    rpc_url: str | None = None,
) -> dict[str, object] | None:
    settings = get_settings()
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getTransactionByHash",
        "params": [tx_hash],
        "id": 1,
    }
    response = _arc_rpc_client().post(
        rpc_url or settings.arc_testnet_rpc_url,
        json=payload,
        timeout=timeout_s,
    )
    response.raise_for_status()
    result = response.json().get("result")
    return result if isinstance(result, dict) else None


def verify_settlement(
    request: SettlementRequest,
    agent: AgentReputation | None = None,
    receipt_fetcher: Callable[[str], dict[str, object] | None] | None = None,
    transaction_fetcher: Callable[[str], dict[str, object] | None] | None = None,
) -> SettlementResult:
    signals = request.signals.copy()

    if request.payment_intent_id and request.payment_intent_id in _SEEN_PAYMENT_INTENTS:
        signals.append(
            JobSignal(
                signal_type="replay_detected",
                description=f"Payment intent {request.payment_intent_id} was already processed",
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REJECTED",
            reason="Payment intent replay detected.",
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
        )

    if agent is not None and not agent.is_verified:
        signals.append(
            JobSignal(
                signal_type="verification_failure",
                description="Agent is not network verified",
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REJECTED",
            reason="Agent must be verified to settle jobs.",
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
        )

    if agent is not None and agent.reputation_score < 0.6:
        signals.append(
            JobSignal(
                signal_type="reputation_low",
                description=(
                    f"Agent reputation {agent.reputation_score:.2f} is below 0.6 threshold"
                ),
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REJECTED",
            reason="Agent reputation too low for settlement.",
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
        )

    if (
        not request.work_proof
        or len(request.work_proof.strip()) < 10
        or request.work_proof == "none"
    ):
        signals.append(
            JobSignal(
                signal_type="missing_proof",
                description="No valid proof of work provided",
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REJECTED",
            reason="Missing or invalid proof of work.",
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
        )

    if not request.tx_hash.startswith("0x"):
        signals.append(
            JobSignal(
                signal_type="receipt_missing",
                description="Transaction hash is not a valid hex hash",
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REFUND_REQUIRED",
            reason="Settlement transaction hash is invalid.",
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
        )

    if not request.expected_receiver:
        signals.append(
            JobSignal(
                signal_type="receiver_missing",
                description="Expected receiver is required for settlement verification",
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REJECTED",
            reason="Expected receiver missing for settlement verification.",
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
        )

    fetcher = receipt_fetcher or (
        lambda tx_hash: fetch_transaction_receipt(tx_hash, request.timeout_s)
    )
    tx_fetcher = transaction_fetcher or (
        lambda tx_hash: fetch_transaction_by_hash(tx_hash, request.timeout_s)
    )
    try:
        receipt = fetcher(request.tx_hash)
    except httpx.TimeoutException:
        signals.append(
            JobSignal(
                signal_type="refund_required",
                description=f"Settlement verification exceeded {request.timeout_s:.1f}s timeout",
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REFUND_REQUIRED",
            reason="Settlement verification timed out.",
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
        )
    except httpx.HTTPError as exc:
        log.warning("settlement_receipt_fetch_failed", tx_hash=request.tx_hash, error=str(exc))
        signals.append(
            JobSignal(
                signal_type="receipt_missing",
                description="Transaction receipt lookup failed",
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REFUND_REQUIRED",
            reason="Transaction receipt lookup failed.",
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
        )

    if not receipt:
        signals.append(
            JobSignal(
                signal_type="receipt_missing",
                description="No transaction receipt was found on Arc testnet",
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REFUND_REQUIRED",
            reason="Transaction receipt not found.",
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
        )

    try:
        transaction = tx_fetcher(request.tx_hash)
    except httpx.TimeoutException:
        signals.append(
            JobSignal(
                signal_type="transaction_lookup_timeout",
                description=f"Transaction lookup exceeded {request.timeout_s:.1f}s timeout",
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REFUND_REQUIRED",
            reason="Transaction lookup timed out.",
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
        )
    except httpx.HTTPError as exc:
        log.warning("settlement_transaction_fetch_failed", tx_hash=request.tx_hash, error=str(exc))
        signals.append(
            JobSignal(
                signal_type="transaction_lookup_failed",
                description="Transaction lookup failed",
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REFUND_REQUIRED",
            reason="Transaction lookup failed.",
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
        )

    receipt_status = str(receipt.get("status", ""))
    if receipt_status not in {"0x1", "1", "True"}:
        signals.append(
            JobSignal(
                signal_type="receipt_failed",
                description=f"Receipt status {receipt_status!r} indicates failure",
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REJECTED",
            reason="Settlement receipt indicates a failed transaction.",
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
            receipt_status=receipt_status,
        )

    settings = get_settings()
    expected_sender = _normalize_address(request.expected_sender)
    expected_receiver = _normalize_address(request.expected_receiver)
    expected_token_address = _normalize_address(
        request.expected_token_address or settings.arc_native_usdc_token_address
    )
    native_amount = _to_base_units(request.amount, _NATIVE_USDC_DECIMALS)
    erc20_amount = _to_base_units(request.amount, _ERC20_USDC_DECIMALS)

    onchain_receiver = _normalize_address(
        _as_optional_str(transaction.get("to")) if isinstance(transaction, dict) else None
    ) or _normalize_address(
        _as_optional_str(receipt.get("to")) if isinstance(receipt, dict) else None
    )
    onchain_sender = _normalize_address(
        _as_optional_str(transaction.get("from")) if isinstance(transaction, dict) else None
    ) or _normalize_address(
        _as_optional_str(receipt.get("from")) if isinstance(receipt, dict) else None
    )
    onchain_value = _parse_quantity(
        transaction.get("value") if isinstance(transaction, dict) else None
    )
    if onchain_value is None:
        onchain_value = _parse_quantity(receipt.get("value") if isinstance(receipt, dict) else None)

    logs = receipt.get("logs") if isinstance(receipt, dict) else None
    native_log = _find_native_movement_log(logs, expected_sender, expected_receiver, native_amount)
    erc20_log = _find_erc20_transfer_log(
        logs,
        expected_token_address,
        expected_sender,
        expected_receiver,
        erc20_amount,
    )

    native_transfer_valid = (
        expected_receiver == onchain_receiver
        and onchain_value == native_amount
        and (expected_sender is None or expected_sender == onchain_sender)
    )
    erc20_transfer_valid = erc20_log is not None

    if not native_transfer_valid and not erc20_transfer_valid:
        if transaction is None and onchain_value is None:
            signals.append(
                JobSignal(
                    signal_type="transaction_missing",
                    description="No transaction details were found on Arc testnet",
                )
            )
            return SettlementResult(
                job_id=request.job_id,
                status="REFUND_REQUIRED",
                reason="Transaction details not found.",
                payment_intent_id=request.payment_intent_id,
                tx_hash=request.tx_hash,
                auditable_signals=signals,
                receipt_status=receipt_status,
            )

        mismatch_reason = "No onchain transfer matched the expected receiver and amount."
        mismatch_signal = "settlement_mismatch"
        mismatch_description = "Onchain transfer did not match the payment intent"

        if expected_sender and onchain_sender and expected_sender != onchain_sender:
            mismatch_reason = "Settlement sender does not match the payment intent."
            mismatch_signal = "sender_mismatch"
            mismatch_description = (
                f"Transaction sender {onchain_sender} does not match expected {expected_sender}"
            )
        elif expected_receiver and onchain_receiver and expected_receiver != onchain_receiver:
            mismatch_reason = "Settlement receiver does not match the payment intent."
            mismatch_signal = "receipt_mismatch"
            mismatch_description = f"Transaction receiver {onchain_receiver} does not match expected {expected_receiver}"
        elif onchain_value is not None and onchain_value != native_amount:
            mismatch_reason = "Settlement amount does not match the payment intent."
            mismatch_signal = "amount_mismatch"
            mismatch_description = (
                f"Transaction value {onchain_value} does not match expected {native_amount}"
            )

        signals.append(
            JobSignal(
                signal_type=mismatch_signal,
                description=mismatch_description,
            )
        )
        return SettlementResult(
            job_id=request.job_id,
            status="REJECTED",
            reason=mismatch_reason,
            payment_intent_id=request.payment_intent_id,
            tx_hash=request.tx_hash,
            auditable_signals=signals,
            receipt_status=receipt_status,
        )

    if native_transfer_valid:
        signals.append(
            JobSignal(
                signal_type="native_value_verified",
                description="Native Arc USDC transfer matched the expected receiver and amount",
            )
        )
        if native_log is not None:
            signals.append(
                JobSignal(
                    signal_type="movement_log_verified",
                    description="Receipt includes a balance movement log matching the native transfer",
                )
            )

    if erc20_transfer_valid:
        signals.append(
            JobSignal(
                signal_type="transfer_log_verified",
                description="USDC ERC-20 Transfer log matched the expected receiver and amount",
            )
        )

    signals.append(
        JobSignal(
            signal_type="proof_validated",
            description="Proof of work successfully validated",
        )
    )
    signals.append(
        JobSignal(
            signal_type="receipt_verified",
            description="Arc receipt confirms funds were settled",
        )
    )
    signals.append(
        JobSignal(
            signal_type="funds_cleared",
            description="Settlement funds are cleared for payment",
        )
    )

    if request.payment_intent_id:
        _SEEN_PAYMENT_INTENTS.add(request.payment_intent_id)

    return SettlementResult(
        job_id=request.job_id,
        status="SETTLED",
        reason="Job settled successfully.",
        payment_intent_id=request.payment_intent_id,
        tx_hash=request.tx_hash,
        auditable_signals=signals,
        receipt_status=receipt_status,
    )


@router.post("/verify", response_model=SettlementResult)
def verify_settlement_endpoint(request: SettlementRequest):
    receiver = request.expected_receiver or "0x0000000000000000000000000000000000000018"
    sender = request.expected_sender or "0x0000000000000000000000000000000000000016"
    native_amount = _to_base_units(request.amount, _NATIVE_USDC_DECIMALS)

    def _mock_receipt(tx_hash: str) -> dict[str, object]:
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

    def _mock_transaction(tx_hash: str) -> dict[str, object]:
        return {
            "hash": tx_hash,
            "from": sender,
            "to": receiver,
            "value": hex(native_amount),
        }

    receipt_fetcher = _mock_receipt if "mock" in request.tx_hash.lower() else None
    transaction_fetcher = _mock_transaction if "mock" in request.tx_hash.lower() else None
    return verify_settlement(
        request,
        receipt_fetcher=receipt_fetcher,
        transaction_fetcher=transaction_fetcher,
    )
