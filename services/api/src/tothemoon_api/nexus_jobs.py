from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import current_user_from_jwt
from .db_models import User

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
CurrentUser = Annotated[User, Depends(current_user_from_jwt)]

JobState = Literal[
    "REQUESTED",
    "PAYMENT_UNLOCKED",
    "WORK_RESERVED",
    "REVIEW_PENDING",
    "DELIVERED",
]


class JobTransition(BaseModel):
    from_state: JobState | None
    to_state: JobState
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reason: str


class NexusJobCreateRequest(BaseModel):
    id: str
    description: str = "Nexus job"
    payment_id: str | None = None
    artifact_id: str | None = None
    artifact_type: str | None = None
    amount_usdc: float | None = None
    buyer_address: str | None = None


class NexusJob(BaseModel):
    id: str
    state: JobState = "REQUESTED"
    transitions: list[JobTransition] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    description: str = "Nexus job"
    payment_id: str | None = None
    artifact_id: str | None = None
    artifact_type: str | None = None
    amount_usdc: float | None = None
    buyer_address: str | None = None
    settlement_status: str | None = None
    tx_hash: str | None = None
    download_url: str | None = None

    def transition_to(self, new_state: JobState, reason: str) -> None:
        self.transitions.append(
            JobTransition(
                from_state=self.state,
                to_state=new_state,
                reason=reason,
            )
        )
        self.state = new_state


_jobs: dict[str, NexusJob] = {}
_job_owners: dict[str, int] = {}

_VALID_TRANSITIONS: dict[JobState, JobState | None] = {
    "PAYMENT_UNLOCKED": "REQUESTED",
    "WORK_RESERVED": "PAYMENT_UNLOCKED",
    "REVIEW_PENDING": "WORK_RESERVED",
    "DELIVERED": "REVIEW_PENDING",
}


def clear_jobs(owner_user_id: int | None = None) -> None:
    if owner_user_id is None:
        _jobs.clear()
        _job_owners.clear()
        return

    for job_id in [job_id for job_id, user_id in _job_owners.items() if user_id == owner_user_id]:
        _jobs.pop(job_id, None)
        _job_owners.pop(job_id, None)


def _visible_job_id(job_key: str) -> str:
    return job_key.rsplit(":", 1)[-1]


def _owned_job_key(job_id: str, owner_user_id: int) -> str | None:
    if _job_owners.get(job_id) == owner_user_id:
        return job_id
    scoped_job_id = f"{owner_user_id}:{job_id}"
    if _job_owners.get(scoped_job_id) == owner_user_id:
        return scoped_job_id
    return None


def create_job(
    job_id: str,
    description: str,
    *,
    payment_id: str | None = None,
    artifact_id: str | None = None,
    artifact_type: str | None = None,
    amount_usdc: float | None = None,
    buyer_address: str | None = None,
    owner_user_id: int | None = None,
) -> NexusJob:
    existing_owner = _job_owners.get(job_id)
    if job_id in _jobs and (owner_user_id is None or existing_owner == owner_user_id):
        raise ValueError(f"Job {job_id} already exists")
    if job_id in _jobs:
        scoped_job_id = f"{owner_user_id}:{job_id}"
        if scoped_job_id in _jobs:
            raise ValueError(f"Job {job_id} already exists")
        job_id = scoped_job_id
    job = NexusJob(
        id=_visible_job_id(job_id),
        description=description,
        payment_id=payment_id,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        amount_usdc=amount_usdc,
        buyer_address=buyer_address,
    )
    job.transitions.append(
        JobTransition(
            from_state=None,
            to_state="REQUESTED",
            reason="Job requested",
        )
    )
    _jobs[job_id] = job
    if owner_user_id is not None:
        _job_owners[job_id] = owner_user_id
    return job


def get_job(job_id: str) -> NexusJob | None:
    return _jobs.get(job_id)


def get_owned_job(job_id: str, owner_user_id: int) -> NexusJob | None:
    job_key = _owned_job_key(job_id, owner_user_id)
    return _jobs.get(job_key) if job_key else None


def list_jobs(owner_user_id: int | None = None) -> list[NexusJob]:
    jobs = list(_jobs.values())
    if owner_user_id is not None:
        jobs = [job for job_key, job in _jobs.items() if _job_owners.get(job_key) == owner_user_id]
    return sorted(jobs, key=lambda job: job.created_at)


def job_belongs_to_user(job_id: str | None, owner_user_id: int) -> bool:
    return bool(job_id) and get_owned_job(str(job_id), owner_user_id) is not None


def task_event_belongs_to_user(task_id: str | None, owner_user_id: int) -> bool:
    return job_belongs_to_user(task_id, owner_user_id)


def transition_owned_job(
    job_id: str,
    owner_user_id: int,
    new_state: JobState,
    reason: str,
) -> NexusJob | None:
    job_key = _owned_job_key(job_id, owner_user_id)
    return transition_job(job_key, new_state, reason) if job_key else None


def transition_job(job_id: str, new_state: JobState, reason: str) -> NexusJob | None:
    job = get_job(job_id)
    if job is None:
        return None
    expected_current = _VALID_TRANSITIONS[new_state]
    if expected_current != job.state:
        return None
    job.transition_to(new_state, reason)
    return job


