from uuid import uuid4

from fastapi.testclient import TestClient

from tothemoon_api.main import app

client = TestClient(app)


def _create_api_key() -> tuple[str, str]:
    email = f"simulate-{uuid4().hex}@example.com"
    password = "correct horse battery staple"
    signup = client.post("/api/v1/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    created = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "simulate-test"},
    )
    assert created.status_code == 201
    return token, created.json()["plaintext"]


def test_simulate_endpoint():
    _token, api_key = _create_api_key()
    response = client.post(
        "/api/v1/simulate/order",
        json={"token_address": "0xABC", "amount": 50.0, "side": "BUY"},
        headers={"X-API-Key": api_key},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert data["trade_id"]
    assert data["fees_paid"] == 8.0


def test_positions_list_and_close():
    _token, api_key = _create_api_key()
    created = client.post(
        "/api/v1/simulate/order",
        json={"token_address": "0xABC", "amount": 50.0, "side": "BUY"},
        headers={"X-API-Key": api_key},
    )
    assert created.status_code == 200
    trade_id = created.json()["trade_id"]

    listed = client.get("/api/v1/simulate/positions", headers={"X-API-Key": api_key})
    assert listed.status_code == 200
    assert any(item["id"] == trade_id for item in listed.json())

    closed = client.post(
        f"/api/v1/simulate/positions/{trade_id}/close",
        headers={"X-API-Key": api_key},
    )
    assert closed.status_code == 200
    assert closed.json()["trade_id"] == trade_id
    assert closed.json()["realized_pnl_usd"] is not None


def test_simulate_endpoint_requires_api_key():
    response = client.post(
        "/api/v1/simulate/order", json={"token_address": "0xABC", "amount": 50.0, "side": "SELL"}
    )
    assert response.status_code == 401
