from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from tothemoon_api.audit import REDACTED_VALUE, record_audit_log
from tothemoon_api.database import SessionLocal
from tothemoon_api.db_models import AuditLog
from tothemoon_api.external.health import build_provider_readiness, record_provider_status
from tothemoon_api.main import app

client = TestClient(app)
PASSWORD = "correct horse battery staple"


def _signup_and_login(prefix: str) -> tuple[str, int]:
    email = f"{prefix}-{uuid4().hex}@example.com"
    signup = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert signup.status_code == 201
    org_id = signup.json()["org_id"]

    login = client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"], org_id


def test_metrics_endpoint_exposes_prometheus_text():
    client.get("/health")
    client.post(
        "/api/backtests/run",
        json={
            "strategy_id": "ema_crossover",
            "lookback_bars": 120,
            "initial_capital": 5000,
            "position_size_pct": 10,
            "fee_bps": 5,
            "slippage_bps": 3,
        },
    )

    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "http_requests_total" in body
    assert "backtests_run_total" in body


def test_response_carries_security_headers():
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "permissions-policy" in response.headers


def test_request_id_is_generated_when_absent():
    response = client.get("/health")
    request_id = response.headers.get("x-request-id")
    assert request_id
    assert len(request_id) >= 16


def test_request_id_is_propagated_from_client():
    response = client.get("/health", headers={"X-Request-ID": "fixture-123"})
    assert response.headers.get("x-request-id") == "fixture-123"


def test_overly_long_request_id_is_rejected_and_regenerated():
    response = client.get("/health", headers={"X-Request-ID": "x" * 500})
    incoming = response.headers.get("x-request-id")
    assert incoming and incoming != "x" * 500


def test_readiness_endpoint_returns_checks():
    response = client.get("/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["checks"]["mainnet_permanently_blocked"] is True
    assert payload["checks"]["strategies_loaded"] is True
    assert payload["checks"]["external_adapters_paper_only"] is True


def test_provider_readiness_endpoint_is_typed_redacted_and_paper_only():
    record_provider_status("goplus", status="degraded", last_error="401 sk-live-secret")

    response = client.get("/api/providers/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["paper_only"] is True
    assert payload["order_submission_enabled"] is False
    assert payload["mainnet_order_submission_enabled"] is False
    assert payload["provider_count"] == 5
    assert {provider["name"] for provider in payload["providers"]} == {
        "goplus",
        "honeypotis",
        "tokensniffer",
        "dexscreener",
        "birdeye",
    }
    assert all(provider["paper_only_safe"] is True for provider in payload["providers"])
    goplus = next(provider for provider in payload["providers"] if provider["name"] == "goplus")
    assert goplus["last_error"] == f"401 {REDACTED_VALUE}"
    assert "sk-live-secret" not in response.text


def test_readiness_fails_when_external_adapter_contract_is_unsafe(monkeypatch):
    monkeypatch.setattr(
        "tothemoon_api.main.get_external_adapter_contract",
        lambda: {
            "order_submission_enabled": True,
            "mainnet_order_submission_enabled": False,
            "providers": [
                {
                    "name": "unsafe_exchange",
                    "read_only": False,
                    "supports_order_submission": True,
                    "mainnet_order_submission": False,
                }
            ],
        },
    )

    response = client.get("/ready")

    assert response.status_code == 503
    payload = response.json()["detail"]
    assert payload["ok"] is False
    assert payload["checks"]["external_adapters_paper_only"] is False


def test_readiness_fails_when_external_adapter_contract_is_malformed(monkeypatch):
    monkeypatch.setattr(
        "tothemoon_api.main.get_external_adapter_contract",
        lambda: {
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": ["not-a-provider-object"],
        },
    )

    response = client.get("/ready")

    assert response.status_code == 503
    payload = response.json()["detail"]
    assert payload["checks"]["external_adapters_paper_only"] is False


def test_readiness_fails_when_external_adapter_contract_has_no_providers(monkeypatch):
    monkeypatch.setattr(
        "tothemoon_api.main.get_external_adapter_contract",
        lambda: {
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": [],
        },
    )

    response = client.get("/ready")

    assert response.status_code == 503
    payload = response.json()["detail"]
    assert payload["checks"]["external_adapters_paper_only"] is False


def test_readiness_fails_when_external_adapter_has_empty_name(monkeypatch):
    monkeypatch.setattr(
        "tothemoon_api.main.get_external_adapter_contract",
        lambda: {
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": [
                {
                    "name": " ",
                    "capabilities": ["market_data"],
                    "read_only": True,
                    "supports_order_submission": False,
                    "mainnet_order_submission": False,
                }
            ],
        },
    )

    response = client.get("/ready")

    assert response.status_code == 503
    payload = response.json()["detail"]
    assert payload["checks"]["external_adapters_paper_only"] is False


def test_readiness_fails_when_external_adapter_has_invalid_capability(monkeypatch):
    monkeypatch.setattr(
        "tothemoon_api.main.get_external_adapter_contract",
        lambda: {
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": [
                {
                    "name": "dexscreener",
                    "capabilities": ["order_submission"],
                    "read_only": True,
                    "supports_order_submission": False,
                    "mainnet_order_submission": False,
                }
            ],
        },
    )

    response = client.get("/ready")

    assert response.status_code == 503
    payload = response.json()["detail"]
    assert payload["checks"]["external_adapters_paper_only"] is False


