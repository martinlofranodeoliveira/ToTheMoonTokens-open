from uuid import uuid4

from fastapi.testclient import TestClient

from tothemoon_api.main import app

client = TestClient(app)
PASSWORD = "correct horse battery staple"


def test_openapi_keeps_public_auth_and_protected_saas_routes_registered():
    schema = client.get("/openapi.json")

    assert schema.status_code == 200
    paths = schema.json()["paths"]
    expected_routes = [
        ("/api/v1/auth/signup", "post"),
        ("/api/v1/auth/login", "post"),
        ("/api/v1/saas/account", "get"),
        ("/api/v1/saas/account/export", "get"),
        ("/api/v1/saas/dashboard", "get"),
        ("/api/v1/saas/export", "get"),
        ("/api/v1/saas/token-discovery-history", "get"),
        ("/api/v1/saas/token-discovery-history/{history_id}", "get"),
        ("/api/v1/saas/token-discovery-history/{history_id}", "delete"),
        ("/api/v1/saas/provider-credentials/gemini", "post"),
        ("/api/jobs", "get"),
        ("/api/jobs/{job_id}", "get"),
        ("/api/payments/orders", "get"),
        ("/api/payments/intent", "post"),
        ("/api/payments/pay", "post"),
        ("/api/payments/verify", "post"),
        ("/api/payments/execute", "post"),
        ("/api/arc/jobs", "get"),
        ("/api/agent/chat", "post"),
        ("/api/providers/readiness", "get"),
        ("/api/operator/readiness-evidence", "get"),
        ("/api/v1/copilot/proposals", "post"),
        ("/api/v1/copilot/approval-queue", "get"),
        ("/api/v1/copilot/proposals/{proposal_id}/approve", "post"),
        ("/api/v1/copilot/proposals/{proposal_id}/reject", "post"),
        ("/api/v1/copilot/scan", "post"),
        ("/api/v1/copilot/scanner-runs", "get"),
        ("/api/v1/copilot/scanner-runs/latest-paper-evidence", "get"),
        ("/api/v1/copilot/scanner-runs/{scanner_run_id}", "get"),
        ("/api/v1/tokens/{token_address}/audit", "get"),
    ]

    for route, method in expected_routes:
        assert route in paths
        assert method in paths[route]


def test_protected_account_route_requires_bearer_token():
    response = client.get("/api/v1/saas/account")

    assert response.status_code == 401


def test_global_demo_job_routes_are_disabled_by_default():
    response = client.post("/api/demo/jobs/request", json={"artifact_type": "delivery_packet"})

    assert response.status_code == 404


def auth_headers() -> dict[str, str]:
    email = f"route-contract-{uuid4().hex}@example.com"
    signup = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert signup.status_code == 201
    login = client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_deploy_validation_runtime_routes_require_auth():
    checks = [
        ("GET", "/api/jobs", None),
        ("POST", "/api/jobs", {"id": "route-contract-job", "description": "route contract"}),
        ("GET", "/api/jobs/missing-job", None),
        ("GET", "/api/payments/orders", None),
        (
            "POST",
            "/api/payments/intent",
            {"artifact_id": "artifact_delivery_packet", "buyer_address": "0xBuyerAddress"},
        ),
        ("POST", "/api/payments/pay", {"payment_id": "missing-payment"}),
        (
            "POST",
            "/api/payments/verify",
            {"payment_id": "missing-payment", "tx_hash": "0xMockTransactionHash"},
        ),
        (
            "POST",
            "/api/payments/execute",
            {"artifact_id": "artifact_delivery_packet", "payment_id": "missing-payment"},
        ),
        ("GET", "/api/arc/jobs", None),
        ("POST", "/api/agent/chat", {"message": "status", "history": []}),
    ]

    for method, path, json_payload in checks:
        response = client.request(method, path, json=json_payload)
        assert response.status_code == 401, f"{method} {path} should require auth"


def test_deploy_validation_runtime_routes_accept_auth():
    headers = auth_headers()
    job_id = f"route-contract-job-{uuid4().hex}"
    checks = [
        ("GET", "/api/jobs", None, {200}),
        ("POST", "/api/jobs", {"id": job_id, "description": "route contract"}, {200}),
        ("GET", "/api/jobs/missing-job", None, {404}),
        ("GET", "/api/payments/orders", None, {200}),
        (
            "POST",
            "/api/payments/intent",
            {"artifact_id": "artifact_delivery_packet", "buyer_address": "0xBuyerAddress"},
            {200},
        ),
        ("POST", "/api/payments/pay", {"payment_id": "missing-payment"}, {404}),
        (
            "POST",
            "/api/payments/verify",
            {"payment_id": "missing-payment", "tx_hash": "0xMockTransactionHash"},
            {404},
        ),
        (
            "POST",
            "/api/payments/execute",
            {"artifact_id": "artifact_delivery_packet", "payment_id": "missing-payment"},
            {404},
        ),
        ("GET", "/api/arc/jobs", None, {200}),
        ("POST", "/api/agent/chat", {"message": "status", "history": []}, {200, 409}),
    ]

    for method, path, json_payload, expected_statuses in checks:
        response = client.request(method, path, headers=headers, json=json_payload)
        assert response.status_code in expected_statuses, f"{method} {path} returned {response.status_code}"


def test_public_auth_routes_keep_expected_status_contracts():
    signup = client.post(
        "/api/v1/auth/signup",
        json={"email": "synthetic-user-38@example.com", "password": "correct horse battery staple"},
    )
    login = client.post(
        "/api/v1/auth/login",
        data={"username": "synthetic-user-38@example.com", "password": "correct horse battery staple"},
    )

    assert signup.status_code == 201
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"
