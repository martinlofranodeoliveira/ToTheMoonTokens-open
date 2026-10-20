import uuid

from fastapi.testclient import TestClient

from tothemoon_api.circle import circle_client
from tothemoon_api.demo_agent import DemoJobRequest, clear_demo_jobs, get_demo_job, request_job
from tothemoon_api.main import app
from tothemoon_api.nexus_jobs import clear_jobs
from tothemoon_api.payments import clear_payment_intents

client = TestClient(app)

SENDER = "0x0000000000000000000000000000000000000018"
TREASURY = "0x0000000000000000000000000000000000000016"
PASSWORD = "correct horse battery staple"


def setup_function():
    clear_payment_intents()
    clear_jobs()
    clear_demo_jobs()
    circle_client.wallets_by_role = {}
    circle_client.wallets_loaded = False


def _auth_headers(prefix: str = "payments") -> dict[str, str]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    signup = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert signup.status_code == 201
    login = client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_payment_catalog_returns_items(monkeypatch):
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    response = client.get("/api/payments/catalog")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) >= 3
    assert any(item["id"] == "artifact_delivery_packet" for item in payload)
    assert all(0 < item["price_usd"] <= 0.01 for item in payload)


def test_payment_intent_creation_and_verification_flow(monkeypatch):
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    headers = _auth_headers("payment-flow")

    intent_response = client.post(
        "/api/payments/intent",
        json={"artifact_id": "artifact_review_bundle", "buyer_address": "0xBuyerAddress"},
        headers=headers,
    )
    assert intent_response.status_code == 200
    intent_payload = intent_response.json()
    assert intent_payload["status"] == "pending"
    assert intent_payload["amount_usd"] == 0.005
    assert intent_payload["currency"] == "USDC"
    assert intent_payload["payment_requirement"] == {
        "asset": "USDC",
        "network": "arc_testnet",
        "amount": "0.005",
        "amount_native_units": "5000000000000000",
        "pay_to": "0xMockDepositAddressForTestnetOnly",
        "payment_id": intent_payload["payment_id"],
        "verification_endpoint": "/api/payments/verify",
    }
    payment_id = intent_payload["payment_id"]

    verify_response = client.post(
        "/api/payments/verify",
        json={"payment_id": payment_id, "tx_hash": "0xMockTransactionHash"},
        headers=headers,
    )
    assert verify_response.status_code == 200
    verify_payload = verify_response.json()
    assert verify_payload["status"] == "verified"
    assert verify_payload["unlocked_artifact_id"] == "artifact_review_bundle"


def test_payment_verification_fails_with_invalid_tx(monkeypatch):
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    headers = _auth_headers("payment-invalid")

    intent_response = client.post(
        "/api/payments/intent",
        json={"artifact_id": "artifact_market_intel_brief", "buyer_address": "0xBuyerAddress"},
        headers=headers,
    )
    assert intent_response.status_code == 200
    payment_id = intent_response.json()["payment_id"]

    verify_response = client.post(
        "/api/payments/verify",
        json={"payment_id": payment_id, "tx_hash": "invalid_tx"},
        headers=headers,
    )
    assert verify_response.status_code == 200
    verify_payload = verify_response.json()
    assert verify_payload["status"] == "failed"
    assert verify_payload["unlocked_artifact_id"] is None


