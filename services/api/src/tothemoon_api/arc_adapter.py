from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ArcJobStatus = Literal["pending", "escrowed", "completed", "disputed"]
ArcOnchainWriteStatus = Literal["stubbed"]


class NexusTaskEvent(BaseModel):
    task_id: str
    agent_id: str
    action: str
    evidence: dict[str, str] = Field(default_factory=dict)
    payment_id: str | None = None
    payment_status: Literal["pending", "verified", "failed"] | None = None
    settlement_status: str | None = None
    tx_hash: str | None = None


class PaidWorkUnit(BaseModel):
    work_unit_id: str
    payment_id: str
    artifact_id: str
    agent_id: str
    amount_usdc: float = Field(gt=0)
    payment_status: Literal["verified"] = "verified"
    settlement_status: str = "SETTLED"
    status: Literal["paid", "completed", "disputed"] = "paid"
    tx_hash: str | None = None
    evidence: dict[str, str] = Field(default_factory=dict)


class ArcJobProof(BaseModel):
    job_id: str
    task_id: str
    agent_id: str
    status: ArcJobStatus
    proof_hash: str
    timestamp: datetime
    metadata: dict[str, str]
    work_unit_id: str | None = None
    payment_id: str | None = None
    payment_status: str | None = None
    settlement_status: str | None = None
    tx_hash: str | None = None
    onchain_write_status: ArcOnchainWriteStatus = "stubbed"
    owner_user_id: int | None = None


def _jobs_path() -> Path:
    configured = os.getenv("ARC_JOBS_FILE", ".nexus/arc_jobs.json")
    return Path(configured)


def _load_jobs() -> list[ArcJobProof]:
    path = _jobs_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [ArcJobProof.model_validate(item) for item in payload]
    except Exception:
        return []


def _persist(jobs: list[ArcJobProof]) -> None:
    path = _jobs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp"
    tmp_path.write_text(
        json.dumps([job.model_dump(mode="json") for job in jobs], indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "0x" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def generate_proof_hash(event: NexusTaskEvent, timestamp: datetime | None = None) -> str:
    proof_timestamp = timestamp or datetime.now(UTC)
    return _canonical_hash(
        {
            "task_id": event.task_id,
            "agent_id": event.agent_id,
            "action": event.action,
            "evidence": event.evidence,
            "payment_id": event.payment_id,
            "payment_status": event.payment_status,
            "settlement_status": event.settlement_status,
            "tx_hash": event.tx_hash,
            "timestamp": proof_timestamp.isoformat(),
        }
    )


def _next_job_id(jobs: list[ArcJobProof], timestamp: datetime) -> str:
    return f"job_arc_{len(jobs) + 1}_{int(timestamp.timestamp())}"


def _base_metadata(event: NexusTaskEvent) -> dict[str, str]:
    metadata = {
        "source": "nexus_task_event",
        "action": event.action,
        "evidence_keys": ",".join(sorted(event.evidence.keys())),
        "adapter_version": "1.1",
        "chain": "arc-testnet",
        "onchain_write": "stubbed",
        "onchain_write_reason": "Demo adapter records proof metadata without a signing key.",
    }
    if event.payment_id:
        metadata["payment_id"] = event.payment_id
    if event.payment_status:
        metadata["payment_status"] = event.payment_status
    if event.settlement_status:
        metadata["settlement_status"] = event.settlement_status
    return metadata


def submit_nexus_task_event(
    event: NexusTaskEvent,
    *,
    owner_user_id: int | None = None,
) -> ArcJobProof:
    jobs = _load_jobs()
    timestamp = datetime.now(UTC)
    job_id = _next_job_id(jobs, timestamp)
    proof_hash = generate_proof_hash(event, timestamp)

    proof = ArcJobProof(
        job_id=job_id,
        task_id=event.task_id,
        agent_id=event.agent_id,
        status="completed" if event.action == "complete" else "pending",
        proof_hash=proof_hash,
        timestamp=timestamp,
        metadata=_base_metadata(event),
        payment_id=event.payment_id,
        payment_status=event.payment_status,
        settlement_status=event.settlement_status,
        tx_hash=event.tx_hash,
        owner_user_id=owner_user_id,
    )
    jobs.append(proof)
    _persist(jobs)
    return proof


def submit_paid_work_unit(
    work_unit: PaidWorkUnit,
    *,
    owner_user_id: int | None = None,
) -> ArcJobProof:
    event = NexusTaskEvent(
        task_id=work_unit.work_unit_id,
        agent_id=work_unit.agent_id,
        action="complete" if work_unit.status == "completed" else "paid",
        evidence=work_unit.evidence,
        payment_id=work_unit.payment_id,
        payment_status=work_unit.payment_status,
        settlement_status=work_unit.settlement_status,
        tx_hash=work_unit.tx_hash,
    )
    proof = submit_nexus_task_event(event, owner_user_id=owner_user_id)
    proof.status = "completed" if work_unit.status == "completed" else "escrowed"
    proof.work_unit_id = work_unit.work_unit_id
    proof.metadata.update(
        {
            "source": "paid_work_unit",
            "artifact_id": work_unit.artifact_id,
            "amount_usdc": f"{work_unit.amount_usdc:.6f}".rstrip("0").rstrip("."),
            "work_unit_status": work_unit.status,
        }
    )
    jobs = _load_jobs()
    jobs = [job if job.job_id != proof.job_id else proof for job in jobs]
    _persist(jobs)
    return proof


def get_arc_jobs(limit: int = 20) -> list[ArcJobProof]:
    jobs = _load_jobs()
    return sorted(jobs, key=lambda x: x.timestamp, reverse=True)[:limit]