def create_or_update_paid_job(
    *,
    job_id: str,
    payment_id: str,
    artifact_id: str,
    artifact_type: str,
    amount_usdc: float,
    buyer_address: str,
    owner_user_id: int | None = None,
) -> NexusJob:
    job = get_job(job_id)
    if job is None:
        return create_job(
            job_id,
            f"Paid Nexus job for {artifact_id}",
            payment_id=payment_id,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            amount_usdc=amount_usdc,
            buyer_address=buyer_address,
            owner_user_id=owner_user_id,
        )

    job.payment_id = payment_id
    job.artifact_id = artifact_id
    job.artifact_type = artifact_type
    job.amount_usdc = amount_usdc
    job.buyer_address = buyer_address
    if owner_user_id is not None:
        _job_owners[job_id] = owner_user_id
    return job


def record_payment_unlocked(
    *,
    job_id: str,
    payment_id: str,
    settlement_status: str | None,
    tx_hash: str | None,
) -> NexusJob | None:
    job = get_job(job_id)
    if job is None:
        return None

    job.payment_id = payment_id
    job.settlement_status = settlement_status
    job.tx_hash = tx_hash
    if job.state == "REQUESTED":
        job.transition_to("PAYMENT_UNLOCKED", "Payment confirmed")
    return job


def reserve_paid_work_for_review(job_id: str) -> NexusJob | None:
    job = get_job(job_id)
    if job is None:
        return None
    if job.state == "PAYMENT_UNLOCKED":
        job.transition_to("WORK_RESERVED", "Nexus work allocated")
    if job.state == "WORK_RESERVED":
        job.transition_to("REVIEW_PENDING", "Work completed, awaiting review")
    return job if job.state in {"REVIEW_PENDING", "DELIVERED"} else None


def unlock_delivery_after_review(job_id: str, download_url: str) -> NexusJob | None:
    job = deliver_job(job_id)
    if job is None:
        return None
    job.download_url = download_url
    return job


def unlock_payment(job_id: str) -> NexusJob | None:
    return transition_job(job_id, "PAYMENT_UNLOCKED", "Payment confirmed")


def reserve_work(job_id: str) -> NexusJob | None:
    return transition_job(job_id, "WORK_RESERVED", "Nexus work allocated")


def request_review(job_id: str) -> NexusJob | None:
    return transition_job(job_id, "REVIEW_PENDING", "Work completed, awaiting review")


def deliver_job(job_id: str) -> NexusJob | None:
    return transition_job(job_id, "DELIVERED", "Review passed, delivery unlocked")


def _require_transition(job: NexusJob | None, job_id: str, target: str) -> NexusJob:
    if job is None:
        if get_job(job_id) is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        raise HTTPException(status_code=409, detail=f"Cannot transition job {job_id} to {target}")
    return job


def _require_owned_job(job_id: str, user: User) -> NexusJob:
    job = get_owned_job(job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@router.get("", response_model=list[NexusJob])
def list_jobs_endpoint(user: CurrentUser):
    return list_jobs(owner_user_id=user.id)


@router.delete("")
def clear_jobs_endpoint(user: CurrentUser):
    clear_jobs(owner_user_id=user.id)
    return {"ok": True}


@router.post("", response_model=NexusJob)
def create_job_endpoint(request: NexusJobCreateRequest, user: CurrentUser):
    try:
        return create_job(
            request.id,
            request.description,
            payment_id=request.payment_id,
            artifact_id=request.artifact_id,
            artifact_type=request.artifact_type,
            amount_usdc=request.amount_usdc,
            buyer_address=request.buyer_address,
            owner_user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{job_id}", response_model=NexusJob)
def get_job_endpoint(job_id: str, user: CurrentUser):
    return _require_owned_job(job_id, user)


@router.post("/{job_id}/unlock_payment", response_model=NexusJob)
def unlock_payment_endpoint(job_id: str, user: CurrentUser):
    _require_owned_job(job_id, user)
    return _require_transition(
        transition_owned_job(job_id, user.id, "PAYMENT_UNLOCKED", "Payment confirmed"),
        job_id,
        "PAYMENT_UNLOCKED",
    )


@router.post("/{job_id}/reserve_work", response_model=NexusJob)
def reserve_work_endpoint(job_id: str, user: CurrentUser):
    _require_owned_job(job_id, user)
    return _require_transition(
        transition_owned_job(job_id, user.id, "WORK_RESERVED", "Nexus work allocated"),
        job_id,
        "WORK_RESERVED",
    )


@router.post("/{job_id}/request_review", response_model=NexusJob)
def request_review_endpoint(job_id: str, user: CurrentUser):
    _require_owned_job(job_id, user)
    return _require_transition(
        transition_owned_job(job_id, user.id, "REVIEW_PENDING", "Work completed, awaiting review"),
        job_id,
        "REVIEW_PENDING",
    )


@router.post("/{job_id}/deliver", response_model=NexusJob)
def deliver_job_endpoint(job_id: str, user: CurrentUser):
    _require_owned_job(job_id, user)
    return _require_transition(
        transition_owned_job(job_id, user.id, "DELIVERED", "Review passed, delivery unlocked"),
        job_id,
        "DELIVERED",
    )
