# services/api/tests/test_api_tokens.py
from uuid import uuid4

from fastapi.testclient import TestClient

from tothemoon_api.main import app

client = TestClient(app)


def _create_api_key(scopes: list[str] | None = None) -> str:
    if scopes is None:
        email = f"tokens-{uuid4().hex}@example.com"
    else:
        scope_slug = "-".join(scope.replace(":", "-") for scope in sorted(scopes))
        email = f"scoped-{scope_slug}-{uuid4().hex}@example.com"
    password = "correct horse battery staple"
    client.post("/api/v1/auth/signup", json={"email": email, "password": password})
    login = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    token = login.json()["access_token"]
    payload: dict[str, object] = {"name": "tokens-test"}
    if scopes is not None:
        payload["scopes"] = scopes
    created = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    return created.json()["plaintext"]


def test_token_audit_endpoint():
    response = client.get("/api/v1/tokens/0xSAFE/audit", headers={"X-API-Key": _create_api_key()})
    assert response.status_code == 200
    data = response.json()
    assert "security" in data
    assert "market" in data
    assert data["security"]["is_honeypot"] is False


def test_token_audit_requires_api_key():
    response = client.get("/api/v1/tokens/0xSAFE/audit")
    assert response.status_code == 401


def test_token_audit_rejects_api_key_without_tokens_scope():
    response = client.get(
        "/api/v1/tokens/0xSAFE/audit",
        headers={"X-API-Key": _create_api_key(scopes=["simulate:order"])},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "API key scope denied"


def test_simulation_order_rejects_api_key_without_simulate_scope():
    response = client.post(
        "/api/v1/simulate/order",
        headers={"X-API-Key": _create_api_key(scopes=["tokens:audit"])},
        json={"token_address": "0xSAFE", "amount": 50.0, "side": "BUY"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "API key scope denied"


def test_simulation_positions_reject_api_key_without_simulate_scope():
    response = client.get(
        "/api/v1/simulate/positions",
        headers={"X-API-Key": _create_api_key(scopes=["tokens:audit"])},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "API key scope denied"


def test_simulation_position_close_rejects_api_key_without_simulate_scope():
    response = client.post(
        "/api/v1/simulate/positions/1/close",
        headers={"X-API-Key": _create_api_key(scopes=["tokens:audit"])},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "API key scope denied"


def test_api_key_create_rejects_unknown_scope():
    email = "synthetic-user-25@example.com"
    password = "correct horse battery staple"
    client.post("/api/v1/auth/signup", json={"email": email, "password": password})
    login = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    token = login.json()["access_token"]

    response = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "bad-scope", "scopes": ["tokens:admin"]},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid API key scopes: tokens:admin"