def test_job_lifecycle_requires_payment(monkeypatch, tmp_path):
    monkeypatch.setenv("ARC_JOBS_FILE", str(tmp_path / "arc_jobs.json"))
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    headers = _auth_headers("payment-lifecycle")

    response = client.post(
        "/api/payments/execute",
        json={"artifact_id": "artifact_delivery_packet", "payment_id": "missing-payment"},
        headers=headers,
    )
    assert response.status_code == 404
    assert "Payment intent not found" in response.json()["detail"]

    intent_response = client.post(
        "/api/payments/intent",
        json={"artifact_id": "artifact_delivery_packet", "buyer_address": "0xBuyerAddress"},
        headers=headers,
    )
    payment_id = intent_response.json()["payment_id"]

    client.post(
        "/api/payments/verify",
        json={"payment_id": payment_id, "tx_hash": "0xMockTransactionHash"},
        headers=headers,
    )

    response = client.post(
        "/api/payments/execute",
        json={"artifact_id": "artifact_delivery_packet", "payment_id": payment_id},
        headers=headers,
    )
    assert response.status_code == 200
    execute_payload = response.json()
    assert execute_payload["status"] == "completed"
    assert execute_payload["download_url"] == "/api/artifacts/artifact_delivery_packet/download"

    orders_response = client.get("/api/payments/orders", headers=headers)
    assert orders_response.status_code == 200
    [order] = orders_response.json()
    assert order["status"] == "verified"
    assert order["settlement_status"] == "SETTLED"
    assert order["executed"] is True
    assert order["download_url"] == execute_payload["download_url"]

    job_response = client.get(f"/api/jobs/{order['job_id']}", headers=headers)
    assert job_response.status_code == 200
    job_payload = job_response.json()
    assert job_payload["state"] == "DELIVERED"
    assert job_payload["download_url"] == execute_payload["download_url"]
    assert [event["to_state"] for event in job_payload["transitions"]] == [
        "REQUESTED",
        "PAYMENT_UNLOCKED",
        "WORK_RESERVED",
        "REVIEW_PENDING",
        "DELIVERED",
    ]

    arc_jobs = client.get("/api/arc/jobs", headers=headers)
    assert arc_jobs.status_code == 200
    assert arc_jobs.json()[0]["payment_id"] == order["payment_id"]
    assert arc_jobs.json()[0]["metadata"]["artifact_id"] == "artifact_delivery_packet"


def test_delivery_unlock_is_blocked_until_payment_verified(monkeypatch):
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    headers = _auth_headers("payment-locked")

    intent_response = client.post(
        "/api/payments/intent",
        json={"artifact_id": "artifact_delivery_packet", "buyer_address": "0xBuyerAddress"},
        headers=headers,
    )
    assert intent_response.status_code == 200
    intent_payload = intent_response.json()

    blocked_response = client.post(
        "/api/payments/execute",
        json={
            "artifact_id": "artifact_delivery_packet",
            "payment_id": intent_payload["payment_id"],
        },
        headers=headers,
    )
    assert blocked_response.status_code == 402
    assert blocked_response.json()["detail"] == "Payment required to unlock this job."

    job_response = client.get(f"/api/jobs/{intent_payload['job_id']}", headers=headers)
    assert job_response.status_code == 200
    job_payload = job_response.json()
    assert job_payload["state"] == "REQUESTED"
    assert job_payload["download_url"] is None


def test_execute_requires_matching_payment_intent(monkeypatch):
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    headers = _auth_headers("payment-match")

    delivery_intent = client.post(
        "/api/payments/intent",
        json={"artifact_id": "artifact_delivery_packet", "buyer_address": "0xBuyerAddress"},
        headers=headers,
    )
    delivery_payment_id = delivery_intent.json()["payment_id"]
    client.post(
        "/api/payments/verify",
        json={"payment_id": delivery_payment_id, "tx_hash": "0xMockTransactionHash"},
        headers=headers,
    )

    response = client.post(
        "/api/payments/execute",
        json={
            "artifact_id": "artifact_market_intel_brief",
            "payment_id": delivery_payment_id,
        },
        headers=headers,
    )
    assert response.status_code == 409
    assert "does not match requested artifact" in response.json()["detail"]


def test_payment_orders_are_scoped_to_authenticated_user(monkeypatch):
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    owner_headers = _auth_headers("payment-owner")
    other_headers = _auth_headers("payment-other")

    intent_response = client.post(
        "/api/payments/intent",
        json={"artifact_id": "artifact_delivery_packet", "buyer_address": "0xBuyerAddress"},
        headers=owner_headers,
    )
    assert intent_response.status_code == 200
    payment_id = intent_response.json()["payment_id"]

    owner_orders = client.get("/api/payments/orders", headers=owner_headers)
    assert owner_orders.status_code == 200
    assert [order["payment_id"] for order in owner_orders.json()] == [payment_id]

    other_orders = client.get("/api/payments/orders", headers=other_headers)
    assert other_orders.status_code == 200
    assert other_orders.json() == []

    other_verify = client.post(
        "/api/payments/verify",
        json={"payment_id": payment_id, "tx_hash": "0xMockTransactionHash"},
        headers=other_headers,
    )
    assert other_verify.status_code == 404

    other_execute = client.post(
        "/api/payments/execute",
        json={"artifact_id": "artifact_delivery_packet", "payment_id": payment_id},
        headers=other_headers,
    )
    assert other_execute.status_code == 404


