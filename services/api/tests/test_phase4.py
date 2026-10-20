from uuid import uuid4

from fastapi.testclient import TestClient

from tothemoon_api.database import SessionLocal
from tothemoon_api.db_models import SimulatedTrade
from tothemoon_api.main import app

client = TestClient(app)
PASSWORD = "correct horse battery staple"


def _signup_login_key(prefix: str) -> tuple[str, str, int]:
    email = f"{prefix}-{uuid4().hex}@example.com"
    signup = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert signup.status_code == 201
    org_id = int(signup.json()["org_id"])
    login = client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["access_token"]
    created = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "phase4"},
    )
    assert created.status_code == 201
    return token, created.json()["plaintext"], org_id


def _simulate(api_key: str, amount: float = 100.0) -> dict[str, object]:
    response = client.post(
        "/api/v1/simulate/order",
        headers={"X-API-Key": api_key},
        json={"token_address": "0xSAFE", "amount": amount, "side": "BUY"},
    )
    assert response.status_code == 200
    return response.json()


def test_dashboard_aggregates_per_org():
    token, api_key, org_id = _signup_login_key("dashboard")
    for _ in range(5):
        _simulate(api_key, amount=100.0)

    dashboard = client.get("/api/v1/saas/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["total_simulated_trades"] == 5
    assert body["total_volume_usd"] == 500.0
    assert body["total_fees_usd"] == 40.0
    assert len(body["last_30_days_chart_points"]) == 30

    with SessionLocal() as db:
        assert db.query(SimulatedTrade).filter(SimulatedTrade.org_id == org_id).count() == 5


def test_dashboard_isolates_orgs():
    token_a, key_a, _org_a = _signup_login_key("dashboard-a")
    token_b, key_b, _org_b = _signup_login_key("dashboard-b")
    _simulate(key_a, amount=100.0)
    _simulate(key_a, amount=150.0)
    _simulate(key_b, amount=25.0)

    dashboard_a = client.get(
        "/api/v1/saas/dashboard", headers={"Authorization": f"Bearer {token_a}"}
    )
    dashboard_b = client.get(
        "/api/v1/saas/dashboard", headers={"Authorization": f"Bearer {token_b}"}
    )

    assert dashboard_a.status_code == 200
    assert dashboard_b.status_code == 200
    assert dashboard_a.json()["total_simulated_trades"] == 2
    assert dashboard_a.json()["total_volume_usd"] == 250.0
    assert dashboard_b.json()["total_simulated_trades"] == 1
    assert dashboard_b.json()["total_volume_usd"] == 25.0


def test_honeypot_blocks_buy_api():
    _token, api_key, _org_id = _signup_login_key("honeypot")
    response = client.post(
        "/api/v1/simulate/order",
        headers={"X-API-Key": api_key},
        json={"token_address": "0xSCAM", "amount": 100.0, "side": "BUY"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Honeypot detected, blocked"