def test_readiness_fails_when_external_adapter_has_public_exposure(monkeypatch):
    monkeypatch.setattr(
        "tothemoon_api.main.get_external_adapter_contract",
        lambda: {
            "mode": "paper_only_read_through",
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": [
                {
                    "name": "dexscreener",
                    "capabilities": ["market_data"],
                    "requires_api_key": False,
                    "read_only": True,
                    "supports_order_submission": False,
                    "mainnet_order_submission": False,
                    "public_exposure": True,
                }
            ],
        },
    )

    response = client.get("/ready")

    assert response.status_code == 503
    payload = response.json()["detail"]
    assert payload["checks"]["external_adapters_paper_only"] is False


def test_cors_preflight_responds_for_allowed_origin():
    response = client.options(
        "/api/dashboard",
        headers={
            "Origin": "http://127.0.0.1:4173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code in {200, 204}
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:4173"


def test_forced_observability_error_endpoint_returns_500():
    isolated = TestClient(app, raise_server_exceptions=False)
    response = isolated.get("/api/_test/raise")
    assert response.status_code == 500


def test_audit_log_records_auth_key_and_checkout_events():
    token, org_id = _signup_and_login("phase8")

    created = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "phase8"},
    )
    assert created.status_code == 201

    revoked = client.delete(
        f"/api/v1/saas/api-keys/{created.json()['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert revoked.status_code == 200

    checkout = client.post(
        "/api/v1/billing/checkout",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_code": "pro"},
    )
    assert checkout.status_code == 200

    response = client.get("/api/v1/saas/audit-log", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["org_id"] == org_id
    assert payload["total"] >= 5
    actions = {event["action"] for event in payload["events"]}
    assert {
        "auth.signup",
        "auth.login",
        "api_key.created",
        "api_key.revoked",
        "billing.checkout.created",
    }.issubset(actions)

    with SessionLocal() as db:
        rows = db.query(AuditLog).filter(AuditLog.org_id == org_id).all()
        assert len(rows) >= 5
        assert any(row.after and row.after.get("prefix") for row in rows)


def test_audit_log_redacts_sensitive_before_after_context():
    token, org_id = _signup_and_login("audit-redaction")
    sensitive_value = "must-not-leak-audit-secret"

    with SessionLocal() as db:
        record_audit_log(
            db,
            org_id=org_id,
            action="test.redaction",
            target_type="test",
            target_id="audit-redaction",
            before={
                "api_key": sensitive_value,
                "geminiApiKey": sensitive_value,
                "nested": {"Authorization": f"Bearer {sensitive_value}", "safe": "before"},
            },
            after={
                "password": sensitive_value,
                "private key": sensitive_value,
                "items": [{"refresh-token": sensitive_value, "safe": "after"}],
            },
        )
        db.commit()

    response = client.get("/api/v1/saas/audit-log", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert sensitive_value not in response.text
    event = next(event for event in response.json()["events"] if event["action"] == "test.redaction")
    assert event["before"] == {
        "api_key": REDACTED_VALUE,
        "geminiApiKey": REDACTED_VALUE,
        "nested": {"Authorization": REDACTED_VALUE, "safe": "before"},
    }
    assert event["after"] == {
        "password": REDACTED_VALUE,
        "private key": REDACTED_VALUE,
        "items": [{"refresh-token": REDACTED_VALUE, "safe": "after"}],
    }


def test_audit_log_pagination_bounds_remain_tenant_scoped():
    owner_token, owner_org_id = _signup_and_login("audit-owner")
    other_token, other_org_id = _signup_and_login("audit-other")
    assert owner_org_id != other_org_id

    created = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"name": "owner-key"},
    )
    assert created.status_code == 201

    revoked = client.delete(
        f"/api/v1/saas/api-keys/{created.json()['id']}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert revoked.status_code == 200

    checkout = client.post(
        "/api/v1/billing/checkout",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"plan_code": "pro"},
    )
    assert checkout.status_code == 200

    other_created = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"name": "other-key"},
    )
    assert other_created.status_code == 201

    response = client.get(
        "/api/v1/saas/audit-log?limit=500&offset=-10",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["org_id"] == owner_org_id
    assert payload["limit"] == 100
    assert payload["offset"] == 0
    assert payload["total"] >= 4
    assert payload["events"]
    assert {event["org_id"] for event in payload["events"]} == {owner_org_id}
    assert "other-key" not in str(payload["events"])

    second_page = client.get(
        "/api/v1/saas/audit-log?limit=2&offset=2",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    assert second_page.status_code == 200
    second_payload = second_page.json()
    assert second_payload["limit"] == 2
    assert second_payload["offset"] == 2
    assert second_payload["total"] == payload["total"]
    assert len(second_payload["events"]) <= 2
    assert {event["org_id"] for event in second_payload["events"]} <= {owner_org_id}


def test_optional_skipped_providers_do_not_block_capability_readiness() -> None:
    now = datetime(2026, 5, 7, tzinfo=UTC)
    contract = {
        "mode": "paper_only_read_through",
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "providers": [
            {
                "name": "goplus",
                "capabilities": ["security_audit"],
                "requires_api_key": False,
                "api_key_configured": True,
                "read_only": True,
                "supports_order_submission": False,
                "mainnet_order_submission": False,
                "public_exposure": False,
            },
            {
                "name": "tokensniffer",
                "capabilities": ["security_audit"],
                "requires_api_key": True,
                "api_key_configured": False,
                "read_only": True,
                "supports_order_submission": False,
                "mainnet_order_submission": False,
                "public_exposure": False,
            },
            {
                "name": "dexscreener",
                "capabilities": ["market_data"],
                "requires_api_key": False,
                "api_key_configured": True,
                "read_only": True,
                "supports_order_submission": False,
                "mainnet_order_submission": False,
                "public_exposure": False,
            },
            {
                "name": "birdeye",
                "capabilities": ["market_data"],
                "requires_api_key": True,
                "api_key_configured": False,
                "read_only": True,
                "supports_order_submission": False,
                "mainnet_order_submission": False,
                "public_exposure": False,
            },
        ],
    }
    health = {
        "goplus": {"status": "ok", "observed_at": "2026-05-07T00:00:00Z"},
        "tokensniffer": {"status": "skipped", "observed_at": "2026-05-07T00:00:00Z"},
        "dexscreener": {"status": "ok", "observed_at": "2026-05-07T00:00:00Z"},
        "birdeye": {"status": "skipped", "observed_at": "2026-05-07T00:00:00Z"},
    }

    readiness = build_provider_readiness(contract=contract, health=health, now=now)

    assert readiness["status"] == "healthy"
    assert readiness["paper_only"] is True
    assert readiness["capabilities"] == {
        "market_data": {"status": "healthy"},
        "security_audit": {"status": "healthy"},
    }
    providers = {provider["name"]: provider for provider in readiness["providers"]}
    assert providers["birdeye"]["status"] == "skipped"
    assert providers["birdeye"]["last_error"] == "missing API key"
    assert providers["tokensniffer"]["status"] == "skipped"


def test_skipped_only_capability_remains_not_ready() -> None:
    now = datetime(2026, 5, 7, tzinfo=UTC)
    contract = {
        "mode": "paper_only_read_through",
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "providers": [
            {
                "name": "tokensniffer",
                "capabilities": ["security_audit"],
                "requires_api_key": True,
                "api_key_configured": False,
                "read_only": True,
                "supports_order_submission": False,
                "mainnet_order_submission": False,
                "public_exposure": False,
            }
        ],
    }

    readiness = build_provider_readiness(contract=contract, health={}, now=now)

    assert readiness["status"] == "not_ready"
    assert readiness["capabilities"] == {"security_audit": {"status": "not_ready"}}
    assert readiness["providers"][0]["status"] == "skipped"


def test_explicit_empty_provider_health_does_not_fall_back_to_global_state() -> None:
    now = datetime(2026, 5, 7, tzinfo=UTC)
    record_provider_status("goplus", status="ok")
    contract = {
        "mode": "paper_only_read_through",
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "providers": [
            {
                "name": "goplus",
                "capabilities": ["security_audit"],
                "requires_api_key": False,
                "api_key_configured": True,
                "read_only": True,
                "supports_order_submission": False,
                "mainnet_order_submission": False,
                "public_exposure": False,
            }
        ],
    }

    readiness = build_provider_readiness(contract=contract, health={}, now=now)

    assert readiness["status"] == "not_ready"
    assert readiness["capabilities"] == {"security_audit": {"status": "not_ready"}}
    assert readiness["providers"][0]["status"] == "unknown"