def test_payment_mutation_payloads_reject_blank_values(monkeypatch):
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    headers = _auth_headers("payment-blank-values")

    blank_intent = client.post(
        "/api/payments/intent",
        json={"artifact_id": "   ", "buyer_address": "0xBuyerAddress"},
        headers=headers,
    )
    assert blank_intent.status_code == 422

    blank_pay = client.post(
        "/api/payments/pay",
        json={"payment_id": "   "},
        headers=headers,
    )
    assert blank_pay.status_code == 422

    blank_verify = client.post(
        "/api/payments/verify",
        json={"payment_id": "pay_123", "tx_hash": "   "},
        headers=headers,
    )
    assert blank_verify.status_code == 422

    blank_execute = client.post(
        "/api/payments/execute",
        json={"artifact_id": "artifact_delivery_packet", "payment_id": "   "},
        headers=headers,
    )
    assert blank_execute.status_code == 422


def test_payment_mutation_payloads_trim_identifiers(monkeypatch):
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    headers = _auth_headers("payment-trim-values")

    intent_response = client.post(
        "/api/payments/intent",
        json={"artifact_id": " artifact_delivery_packet ", "buyer_address": " 0xBuyerAddress "},
        headers=headers,
    )
    assert intent_response.status_code == 200
    payment_id = intent_response.json()["payment_id"]

    verify_response = client.post(
        "/api/payments/verify",
        json={"payment_id": f" {payment_id} ", "tx_hash": " 0xMockTransactionHash "},
        headers=headers,
    )
    assert verify_response.status_code == 200
    assert verify_response.json()["payment_id"] == payment_id
    assert verify_response.json()["status"] == "verified"

    execute_response = client.post(
        "/api/payments/execute",
        json={"artifact_id": " artifact_delivery_packet ", "payment_id": f" {payment_id} "},
        headers=headers,
    )
    assert execute_response.status_code == 200
    assert execute_response.json()["status"] == "completed"


def test_payment_write_routes_require_authentication(monkeypatch):
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    headers = _auth_headers("payment-write-auth")
    intent_response = client.post(
        "/api/payments/intent",
        json={"artifact_id": "artifact_delivery_packet", "buyer_address": "0xBuyerAddress"},
        headers=headers,
    )
    assert intent_response.status_code == 200
    payment_id = intent_response.json()["payment_id"]

    orders_response = client.get("/api/payments/orders")
    assert orders_response.status_code == 401
    assert orders_response.json()["detail"] == "Missing bearer token"

    checks = [
        (
            "/api/payments/intent",
            {"artifact_id": "artifact_delivery_packet", "buyer_address": "0xBuyerAddress"},
        ),
        ("/api/payments/pay", {"payment_id": payment_id}),
        ("/api/payments/verify", {"payment_id": payment_id, "tx_hash": "0xMockTransactionHash"}),
        ("/api/payments/execute", {"artifact_id": "artifact_delivery_packet", "payment_id": payment_id}),
    ]
    for path, payload in checks:
        response = client.post(path, json=payload)
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing bearer token"


