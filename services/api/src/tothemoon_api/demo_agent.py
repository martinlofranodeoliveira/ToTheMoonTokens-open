from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .observability import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/demo/jobs", tags=["demo"])

DemoJobState = Literal["REQUESTED", "PAID", "REVIEW_PENDING", "REVIEWED", "DELIVERED"]


class DemoJobRequest(BaseModel):
    artifact_type: str = Field(default="delivery_packet")


class DemoJob(BaseModel):
    id: str
    artifact_type: str
    state: DemoJobState
    price_usdc: float


_PRICE_BY_ARTIFACT = {
    "delivery_packet": 0.001,
    "review_bundle": 0.005,
    "market_intel_brief": 0.01,
}

_jobs: dict[str, DemoJob] = {}


def clear_demo_jobs() -> None:
    _jobs.clear()


def ensure_job_paid(job_id: str) -> DemoJob:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.state == "REQUESTED":
        return pay_job(job_id)
    return job


def advance_job_to_delivered(job_id: str) -> DemoJob:
    job = ensure_job_paid(job_id)
    if job.state == "PAID":
        job = execute_job(job_id)
    if job.state == "REVIEW_PENDING":
        job = review_job(job_id, approve=True)
    if job.state == "REVIEWED":
        job = deliver_job(job_id)
    return job


@router.get("", response_model=list[DemoJob])
def list_demo_jobs():
    return list(_jobs.values())


@router.delete("")
def clear_demo_jobs_endpoint():
    clear_demo_jobs()
    return {"ok": True}


@router.get("/{job_id}", response_model=DemoJob)
def get_demo_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.post("/request", response_model=DemoJob)
def request_job(req: DemoJobRequest):
    job_id = uuid.uuid4().hex[:8]
    price = _PRICE_BY_ARTIFACT.get(req.artifact_type, 5.0)
    job = DemoJob(
        id=job_id,
        artifact_type=req.artifact_type,
        state="REQUESTED",
        price_usdc=price,
    )
    _jobs[job_id] = job
    log.info(
        "audit_event",
        event_type="request",
        job_id=job_id,
        artifact=req.artifact_type,
        state=job.state,
    )
    return job


@router.post("/{job_id}/pay", response_model=DemoJob)
def pay_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.state != "REQUESTED":
        raise HTTPException(409, f"Cannot pay for job in state {job.state}")

    job.state = "PAID"
    log.info(
        "audit_event", event_type="payment", job_id=job_id, state=job.state, amount=job.price_usdc
    )
    return job


@router.post("/{job_id}/execute", response_model=DemoJob)
def execute_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.state != "PAID":
        raise HTTPException(409, f"Cannot execute job in state {job.state}. Payment required.")

    job.state = "REVIEW_PENDING"
    log.info("audit_event", event_type="execution", job_id=job_id, state=job.state)
    return job


@router.post("/{job_id}/review", response_model=DemoJob)
def review_job(job_id: str, approve: bool = True):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.state != "REVIEW_PENDING":
        raise HTTPException(409, f"Cannot review job in state {job.state}")

    job.state = "REVIEWED" if approve else "PAID"
    log.info(
        "audit_event",
        event_type="review",
        job_id=job_id,
        state=job.state,
        decision="approved" if approve else "needs-rework",
    )
    return job


@router.post("/{job_id}/deliver", response_model=DemoJob)
def deliver_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.state != "REVIEWED":
        raise HTTPException(409, f"Cannot deliver job in state {job.state}. Review required.")

    job.state = "DELIVERED"
    log.info("audit_event", event_type="delivery", job_id=job_id, state=job.state)
    return job
