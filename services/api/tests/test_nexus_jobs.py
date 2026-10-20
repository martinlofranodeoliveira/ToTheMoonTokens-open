import uuid

from fastapi.testclient import TestClient

from tothemoon_api.main import app
from tothemoon_api.nexus_jobs import clear_jobs

client = TestClient(app)
PASSWORD = "correct horse battery staple"


def setup_function():
    clear_jobs()


def _auth_headers(prefix: str = "nexus") -> dict[str, str]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    signup = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert signup.status_code == 201
    login = client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_job_lifecycle():
    headers = _auth_headers("nexus-lifecycle")

    response = client.post(
        "/api/jobs",
        json={"id": "job1", "description": "test job"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["state"] == "REQUESTED"

    response = client.post("/api/jobs/job1/unlock_payment", headers=headers)
    assert response.status_code == 200
    assert response.json()["state"] == "PAYMENT_UNLOCKED"

    response = client.post("/api/jobs/job1/reserve_work", headers=headers)
    assert response.status_code == 200
    assert response.json()["state"] == "WORK_RESERVED"

    response = client.post("/api/jobs/job1/request_review", headers=headers)
    assert response.status_code == 200
    assert response.json()["state"] == "REVIEW_PENDING"

    response = client.post("/api/jobs/job1/deliver", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "DELIVERED"
    assert len(payload["transitions"]) == 5


def test_job_invalid_transition_returns_conflict():
    headers = _auth_headers("nexus-invalid")

    client.post(
        "/api/jobs",
        json={"id": "job2", "description": "test job"},
        headers=headers,
    )
    response = client.post("/api/jobs/job2/reserve_work", headers=headers)
    assert response.status_code == 409


def test_list_jobs():
    owner_headers = _auth_headers("nexus-list-owner")
    other_headers = _auth_headers("nexus-list-other")

    client.post(
        "/api/jobs",
        json={"id": "job1", "description": "test job"},
        headers=owner_headers,
    )
    client.post(
        "/api/jobs",
        json={"id": "job2", "description": "test job"},
        headers=other_headers,
    )
    response = client.get("/api/jobs", headers=owner_headers)
    assert response.status_code == 200
    assert [job["id"] for job in response.json()] == ["job1"]


def test_same_job_id_is_scoped_per_authenticated_owner():
    owner_headers = _auth_headers("nexus-same-id-owner")
    other_headers = _auth_headers("nexus-same-id-other")

    owner_create = client.post(
        "/api/jobs",
        json={"id": "shared-job", "description": "owner job"},
        headers=owner_headers,
    )
    assert owner_create.status_code == 200

    duplicate_same_owner = client.post(
        "/api/jobs",
        json={"id": "shared-job", "description": "duplicate owner job"},
        headers=owner_headers,
    )
    assert duplicate_same_owner.status_code == 409

    other_create = client.post(
        "/api/jobs",
        json={"id": "shared-job", "description": "other job"},
        headers=other_headers,
    )
    assert other_create.status_code == 200

    owner_unlock = client.post("/api/jobs/shared-job/unlock_payment", headers=owner_headers)
    assert owner_unlock.status_code == 200
    assert owner_unlock.json()["state"] == "PAYMENT_UNLOCKED"

    other_job = client.get("/api/jobs/shared-job", headers=other_headers)
    assert other_job.status_code == 200
    assert other_job.json()["state"] == "REQUESTED"


def test_jobs_require_authenticated_owner():
    owner_headers = _auth_headers("nexus-owner")
    other_headers = _auth_headers("nexus-other")

    assert client.get("/api/jobs").status_code == 401

    create_response = client.post(
        "/api/jobs",
        json={"id": "private-job", "description": "private"},
        headers=owner_headers,
    )
    assert create_response.status_code == 200

    other_response = client.get("/api/jobs/private-job", headers=other_headers)
    assert other_response.status_code == 404


def test_paid_payment_flow_updates_nexus_lifecycle(monkeypatch):
    from tothemoon_api.circle import circle_client
    from tothemoon_api.payments import clear_payment_intents

    clear_payment_intents()
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    headers = _auth_headers("nexus-paid")

    intent_response = client.post(
        "/api/payments/intent",
        json={"artifact_id": "artifact_delivery_packet", "buyer_address": "0xBuyerAddress"},
        headers=headers,
    )
    assert intent_response.status_code == 200
    intent_payload = intent_response.json()
    payment_id = intent_payload["payment_id"]
    job_id = intent_payload["job_id"]

    job_response = client.get(f"/api/jobs/{job_id}", headers=headers)
    assert job_response.status_code == 200
    job_payload = job_response.json()
    assert job_payload["state"] == "REQUESTED"
    assert job_payload["payment_id"] == payment_id
    assert job_payload["artifact_id"] == "artifact_delivery_packet"

    execute_before_payment = client.post(
        "/api/payments/execute",
        json={"artifact_id": "artifact_delivery_packet", "payment_id": payment_id},
        headers=headers,
    )
    assert execute_before_payment.status_code == 402

    verify_response = client.post(
        "/api/payments/verify",
        json={"payment_id": payment_id, "tx_hash": "0xMockTransactionHash"},
        headers=headers,
    )
    assert verify_response.status_code == 200

    paid_job = client.get(f"/api/jobs/{job_id}", headers=headers).json()
    assert paid_job["state"] == "PAYMENT_UNLOCKED"
    assert paid_job["settlement_status"] == "SETTLED"
    assert paid_job["tx_hash"] == "0xMockTransactionHash"

    execute_response = client.post(
        "/api/payments/execute",
        json={"artifact_id": "artifact_delivery_packet", "payment_id": payment_id},
        headers=headers,
    )
    assert execute_response.status_code == 200

    delivered_job = client.get(f"/api/jobs/{job_id}", headers=headers).json()
    assert delivered_job["state"] == "DELIVERED"
    assert delivered_job["download_url"] == "/api/artifacts/artifact_delivery_packet/download"
    assert [transition["to_state"] for transition in delivered_job["transitions"]] == [
        "REQUESTED",
        "PAYMENT_UNLOCKED",
        "WORK_RESERVED",
        "REVIEW_PENDING",
        "DELIVERED",
    ]


def test_delivery_endpoint_still_requires_review_gate():
    headers = _auth_headers("nexus-review")

    client.post(
        "/api/jobs",
        json={"id": "review-gated", "description": "test job"},
        headers=headers,
    )
    client.post("/api/jobs/review-gated/unlock_payment", headers=headers)
    client.post("/api/jobs/review-gated/reserve_work", headers=headers)

    response = client.post("/api/jobs/review-gated/deliver", headers=headers)
    assert response.status_code == 409