def test_payment_intent_cannot_attach_to_another_users_job(monkeypatch):
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    owner_headers = _auth_headers("payment-job-owner")
    other_headers = _auth_headers("payment-job-other")

    job_response = client.post(
        "/api/jobs",
        json={"id": "payment-owned-job", "description": "Owned payment job"},
        headers=owner_headers,
    )
    assert job_response.status_code == 200

    other_response = client.post(
        "/api/payments/intent",
        json={
            "artifact_id": "artifact_delivery_packet",
            "buyer_address": "0xBuyerAddress",
            "job_id": "payment-owned-job",
        },
        headers=other_headers,
    )
    assert other_response.status_code == 404
    assert other_response.json()["detail"] == "Nexus job not found"

    owner_response = client.post(
        "/api/payments/intent",
        json={
            "artifact_id": "artifact_delivery_packet",
            "buyer_address": "0xBuyerAddress",
            "job_id": "payment-owned-job",
        },
        headers=owner_headers,
    )
    assert owner_response.status_code == 200
    assert owner_response.json()["job_id"] == "payment-owned-job"


def test_arc_jobs_are_scoped_to_authenticated_payment_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("ARC_JOBS_FILE", str(tmp_path / "arc_jobs.json"))
    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    owner_headers = _auth_headers("arc-owner")
    other_headers = _auth_headers("arc-other")

    intent_response = client.post(
        "/api/payments/intent",
        json={"artifact_id": "artifact_delivery_packet", "buyer_address": "0xBuyerAddress"},
        headers=owner_headers,
    )
    assert intent_response.status_code == 200
    intent_payload = intent_response.json()
    payment_id = intent_payload["payment_id"]

    verify_response = client.post(
        "/api/payments/verify",
        json={"payment_id": payment_id, "tx_hash": "0xMockTransactionHash"},
        headers=owner_headers,
    )
    assert verify_response.status_code == 200

    execute_response = client.post(
        "/api/payments/execute",
        json={"artifact_id": "artifact_delivery_packet", "payment_id": payment_id},
        headers=owner_headers,
    )
    assert execute_response.status_code == 200

    assert client.get("/api/arc/jobs").status_code == 401

    owner_jobs = client.get("/api/arc/jobs", headers=owner_headers)
    assert owner_jobs.status_code == 200
    assert [job["payment_id"] for job in owner_jobs.json()] == [payment_id]

    other_jobs = client.get("/api/arc/jobs", headers=other_headers)
    assert other_jobs.status_code == 200
    assert other_jobs.json() == []


def test_programmatic_payment_endpoint_verifies_and_marks_demo_job_paid(monkeypatch):
    monkeypatch.setenv("AUTONOMOUS_PAYMENTS_ENABLED", "true")
    monkeypatch.setenv("WALLET_MODE", "custodial")
    monkeypatch.setenv("CIRCLE_API_KEY", "test-api-key")
    monkeypatch.setenv("CIRCLE_ENTITY_SECRET", "test-entity-secret")
    monkeypatch.setenv("CIRCLE_WALLET_SET_ID", "ws-123")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    monkeypatch.setattr(circle_client, "ensure_wallets_loaded", lambda: None)
    monkeypatch.setattr(
        circle_client,
        "create_transfer",
        lambda **_kwargs: {"data": {"id": "tx-123", "state": "INITIATED"}},
    )
    monkeypatch.setattr(
        circle_client,
        "wait_for_transaction",
        lambda *_args, **_kwargs: {
            "id": "tx-123",
            "state": "COMPLETE",
            "txHash": "0xmockautopayhash",
        },
    )

    circle_client.wallets_by_role = {"TREASURY": {"address": TREASURY}}
    circle_client.wallets_loaded = True
    headers = _auth_headers("payment-programmatic")

    job_id = request_job(DemoJobRequest(artifact_type="delivery_packet")).id

    intent_response = client.post(
        "/api/payments/intent",
        json={
            "artifact_id": "artifact_delivery_packet",
            "buyer_address": SENDER,
            "job_id": job_id,
        },
        headers=headers,
    )
    assert intent_response.status_code == 200
    payment_id = intent_response.json()["payment_id"]

    pay_response = client.post(
        "/api/payments/pay", json={"payment_id": payment_id}, headers=headers
    )
    assert pay_response.status_code == 200
    pay_payload = pay_response.json()
    assert pay_payload["status"] == "verified"
    assert pay_payload["settlement_status"] == "SETTLED"
    assert pay_payload["tx_hash"] == "0xmockautopayhash"

    job_state = get_demo_job(job_id)
    assert job_state.state == "PAID"
