import uuid

import pytest
from fastapi.testclient import TestClient

from tothemoon_api.arc_adapter import (
    NexusTaskEvent,
    PaidWorkUnit,
    get_arc_jobs,
    submit_nexus_task_event,
    submit_paid_work_unit,
)
from tothemoon_api.main import app
from tothemoon_api.nexus_jobs import clear_jobs
from tothemoon_api.payments import clear_payment_intents

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def isolate_arc_jobs_file(monkeypatch, tmp_path):
    monkeypatch.setenv("ARC_JOBS_FILE", str(tmp_path / "arc_jobs.json"))


def setup_function():
    clear_jobs()
    clear_payment_intents()


def _auth_headers(client: TestClient, prefix: str) -> dict[str, str]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    signup = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert signup.status_code == 201
    login = client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _payment_intent(client: TestClient, headers: dict[str, str]) -> dict[str, object]:
    response = client.post(
        "/api/payments/intent",
        json={"artifact_id": "artifact_review_bundle", "buyer_address": "0xBuyerAddress"},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


def test_submit_nexus_task_event():
    event = NexusTaskEvent(
        task_id="GH-40",
        agent_id="dev",
        action="complete",
        evidence={"commit": "abc", "lines": "100"},
    )
    proof = submit_nexus_task_event(event)
    assert proof.task_id == "GH-40"
    assert proof.agent_id == "dev"
    assert proof.status == "completed"
    assert proof.proof_hash.startswith("0x")
    assert "commit" in proof.metadata["evidence_keys"]

    jobs = get_arc_jobs()
    assert len(jobs) > 0
    assert jobs[0].job_id == proof.job_id


def test_submit_paid_work_unit_records_arc_safe_stub_metadata():
    work_unit = PaidWorkUnit(
        work_unit_id="nexus-job-123",
        payment_id="pay_123",
        artifact_id="artifact_delivery_packet",
        agent_id="agent_backend",
        amount_usdc=0.001,
        status="completed",
        tx_hash="0xMockSettlementHash",
        evidence={"delivery": "artifact_delivery_packet", "review": "approved"},
    )

    proof = submit_paid_work_unit(work_unit)

    assert proof.work_unit_id == "nexus-job-123"
    assert proof.task_id == "nexus-job-123"
    assert proof.payment_id == "pay_123"
    assert proof.payment_status == "verified"
    assert proof.settlement_status == "SETTLED"
    assert proof.tx_hash == "0xMockSettlementHash"
    assert proof.status == "completed"
    assert proof.onchain_write_status == "stubbed"
    assert proof.metadata["source"] == "paid_work_unit"
    assert proof.metadata["artifact_id"] == "artifact_delivery_packet"
    assert proof.metadata["amount_usdc"] == "0.001"
    assert proof.metadata["onchain_write"] == "stubbed"
    assert proof.metadata["onchain_write_reason"]
    assert proof.proof_hash.startswith("0x")


def test_arc_write_endpoints_require_authentication():
    client = TestClient(app)

    list_response = client.get("/api/jobs")
    assert list_response.status_code == 401

    create_response = client.post(
        "/api/jobs",
        json={"id": "nexus-job-api", "description": "Unauthenticated job"},
    )
    assert create_response.status_code == 401

    get_response = client.get("/api/jobs/nexus-job-api")
    assert get_response.status_code == 401

    job_response = client.post(
        "/api/arc/jobs",
        json={"task_id": "nexus-job-api", "agent_id": "agent_backend", "action": "complete"},
    )
    assert job_response.status_code == 401

    paid_response = client.post(
        "/api/arc/paid-work-units",
        json={
            "work_unit_id": "nexus-job-api",
            "payment_id": "pay_api",
            "artifact_id": "artifact_review_bundle",
            "agent_id": "agent_backend",
            "amount_usdc": 0.005,
            "status": "paid",
            "tx_hash": "0xMockSettlementHash",
            "evidence": {"review": "queued"},
        },
    )
    assert paid_response.status_code == 401


def test_paid_work_unit_endpoint_exposes_arc_proof_contract_for_owner(monkeypatch):
    from tothemoon_api.circle import circle_client

    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    client = TestClient(app)
    headers = _auth_headers(client, "arc-owner")
    intent = _payment_intent(client, headers)

    response = client.post(
        "/api/arc/paid-work-units",
        json={
            "work_unit_id": intent["job_id"],
            "payment_id": intent["payment_id"],
            "artifact_id": "artifact_review_bundle",
            "agent_id": "agent_backend",
            "amount_usdc": 0.005,
            "status": "paid",
            "tx_hash": "0xMockSettlementHash",
            "evidence": {"review": "queued"},
        },
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["work_unit_id"] == intent["job_id"]
    assert payload["payment_id"] == intent["payment_id"]
    assert payload["status"] == "escrowed"
    assert payload["onchain_write_status"] == "stubbed"
    assert payload["metadata"]["source"] == "paid_work_unit"
    assert payload["metadata"]["onchain_write"] == "stubbed"
    assert "owner_user_id" not in payload


def test_arc_write_endpoints_reject_cross_user_payment_and_job(monkeypatch):
    from tothemoon_api.circle import circle_client

    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    client = TestClient(app)
    owner_headers = _auth_headers(client, "arc-owner-cross")
    other_headers = _auth_headers(client, "arc-other-cross")
    intent = _payment_intent(client, owner_headers)

    paid_response = client.post(
        "/api/arc/paid-work-units",
        json={
            "work_unit_id": intent["job_id"],
            "payment_id": intent["payment_id"],
            "artifact_id": "artifact_review_bundle",
            "agent_id": "agent_backend",
            "amount_usdc": 0.005,
            "status": "paid",
        },
        headers=other_headers,
    )
    assert paid_response.status_code == 404

    job_response = client.post(
        "/api/arc/jobs",
        json={"task_id": intent["job_id"], "agent_id": "agent_backend", "action": "complete"},
        headers=other_headers,
    )
    assert job_response.status_code == 404


def test_arc_job_event_write_requires_owned_job_without_payment():
    client = TestClient(app)
    owner_headers = _auth_headers(client, "arc-job-owner")
    other_headers = _auth_headers(client, "arc-job-other")
    job_response = client.post(
        "/api/jobs",
        headers=owner_headers,
        json={"id": "arc-owned-job", "description": "Tenant-owned Arc job"},
    )
    assert job_response.status_code == 200

    other_response = client.post(
        "/api/arc/jobs",
        headers=other_headers,
        json={"task_id": "arc-owned-job", "agent_id": "agent_backend", "action": "complete"},
    )
    assert other_response.status_code == 404

    owner_response = client.post(
        "/api/arc/jobs",
        headers=owner_headers,
        json={"task_id": "arc-owned-job", "agent_id": "agent_backend", "action": "complete"},
    )
    assert owner_response.status_code == 200
    assert owner_response.json()["task_id"] == "arc-owned-job"

    listed = client.get("/api/arc/jobs", headers=owner_headers)
    assert listed.status_code == 200
    assert [job["task_id"] for job in listed.json()] == ["arc-owned-job"]
    assert "owner_user_id" not in listed.json()[0]


def test_jobs_are_scoped_to_authenticated_owner():
    client = TestClient(app)
    owner_headers = _auth_headers(client, "job-owner")
    other_headers = _auth_headers(client, "job-other")

    create_response = client.post(
        "/api/jobs",
        headers=owner_headers,
        json={"id": "tenant-owned-job", "description": "Tenant-owned job"},
    )
    assert create_response.status_code == 200

    owner_list = client.get("/api/jobs", headers=owner_headers)
    assert owner_list.status_code == 200
    assert [job["id"] for job in owner_list.json()] == ["tenant-owned-job"]

    owner_get = client.get("/api/jobs/tenant-owned-job", headers=owner_headers)
    assert owner_get.status_code == 200
    assert owner_get.json()["id"] == "tenant-owned-job"

    other_list = client.get("/api/jobs", headers=other_headers)
    assert other_list.status_code == 200
    assert other_list.json() == []

    other_get = client.get("/api/jobs/tenant-owned-job", headers=other_headers)
    assert other_get.status_code == 404
