from uuid import uuid4

from fastapi.testclient import TestClient

from tothemoon_api.auth import create_jwt_for_user, hash_password
from tothemoon_api.config import get_settings
from tothemoon_api.database import SessionLocal
from tothemoon_api.db_models import (
    ApiKey,
    AuditLog,
    CopilotProposal,
    CopilotScannerRun,
    Membership,
    Organization,
    OrgProviderCredential,
    Subscription,
    TokenDiscoveryHistory,
    User,
)
from tothemoon_api.main import app
from tothemoon_api.provider_credentials import (
    decrypt_provider_secret,
    resolve_gemini_execution_credential,
)

client = TestClient(app)
PASSWORD = "correct horse battery staple"


def _email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid4().hex}@example.com"


def _signup(email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/signup", json={"email": email, "password": password})


def _login(email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", data={"username": email, "password": password})


def _token(email: str | None = None) -> str:
    email = email or _email()
    assert _signup(email).status_code == 201
    response = _login(email)
    assert response.status_code == 200
    return response.json()["access_token"]


def _api_key(token: str | None = None) -> dict[str, object]:
    token = token or _token()
    return _api_key_with_scopes(token, None)


def _api_key_with_scopes(token: str, scopes: list[str] | None) -> dict[str, object]:
    payload: dict[str, object] = {"name": "default"}
    if scopes is not None:
        payload["scopes"] = scopes
    response = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert response.status_code == 201
    return response.json()


def _member_token(org_id: int, role: str = "member") -> str:
    with SessionLocal() as db:
        user = User(email=_email("member"), password_hash=hash_password(PASSWORD))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, org_id=org_id, role=role))
        db.commit()
        db.refresh(user)
        return create_jwt_for_user(user)


def _set_membership_role(email: str, role: str) -> int:
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        membership = db.query(Membership).filter(Membership.user_id == user.id).one()
        membership.role = role
        db.commit()
        return membership.org_id


def test_signup_creates_user():
    email = _email()
    response = _signup(email)
    assert response.status_code == 201
    assert response.json()["email"] == email
    assert response.json()["plan"] == "free"
    assert response.json()["org_id"]
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        assert db.query(Membership).filter(Membership.user_id == user.id).count() == 1
        assert (
            db.query(Organization).filter(Organization.id == response.json()["org_id"]).count() == 1
        )


def test_signup_rejects_short_password():
    response = _signup(_email(), "short")
    assert response.status_code == 422


def test_signup_rejects_invalid_email():
    response = _signup("not-an-email")
    assert response.status_code == 422


def test_login_returns_jwt():
    email = _email()
    assert _signup(email).status_code == 201
    response = _login(email)
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]


def test_account_starts_without_gemini_credential():
    token = _token(_email("gemini-empty"))

    response = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    gemini = response.json()["provider_credentials"]["gemini"]
    assert gemini == {"configured": False, "provider": "gemini"}


def test_user_can_save_update_and_delete_gemini_credential():
    token = _token(_email("gemini-save"))

    created = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
        json={"api_key": "AIzaSyTestGeminiKey1234567890", "model": "gemini-3-flash-preview"},
    )
    assert created.status_code == 200
    credential = created.json()["credential"]
    assert credential["configured"] is True
    assert credential["provider"] == "gemini"
    assert credential["key_hint"] == "AIzaSy...7890"
    assert "api_key" not in credential

    with SessionLocal() as db:
        row = (
            db.query(OrgProviderCredential)
            .filter(OrgProviderCredential.org_id == int(created.json()["org_id"]))
            .one()
        )
        assert row.encrypted_api_key != "AIzaSyTestGeminiKey1234567890"
        assert decrypt_provider_secret(row) == "AIzaSyTestGeminiKey1234567890"

    account = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200
    assert account.json()["provider_credentials"]["gemini"]["configured"] is True

    updated = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
        json={"api_key": "AIzaSySecondGeminiKey987654321", "model": "gemini-3-pro-preview"},
    )
    assert updated.status_code == 200
    assert updated.json()["credential"]["model"] == "gemini-3-pro-preview"
    assert updated.json()["credential"]["key_hint"] == "AIzaSy...4321"
    with SessionLocal() as db:
        assert (
            db.query(OrgProviderCredential)
            .filter(OrgProviderCredential.org_id == int(created.json()["org_id"]))
            .count()
            == 1
        )

    deleted = client.delete(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["credential"]["configured"] is False
    with SessionLocal() as db:
        assert (
            db.query(OrgProviderCredential)
            .filter(OrgProviderCredential.org_id == int(created.json()["org_id"]))
            .count()
            == 0
        )


def test_provider_credential_route_accepts_gemini_api_key_alias():
    token = _token(_email("gemini-provider-alias"))
    secret = "provider-alias-gemini-key-1234567890"

    response = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
        json={"gemini_api_key": secret, "model": "gemini-3-flash-preview"},
    )

    assert response.status_code == 200
    credential = response.json()["credential"]
    assert credential["configured"] is True
    assert credential["key_hint"] == "provid...7890"
    assert secret not in response.text

    account = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200
    assert secret not in account.text
    assert account.json()["provider_credentials"]["gemini"]["key_hint"] == "provid...7890"


def test_copilot_bot_status_exposes_secret_safe_readiness(monkeypatch):
    token = _token(_email("copilot-status"))
    api_key = _api_key(token)
    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
        json={"api_key": "status-gemini-key-1234567890", "model": "gemini-3-flash-preview"},
    )
    assert saved.status_code == 200
    monkeypatch.setattr(
        "tothemoon_api.routers.copilot.build_provider_readiness",
        lambda contract: {
            "status": "healthy",
            "paper_only": True,
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": [],
            "capabilities": {},
        },
    )

    response = client.get(
        "/api/v1/copilot/bot/status",
        headers={"X-API-Key": str(api_key["plaintext"])},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["readiness"] == {"ready": True, "blocking_reasons": [], "paper_only": True}
    assert payload["order_submission_enabled"] is False
    assert payload["mainnet_order_submission_enabled"] is False
    assert payload["providers"]["gemini"]["source"] == "organization"
    assert payload["providers"]["gemini"]["key_hint"] == "status...7890"
    assert "status-gemini-key-1234567890" not in response.text


def test_copilot_bot_status_reports_blocking_reasons(monkeypatch):
    token = _token(_email("copilot-blocked"))
    api_key = _api_key(token)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_USE_VERTEXAI", "false")
    monkeypatch.setattr(
        "tothemoon_api.routers.copilot.build_provider_readiness",
        lambda contract: {
            "status": "not_ready",
            "paper_only": True,
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": [],
            "capabilities": {},
        },
    )

    response = client.get(
        "/api/v1/copilot/bot/status",
        headers={"X-API-Key": str(api_key["plaintext"])},
    )

    assert response.status_code == 200
    assert response.json()["readiness"] == {
        "ready": False,
        "blocking_reasons": ["gemini_not_configured", "provider_readiness_not_healthy"],
        "paper_only": True,
    }


def test_copilot_bot_status_blocks_unsafe_adapter_contract(monkeypatch):
    token = _token(_email("copilot-unsafe-contract"))
    api_key = _api_key(token)
    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
        json={"api_key": "unsafe-contract-gemini-key-1234567890"},
    )
    assert saved.status_code == 200
    monkeypatch.setattr(
        "tothemoon_api.routers.copilot.get_external_adapter_contract",
        lambda: {
            "mode": "paper_only_read_through",
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": [
                {
                    "name": "unsafe_public_feed",
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
    monkeypatch.setattr(
        "tothemoon_api.routers.copilot.build_provider_readiness",
        lambda contract: {
            "status": "healthy",
            "paper_only": False,
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": [],
            "capabilities": {},
        },
    )

    response = client.get(
        "/api/v1/copilot/bot/status",
        headers={"X-API-Key": str(api_key["plaintext"])},
    )

    assert response.status_code == 200
    assert response.json()["safeguards"]["adapter_contract_enforced"] is False
    assert response.json()["readiness"] == {
        "ready": False,
        "blocking_reasons": ["external_adapter_contract_unsafe"],
        "paper_only": True,
    }
    assert "unsafe-contract-gemini-key-1234567890" not in response.text


def test_org_member_cannot_save_or_delete_gemini_credential():
    owner_token = _token(_email("gemini-owner"))
    created = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"api_key": "owner-gemini-key-1234567890", "model": "gemini-3-flash-preview"},
    )
    assert created.status_code == 200
    org_id = int(created.json()["org_id"])
    member_token = _member_token(org_id)

    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {member_token}"},
        json={"api_key": "member-gemini-key-1234567890", "model": "gemini-3-pro-preview"},
    )
    deleted = client.delete(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert saved.status_code == 403
    assert saved.json()["detail"] == "Tenant admin role required"
    assert deleted.status_code == 403
    assert deleted.json()["detail"] == "Tenant admin role required"
    with SessionLocal() as db:
        credential = (
            db.query(OrgProviderCredential)
            .filter(OrgProviderCredential.org_id == org_id)
            .one()
        )
        assert decrypt_provider_secret(credential) == "owner-gemini-key-1234567890"


def test_provider_credential_rejections_are_audited_without_secrets():
    owner_token = _token(_email("gemini-rejection-owner"))
    created = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"api_key": "owner-rejection-gemini-key-1234567890", "model": "gemini-3-flash-preview"},
    )
    assert created.status_code == 200
    org_id = int(created.json()["org_id"])
    member_token = _member_token(org_id)
    rejected_secret = "member-rejection-gemini-key-1234567890"

    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {member_token}"},
        json={"api_key": rejected_secret, "model": "gemini-3-pro-preview"},
    )
    deleted = client.delete(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert saved.status_code == 403
    assert deleted.status_code == 403
    with SessionLocal() as db:
        events = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == org_id,
                AuditLog.action.in_(
                    [
                        "provider_credential.upsert_rejected",
                        "provider_credential.delete_rejected",
                    ]
                ),
            )
            .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
            .all()
        )
        assert [event.action for event in events] == [
            "provider_credential.upsert_rejected",
            "provider_credential.delete_rejected",
        ]
        assert {event.target_id for event in events} == {"gemini"}
        assert {event.after["reason"] for event in events} == {"tenant_admin_required"}
        assert {event.after["status_code"] for event in events} == {403}
        assert rejected_secret not in str([event.after for event in events])


def test_provider_credential_missing_delete_is_audited_per_tenant():
    owner_token = _token(_email("gemini-delete-missing"))
    other_token = _token(_email("gemini-delete-other"))
    other_saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"api_key": "other-delete-gemini-key-1234567890", "model": "gemini-3-flash-preview"},
    )
    assert other_saved.status_code == 200

    missing = client.delete(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "Gemini credential not configured"
    with SessionLocal() as db:
        owner_user = db.query(User).filter(User.email.like("gemini-delete-missing-%")).one()
        owner_org_id = db.query(Membership).filter(Membership.user_id == owner_user.id).one().org_id
        other_org_id = int(other_saved.json()["org_id"])
        event = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == owner_org_id,
                AuditLog.action == "provider_credential.delete_rejected",
            )
            .one()
        )
        assert owner_org_id != other_org_id
        assert event.after == {
            "provider": "gemini",
            "status_code": 404,
            "detail": "Gemini credential not configured",
            "reason": "not_configured",
        }
        assert (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == other_org_id,
                AuditLog.action == "provider_credential.delete_rejected",
            )
            .count()
            == 0
        )


def test_org_member_cannot_mutate_gemini_credential_through_compatibility_routes():
    owner_token = _token(_email("gemini-compat-owner"))
    created = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"api_key": "owner-compat-gemini-key-1234567890", "model": "gemini-3-flash-preview"},
    )
    assert created.status_code == 200
    org_id = int(created.json()["org_id"])
    member_token = _member_token(org_id)
    save_routes = [
        "/api/v1/account/settings",
        "/api/v1/saas/account/settings",
        "/api/v1/saas/settings",
        "/api/v1/settings",
        "/api/v1/providers/gemini/credential",
        "/api/v1/credentials/gemini",
        "/api/v1/saas/credentials/gemini",
    ]
    delete_routes = [
        "/api/v1/providers/gemini/credential",
        "/api/v1/credentials/gemini",
        "/api/v1/saas/credentials/gemini",
        "/api/v1/saas/provider-credentials/gemini",
    ]

    for route in save_routes:
        saved = client.post(
            route,
            headers={"Authorization": f"Bearer {member_token}"},
            json={"gemini_api_key": "member-compat-gemini-key-1234567890"},
        )

        assert saved.status_code == 403
        assert saved.json()["detail"] == "Tenant admin role required"

    for route in delete_routes:
        deleted = client.delete(route, headers={"Authorization": f"Bearer {member_token}"})

        assert deleted.status_code == 403
        assert deleted.json()["detail"] == "Tenant admin role required"

    with SessionLocal() as db:
        credential = db.query(OrgProviderCredential).filter_by(org_id=org_id).one()
        assert decrypt_provider_secret(credential) == "owner-compat-gemini-key-1234567890"


def test_org_member_cannot_list_provider_credential_status():
    owner_token = _token(_email("gemini-list-owner"))
    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"api_key": "owner-list-gemini-key-1234567890"},
    )
    assert saved.status_code == 200
    org_id = int(saved.json()["org_id"])
    member_token = _member_token(org_id)

    response = client.get(
        "/api/v1/saas/provider-credentials",
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Tenant admin role required"
    assert "owner-list-gemini-key-1234567890" not in response.text


def test_org_admin_can_list_provider_credential_status():
    owner_token = _token(_email("gemini-list-admin-owner"))
    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"api_key": "admin-list-gemini-key-1234567890"},
    )
    assert saved.status_code == 200
    admin_token = _member_token(int(saved.json()["org_id"]), role="admin")

    response = client.get(
        "/api/v1/saas/provider-credentials",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    assert response.json()["provider_credentials"]["gemini"]["key_hint"] == "admin-...7890"
    assert "admin-list-gemini-key-1234567890" not in response.text


def test_org_admin_can_save_and_delete_gemini_credential():
    email = _email("gemini-admin")
    token = _token(email)
    _set_membership_role(email, "admin")

    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
        json={"api_key": "admin-gemini-key-1234567890", "model": "gemini-3-flash-preview"},
    )
    deleted = client.delete(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert saved.status_code == 200
    assert saved.json()["credential"]["key_hint"] == "admin-...7890"
    assert deleted.status_code == 200
    assert deleted.json()["credential"] == {"configured": False, "provider": "gemini"}


def test_gemini_execution_credential_repr_does_not_expose_api_key(monkeypatch):
    token = _token(_email("gemini-execution-secret"))
    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
        json={"api_key": "org-secret-gemini-key-987654", "model": "gemini-3-pro-preview"},
    )
    assert saved.status_code == 200
    monkeypatch.setenv("GEMINI_API_KEY", "global-secret-gemini-key")
    account = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200

    with SessionLocal() as db:
        org_id = (
            db.query(OrgProviderCredential)
            .filter(OrgProviderCredential.org_id == int(account.json()["org_id"]))
            .one()
            .org_id
        )
        resolved = resolve_gemini_execution_credential(db, org_id, get_settings())

    assert resolved.source == "organization"
    assert resolved.api_key == "org-secret-gemini-key-987654"
    assert resolved.key_hint == "org-se...7654"
    assert "org-secret-gemini-key-987654" not in repr(resolved)
    assert "global-secret-gemini-key" not in repr(resolved)


def test_user_can_save_gemini_credential_through_settings_routes():
    token = _token(_email("gemini-settings-save"))
    secret = "settings-gemini-key-1234567890"
    routes = [
        "/api/v1/account/settings",
        "/api/v1/saas/account/settings",
        "/api/v1/saas/settings",
        "/api/v1/settings",
        "/api/v1/providers/gemini/credential",
        "/api/v1/credentials/gemini",
        "/api/v1/saas/credentials/gemini",
    ]

    for route in routes:
        response = client.post(
            route,
            headers={"Authorization": f"Bearer {token}"},
            json={"gemini_api_key": secret, "model": "gemini-3-flash-preview"},
        )

        assert response.status_code == 200
        assert secret not in response.text
        credential = response.json()["credential"]
        assert credential["configured"] is True
        assert credential["provider"] == "gemini"
        assert credential["key_hint"] == "settin...7890"
        assert "api_key" not in credential
        assert "gemini_api_key" not in credential

    account = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200
    assert secret not in account.text
    gemini = account.json()["provider_credentials"]["gemini"]
    assert gemini["configured"] is True
    assert gemini["key_hint"] == "settin...7890"
    assert "api_key" not in gemini
    assert "gemini_api_key" not in gemini


def test_gemini_credential_model_is_trimmed_before_save():
    token = _token(_email("gemini-model-trim"))
    response = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
        json={"api_key": "trim-gemini-key-1234567890", "model": "  gemini-3-pro-preview  "},
    )

    assert response.status_code == 200
    assert response.json()["credential"]["model"] == "gemini-3-pro-preview"


def test_gemini_credential_api_key_is_trimmed_before_save():
    token = _token(_email("gemini-key-trim"))
    secret = "trimmed-gemini-key-1234567890"
    response = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
        json={"api_key": f"  {secret}\n", "model": "gemini-3-flash-preview"},
    )

    assert response.status_code == 200
    credential = response.json()["credential"]
    assert credential["key_hint"] == "trimme...7890"
    assert "api_key" not in credential
    assert secret not in response.text

    account = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200
    gemini = account.json()["provider_credentials"]["gemini"]
    assert gemini["key_hint"] == "trimme...7890"
    assert "api_key" not in gemini
    assert secret not in account.text

    with SessionLocal() as db:
        row = (
            db.query(OrgProviderCredential)
            .filter(OrgProviderCredential.org_id == int(response.json()["org_id"]))
            .one()
        )
        assert decrypt_provider_secret(row) == secret


def test_gemini_credential_rejects_blank_model():
    token = _token(_email("gemini-model-blank"))
    response = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
        json={"api_key": "blank-model-gemini-key-1234567890", "model": "   "},
    )

    assert response.status_code == 422
    assert "Gemini model must not be blank" in response.text

    account = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200
    assert account.json()["provider_credentials"]["gemini"] == {
        "configured": False,
        "provider": "gemini",
    }


def test_user_cannot_save_masked_gemini_credential_hint():
    token = _token(_email("gemini-masked-hint"))
    for api_key in ("AIzaSy...7890", "AIzaSy\u20267890", "AIzaSy•••7890"):
        response = client.post(
            "/api/v1/saas/provider-credentials/gemini",
            headers={"Authorization": f"Bearer {token}"},
            json={"api_key": api_key, "model": "gemini-3-flash-preview"},
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "Cole a Gemini API key completa, nao o hint mascarado."

    account = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200
    assert account.json()["provider_credentials"]["gemini"] == {"configured": False, "provider": "gemini"}


def test_user_cannot_save_malformed_gemini_credential():
    token = _token(_email("gemini-malformed"))
    for api_key in ("     short     ", "AIzaSy invalid 7890"):
        response = client.post(
            "/api/v1/saas/provider-credentials/gemini",
            headers={"Authorization": f"Bearer {token}"},
            json={"api_key": api_key, "model": "gemini-3-flash-preview"},
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "Cole uma credencial Gemini valida."

    account = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200
    assert account.json()["provider_credentials"]["gemini"] == {"configured": False, "provider": "gemini"}


def test_settings_routes_reject_masked_gemini_credential_hint():
    token = _token(_email("gemini-masked-settings"))
    routes = [
        "/api/v1/account/settings",
        "/api/v1/saas/account/settings",
        "/api/v1/saas/settings",
        "/api/v1/settings",
        "/api/v1/providers/gemini/credential",
        "/api/v1/credentials/gemini",
        "/api/v1/saas/credentials/gemini",
    ]

    for route in routes:
        response = client.post(
            route,
            headers={"Authorization": f"Bearer {token}"},
            json={"gemini_api_key": "AIzaSy...7890", "model": "gemini-3-flash-preview"},
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "Cole a Gemini API key completa, nao o hint mascarado."

    account = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200
    assert account.json()["provider_credentials"]["gemini"] == {"configured": False, "provider": "gemini"}


def test_user_can_delete_gemini_credential_through_compatibility_routes():
    token = _token(_email("gemini-settings-delete"))
    routes = [
        "/api/v1/providers/gemini/credential",
        "/api/v1/credentials/gemini",
        "/api/v1/saas/credentials/gemini",
        "/api/v1/saas/provider-credentials/gemini",
    ]

    for index, route in enumerate(routes):
        saved = client.post(
            "/api/v1/saas/provider-credentials/gemini",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "api_key": f"compat-delete-gemini-key-{index}-1234567890",
                "model": "gemini-3-flash-preview",
            },
        )
        assert saved.status_code == 200

        deleted = client.delete(route, headers={"Authorization": f"Bearer {token}"})

        assert deleted.status_code == 200
        assert deleted.json()["credential"] == {"configured": False, "provider": "gemini"}


def test_gemini_settings_routes_require_authentication():
    routes = [
        "/api/v1/account/settings",
        "/api/v1/saas/account/settings",
        "/api/v1/saas/settings",
        "/api/v1/settings",
        "/api/v1/providers/gemini/credential",
        "/api/v1/credentials/gemini",
        "/api/v1/saas/credentials/gemini",
    ]

    for route in routes:
        response = client.post(
            route,
            json={"gemini_api_key": "unauthenticated-gemini-key"},
        )

        assert response.status_code == 401


def test_gemini_delete_routes_require_authentication():
    routes = [
        "/api/v1/providers/gemini/credential",
        "/api/v1/credentials/gemini",
        "/api/v1/saas/credentials/gemini",
        "/api/v1/saas/provider-credentials/gemini",
    ]

    for route in routes:
        response = client.delete(route)

        assert response.status_code == 401


def test_user_cannot_see_other_org_gemini_credential():
    token_a = _token(_email("gemini-a"))
    token_b = _token(_email("gemini-b"))
    assert (
        client.post(
            "/api/v1/saas/provider-credentials/gemini",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"api_key": "AIzaSyTenantAOnly123456", "model": "gemini-3-flash-preview"},
        ).status_code
        == 200
    )

    response_b = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token_b}"})

    assert response_b.status_code == 200
    assert response_b.json()["provider_credentials"]["gemini"]["configured"] is False


def test_account_data_export_requires_authentication():
    response = client.get("/api/v1/saas/account/export")

    assert response.status_code == 401


def test_account_data_export_is_scoped_and_secret_safe():
    token_a = _token(_email("export-a"))
    token_b = _token(_email("export-b"))
    key_a = _api_key(token_a)
    key_b = _api_key(token_b)
    gemini_secret = "AIzaSyExportSecret1234567890"
    assert (
        client.post(
            "/api/v1/saas/provider-credentials/gemini",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"api_key": gemini_secret, "model": "gemini-3-flash-preview"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/saas/token-discovery-history",
            headers={"Authorization": f"Bearer {token_a}"},
            json={
                "token_address": "0xEXPORTA",
                "chain": "Base",
                "symbol": "EXPA",
                "metadata": {"api_key": "must-not-export", "note": "safe"},
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/saas/token-discovery-history",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"token_address": "0xEXPORTB", "chain": "Base", "symbol": "EXPB"},
        ).status_code
        == 201
    )
    with SessionLocal() as db:
        db.add(
            Subscription(
                org_id=key_a["org_id"],
                provider="stripe",
                status="active",
                provider_customer_id="cus_export_owner_secret",
                provider_subscription_id="sub_export_owner_secret",
            )
        )
        db.add(
            AuditLog(
                org_id=key_a["org_id"],
                actor_type="system",
                action="legacy.secret_payload",
                target_type="legacy",
                before={
                    "api_key": "must-not-export",
                    "circle_api_key": "must-not-export",
                    "safe": "before",
                },
                after={
                    "nested": {
                        "authorization": "must-not-export",
                        "client_secret": "must-not-export",
                        "safe": "after",
                    }
                },
            )
        )
        db.add(
            CopilotProposal(
                org_id=key_a["org_id"],
                api_key_id=key_a["id"],
                token_address="0xCOPILOTEXPORT",
                chain="base",
                symbol="CPEX",
                side="BUY",
                amount_usd=25,
                score=88,
                rationale="Export secret redaction regression coverage.",
                execution_payload={
                    "route": "paper",
                    "api_key": "must-not-export",
                    "nested": {"authorization": "must-not-export", "safe": "kept"},
                },
            )
        )
        db.commit()

    response = client.get(
        "/api/v1/saas/account/export",
        headers={"Authorization": f"Bearer {token_a}"},
    )

    assert response.status_code == 200
    payload = response.json()
    serialized = response.text
    assert payload["schema_version"] == 1
    assert payload["account"]["email"].startswith("export-a-")
    assert {row["token_address"] for row in payload["token_discovery_history"]} == {"0xEXPORTA"}
    assert payload["token_discovery_history"][0]["metadata"] == {"note": "safe"}
    legacy_audit = next(row for row in payload["audit_logs"] if row["action"] == "legacy.secret_payload")
    assert legacy_audit["before"] == {"safe": "before"}
    assert legacy_audit["after"] == {"nested": {"safe": "after"}}
    proposal = next(
        row for row in payload["copilot_proposals"] if row["token_address"] == "0xCOPILOTEXPORT"
    )
    assert proposal["execution_payload"] == {"route": "paper", "nested": {"safe": "kept"}}
    assert payload["subscriptions"] == [
        {
            "id": payload["subscriptions"][0]["id"],
            "org_id": payload["organization"]["id"],
            "status": "active",
            "provider": "stripe",
            "current_period_end": None,
            "created_at": payload["subscriptions"][0]["created_at"],
            "updated_at": payload["subscriptions"][0]["updated_at"],
            "provider_customer_id_hint": "cus_...cret",
            "provider_subscription_id_hint": "sub_...cret",
        }
    ]
    assert [row["prefix"] for row in payload["api_keys"]] == [key_a["prefix"]]
    assert payload["provider_credentials"] == [
        {
            "configured": True,
            "provider": "gemini",
            "key_hint": "AIzaSy...7890",
            "model": "gemini-3-flash-preview",
            "id": payload["provider_credentials"][0]["id"],
            "org_id": payload["organization"]["id"],
            "created_at": payload["provider_credentials"][0]["created_at"],
            "updated_at": payload["provider_credentials"][0]["updated_at"],
        }
    ]
    assert key_a["plaintext"] not in serialized
    assert key_b["plaintext"] not in serialized
    assert gemini_secret not in serialized
    assert "encrypted_api_key" not in serialized
    assert "key_hash" not in serialized
    assert "must-not-export" not in serialized
    assert "cus_export_owner_secret" not in serialized
    assert "sub_export_owner_secret" not in serialized
    assert "0xEXPORTB" not in serialized


def test_account_data_export_requires_tenant_admin_role():
    owner_email = _email("export-owner")
    owner_token = _token(owner_email)
    org_id = _set_membership_role(owner_email, "owner")
    member_token = _member_token(org_id, "member")

    response = client.get(
        "/api/v1/saas/account/export",
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert owner_token
    assert response.status_code == 403
    assert response.json()["detail"] == "Tenant admin role required"


def test_account_data_export_includes_tenant_scoped_rejected_copilot_audit():
    token_a = _token(_email("export-rejected-copilot-a"))
    token_b = _token(_email("export-rejected-copilot-b"))
    key_a = _api_key(token_a)
    key_b = _api_key(token_b)

    created_a = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": key_a["plaintext"]},
        json={
            "token_address": "0xREJECTEXPORTA",
            "chain": "base",
            "symbol": "RXA",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Tenant A rejection should be exported.",
        },
    )
    created_b = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": key_b["plaintext"]},
        json={
            "token_address": "0xREJECTEXPORTB",
            "chain": "base",
            "symbol": "RXB",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Tenant B rejection must stay isolated.",
        },
    )
    assert created_a.status_code == 201
    assert created_b.status_code == 201
    proposal_a_id = created_a.json()["proposal"]["id"]
    proposal_b_id = created_b.json()["proposal"]["id"]

    rejected_a = client.post(
        f"/api/v1/copilot/proposals/{proposal_a_id}/reject",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"reason": "Rejected for export audit evidence."},
    )
    rejected_b = client.post(
        f"/api/v1/copilot/proposals/{proposal_b_id}/reject",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"reason": "Other tenant rejection."},
    )
    assert rejected_a.status_code == 200
    assert rejected_b.status_code == 200

    response = client.get(
        "/api/v1/saas/account/export",
        headers={"Authorization": f"Bearer {token_a}"},
    )

    assert response.status_code == 200
    payload = response.json()
    serialized = response.text
    proposal = next(
        row for row in payload["copilot_proposals"] if row["id"] == proposal_a_id
    )
    assert proposal["org_id"] == key_a["org_id"]
    assert proposal["status"] == "rejected"
    assert proposal["execution_payload"] == {
        "rejection": {
            "reason": "Rejected for export audit evidence.",
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
        }
    }
    rejection_audit = next(
        row for row in payload["audit_logs"] if row["action"] == "copilot.proposal.rejected"
    )
    assert rejection_audit["target_id"] == str(proposal_a_id)
    assert rejection_audit["after"] == {
        "status": "rejected",
        "scanner_run_id": None,
        "rejection_reason": "Rejected for export audit evidence.",
        "execution": "none",
    }
    assert "0xREJECTEXPORTB" not in serialized
    assert "Other tenant rejection." not in serialized
    assert key_a["plaintext"] not in serialized
    assert key_b["plaintext"] not in serialized


def test_paper_auto_scanner_marks_successful_run_completed(monkeypatch):
    token = _token(_email("scanner-completed"))
    _api_key_with_scopes(token, ["copilot:proposals", "simulate:order"])

    monkeypatch.setattr(
        "tothemoon_api.routers.copilot._scan_market_candidates",
        lambda payload: [
            {
                "token_address": "0xSAFE",
                "chain": "base",
                "symbol": "SAFE",
                "volume_24h": 250_000,
                "momentum_h1": 8.0,
                "liquidity_usd": 125_000,
                "price_usd": 1.0,
            }
        ],
    )
    monkeypatch.setattr(
        "tothemoon_api.routers.copilot.get_token_security_audit",
        lambda token_address, chain: {
            "token_address": token_address,
            "is_honeypot": False,
            "risk_score": 10,
            "providers": ["test_provider"],
        },
    )

    response = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "SAFE", "execution_mode": "paper_auto", "amount_usd": "25"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["scanner_run"]["status"] == "completed"
    assert payload["scanner_run"]["candidate_count"] == 1
    assert payload["scanner_run"]["paper_executed_count"] == 1
    assert payload["created"][0]["status"] == "executed"

    with SessionLocal() as db:
        run = db.query(CopilotScannerRun).filter(CopilotScannerRun.id == payload["scanner_run"]["id"]).one()
        assert run.status == "completed"
        assert run.completed_at is not None


def test_login_rejects_wrong_password():
    email = _email()
    assert _signup(email).status_code == 201
    response = _login(email, "wrong password value")
    assert response.status_code == 401


def test_login_rejects_inactive_user():
    email = _email()
    assert _signup(email).status_code == 201
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        user.is_active = False
        db.commit()

    response = _login(email)
    assert response.status_code == 401


def test_inactive_user_jwt_rejected():
    email = _email()
    token = _token(email)
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        user.is_active = False
        db.commit()

    response = client.get("/api/v1/saas/account", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_create_api_key_requires_jwt():
    response = client.post("/api/v1/saas/api-keys", json={"name": "missing-token"})
    assert response.status_code == 401


def test_free_plan_allows_only_one_active_api_key():
    token = _token()
    created = _api_key(token)

    second = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "second"},
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "Active API key limit reached"

    revoked = client.delete(
        f"/api/v1/saas/api-keys/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert revoked.status_code == 200

    replacement = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "replacement"},
    )
    assert replacement.status_code == 201


def test_api_key_plaintext_returned_only_once():
    token = _token()
    created = _api_key(token)
    assert str(created["plaintext"]).startswith("ttm_sk_live_")
    assert created["scopes"] == "copilot:proposals simulate:order tokens:audit"
    listed = client.get("/api/v1/saas/api-keys", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    assert "plaintext" not in listed.json()[0]


def test_billing_and_payment_api_key_scopes_are_enforced():
    token = _token(_email("billing-payment-scopes"))
    audit_only = _api_key_with_scopes(token, ["tokens:audit"])

    billing_denied = client.get(
        "/api/v1/billing/status",
        headers={"X-API-Key": str(audit_only["plaintext"])},
    )
    payment_denied = client.post(
        "/api/payments/intent",
        headers={"X-API-Key": str(audit_only["plaintext"])},
        json={"artifact_id": "artifact_review_bundle", "buyer_address": "0xBuyerAddress"},
    )
    assert billing_denied.status_code == 403
    assert billing_denied.json()["detail"] == "API key scope denied"
    assert payment_denied.status_code == 403
    assert payment_denied.json()["detail"] == "API key scope denied"

    client.delete(
        f"/api/v1/saas/api-keys/{audit_only['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    scoped = _api_key_with_scopes(token, ["billing:read", "billing:write", "payments:write"])

    billing_allowed = client.get(
        "/api/v1/billing/status",
        headers={"X-API-Key": str(scoped["plaintext"])},
    )
    checkout_allowed = client.post(
        "/api/v1/billing/checkout",
        headers={"X-API-Key": str(scoped["plaintext"])},
        json={"plan_code": "pro"},
    )
    payment_allowed = client.post(
        "/api/payments/intent",
        headers={"X-API-Key": str(scoped["plaintext"])},
        json={"artifact_id": "artifact_review_bundle", "buyer_address": "0xBuyerAddress"},
    )

    assert billing_allowed.status_code == 200
    assert checkout_allowed.status_code == 200
    assert payment_allowed.status_code == 200
    assert payment_allowed.json()["payment_id"]


def test_api_key_name_rejects_blank_and_trims():
    token = _token(_email("api-key-name"))

    blank = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "   "},
    )
    assert blank.status_code == 422

    created = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "  production bot  "},
    )
    assert created.status_code == 201
    assert created.json()["name"] == "production bot"

    listed = client.get("/api/v1/saas/api-keys", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "production bot"


def test_api_key_invalid_scope_rejected_before_active_key_limit():
    token = _token(_email("api-key-invalid-scope-limit"))
    created = _api_key(token)
    assert created["id"]

    response = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "invalid scoped key", "scopes": ["tokens:admin"]},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid API key scopes: tokens:admin"

    with SessionLocal() as db:
        org_id = int(created["org_id"])
        event = (
            db.query(AuditLog)
            .filter(AuditLog.org_id == org_id, AuditLog.action == "api_key.create_rejected")
            .one()
        )
        assert event.after == {
            "name": "invalid scoped key",
            "scopes": ["tokens:admin"],
            "status_code": 422,
            "detail": "Invalid API key scopes: tokens:admin",
        }


def test_api_key_hash_stored_not_plaintext():
    created = _api_key()
    plaintext = str(created["plaintext"])
    with SessionLocal() as db:
        row = db.query(ApiKey).filter(ApiKey.id == int(created["id"])).one()
        assert row.key_hash != plaintext
        assert plaintext not in row.key_hash
        assert row.org_id == int(created["org_id"])


def test_api_key_revoke_blocks_future_calls():
    token = _token()
    created = _api_key(token)
    plaintext = str(created["plaintext"])
    response = client.delete(
        f"/api/v1/saas/api-keys/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    simulated = client.post(
        "/api/v1/simulate/order",
        headers={"X-API-Key": plaintext},
        json={"token_address": "0xSAFE", "amount": 100, "side": "BUY"},
    )
    assert simulated.status_code == 403


def test_api_key_rejected_after_owner_membership_removed():
    token = _token()
    created = _api_key(token)
    plaintext = str(created["plaintext"])
    with SessionLocal() as db:
        api_key = db.query(ApiKey).filter(ApiKey.id == int(created["id"])).one()
        db.query(Membership).filter(
            Membership.user_id == api_key.user_id,
            Membership.org_id == api_key.org_id,
        ).delete()
        db.commit()

    simulated = client.post(
        "/api/v1/simulate/order",
        headers={"X-API-Key": plaintext},
        json={"token_address": "0xSAFE", "amount": 100, "side": "BUY"},
    )
    assert simulated.status_code == 403
    assert simulated.json()["detail"] == "Invalid API Key"


def test_api_key_rejected_after_owner_deactivated():
    token = _token()
    created = _api_key(token)
    plaintext = str(created["plaintext"])
    with SessionLocal() as db:
        api_key = db.query(ApiKey).filter(ApiKey.id == int(created["id"])).one()
        api_key.owner.is_active = False
        db.commit()

    simulated = client.post(
        "/api/v1/simulate/order",
        headers={"X-API-Key": plaintext},
        json={"token_address": "0xSAFE", "amount": 100, "side": "BUY"},
    )
    assert simulated.status_code == 403
    assert simulated.json()["detail"] == "Invalid API Key"


def test_simulate_order_requires_x_api_key():
    response = client.post(
        "/api/v1/simulate/order",
        json={"token_address": "0xSAFE", "amount": 100, "side": "BUY"},
    )
    assert response.status_code == 401


def test_bearer_token_does_not_authorize_simulate_order():
    token = _token()
    response = client.post(
        "/api/v1/simulate/order",
        headers={"Authorization": f"Bearer {token}"},
        json={"token_address": "0xSAFE", "amount": 100, "side": "BUY"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing X-API-Key"


def test_invalid_api_key_rejected_for_simulate_order():
    response = client.post(
        "/api/v1/simulate/order",
        headers={"X-API-Key": "ttm_sk_live_not-a-real-key"},
        json={"token_address": "0xSAFE", "amount": 100, "side": "BUY"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid API Key"


def test_last_used_at_updated_on_use():
    token = _token()
    created = _api_key(token)
    plaintext = str(created["plaintext"])

    simulated = client.post(
        "/api/v1/simulate/order",
        headers={"X-API-Key": plaintext},
        json={"token_address": "0xSAFE", "amount": 100, "side": "BUY"},
    )
    assert simulated.status_code == 200

    listed = client.get("/api/v1/saas/api-keys", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    assert listed.json()[0]["last_used_at"] is not None


def test_saas_usage_and_dashboard_reflect_paper_simulation_only():
    token = _token()
    created = _api_key(token)
    plaintext = str(created["plaintext"])

    before_usage = client.get(
        "/api/v1/saas/usage",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert before_usage.status_code == 200
    assert before_usage.json()["plan"] == "free"
    assert before_usage.json()["quota"] == {
        "simulate_order": 1000,
        "token_audit": 100,
        "active_api_keys": 1,
    }

    simulated = client.post(
        "/api/v1/simulate/order",
        headers={"X-API-Key": plaintext},
        json={"token_address": "0xSAFE", "amount": 100, "side": "BUY"},
    )
    assert simulated.status_code == 200
    assert simulated.json()["status"] == "SUCCESS"

    after_usage = client.get(
        "/api/v1/saas/usage",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert after_usage.status_code == 200
    assert after_usage.json()["requests_month"] == before_usage.json()["requests_month"] + 1

    dashboard = client.get(
        "/api/v1/saas/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert payload["active_api_keys"] == 1
    assert payload["total_simulated_trades"] == 1
    assert payload["total_volume_usd"] == 100.0


def test_signup_hashes_password():
    email = _email()
    assert _signup(email).status_code == 201
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        assert user.password_hash != PASSWORD
        assert PASSWORD not in user.password_hash


def test_user_does_not_see_other_users_api_keys():
    token_a = _token(_email("tenant-a"))
    token_b = _token(_email("tenant-b"))
    key_a = _api_key(token_a)
    key_b = _api_key(token_b)

    listed_a = client.get("/api/v1/saas/api-keys", headers={"Authorization": f"Bearer {token_a}"})
    assert listed_a.status_code == 200
    ids_a = {item["id"] for item in listed_a.json()}
    assert key_a["id"] in ids_a
    assert key_b["id"] not in ids_a

    revoked_b = client.delete(
        f"/api/v1/saas/api-keys/{key_b['id']}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert revoked_b.status_code == 404


def test_token_discovery_history_is_account_scoped_and_non_secret():
    token_a = _token(_email("history-a"))
    token_b = _token(_email("history-b"))

    created_a = client.post(
        "/api/v1/saas/token-discovery-history",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "token_address": "0xAAA",
            "chain": "Base",
            "symbol": "moon",
            "score": 91.5,
            "recommendation": "watchlist",
            "rationale": "Liquidity locked and holder concentration acceptable.",
            "metadata": {
                "liquidity_usd": 125000,
                "token_address": "0xAAA",
                "secret": "must-not-persist",
                "api_key_value": "must-not-persist",
                "gemini_api_key": "must-not-persist",
                "geminiApiKeyValue": "must-not-persist",
                "provider": {
                    "name": "gemini",
                    "api_key": "must-not-persist",
                    "apiKey": "must-not-persist",
                    "api key": "must-not-persist",
                    "geminiApiKey": "must-not-persist",
                    "gemini.api.key": "must-not-persist",
                    "nested": {
                        "access_token": "must-not-persist",
                        "access_token_hint": "must-not-persist",
                        "score": 91,
                    },
                },
                "checks": [
                    {
                        "name": "holders",
                        "Authorization": "Bearer must-not-persist",
                        "x-api-key": "must-not-persist",
                        "api_key_header": "must-not-persist",
                        "bearer_token": "must-not-persist",
                        "password": "must-not-persist",
                        "privateKey": "must-not-persist",
                        "private_key_pem": "must-not-persist",
                        "refresh-token": "must-not-persist",
                        "refresh token": "must-not-persist",
                        "status": "ok",
                    }
                ],
            },
        },
    )
    assert created_a.status_code == 201
    created_payload = created_a.json()["history"]
    assert created_payload["org_id"]
    assert created_payload["symbol"] == "MOON"
    assert created_payload["chain"] == "base"
    assert created_payload["metadata"] == {
        "liquidity_usd": 125000,
        "token_address": "0xAAA",
        "provider": {"name": "gemini", "nested": {"score": 91}},
        "checks": [{"name": "holders", "status": "ok"}],
    }

    created_b = client.post(
        "/api/v1/saas/token-discovery-history",
        headers={"Authorization": f"Bearer {token_b}"},
        json={
            "token_address": "0xBBB",
            "chain": "solana",
            "score": 47,
            "recommendation": "skip",
        },
    )
    assert created_b.status_code == 201

    listed_a = client.get(
        "/api/v1/saas/token-discovery-history",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert listed_a.status_code == 200
    history_a = listed_a.json()["history"]
    assert [item["token_address"] for item in history_a] == ["0xAAA"]
    assert listed_a.json()["total"] == 1
    assert all(item["org_id"] == created_payload["org_id"] for item in history_a)
    assert "0xBBB" not in {item["token_address"] for item in history_a}
    assert "must-not-persist" not in listed_a.text

    listed_b = client.get(
        "/api/v1/saas/token-discovery-history",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert listed_b.status_code == 200
    assert listed_b.json()["total"] == 1
    assert [item["token_address"] for item in listed_b.json()["history"]] == ["0xBBB"]

    fetched_a = client.get(
        f"/api/v1/saas/token-discovery-history/{created_payload['id']}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert fetched_a.status_code == 200
    assert fetched_a.json()["history"] == created_payload
    assert "must-not-persist" not in fetched_a.text

    cross_org_fetch = client.get(
        f"/api/v1/saas/token-discovery-history/{created_payload['id']}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert cross_org_fetch.status_code == 404
    assert cross_org_fetch.json()["detail"] == "Token discovery history record not found"


def test_token_discovery_history_requires_jwt():
    response = client.get("/api/v1/saas/token-discovery-history")
    assert response.status_code == 401

    detail_response = client.get("/api/v1/saas/token-discovery-history/1")
    assert detail_response.status_code == 401


def test_token_discovery_history_rejects_blank_required_fields():
    token = _token(_email("history-blank-fields"))

    for payload in (
        {"token_address": "   ", "chain": "base"},
        {"token_address": "0xAAA", "chain": "   "},
    ):
        response = client.post(
            "/api/v1/saas/token-discovery-history",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )

        assert response.status_code == 422


def test_token_discovery_history_normalizes_optional_blank_fields():
    token = _token(_email("history-optional-blank"))

    response = client.post(
        "/api/v1/saas/token-discovery-history",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "token_address": " 0xAAA ",
            "chain": " Base ",
            "symbol": "   ",
            "recommendation": "   ",
        },
    )

    assert response.status_code == 201
    history = response.json()["history"]
    assert history["token_address"] == "0xAAA"
    assert history["chain"] == "base"
    assert history["symbol"] is None
    assert history["recommendation"] is None


def test_token_discovery_history_response_sanitizes_nested_stored_metadata():
    email = _email("history-legacy-secret")
    token = _token(email)
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        org_id = db.query(Membership).filter(Membership.user_id == user.id).one().org_id
        db.add(
            TokenDiscoveryHistory(
                org_id=org_id,
                token_address="0xLEGACY",
                chain="base",
                metadata_json={
                    "safe": {"score": 88},
                    "token_address": "0xLEGACY",
                    "credentials": {
                        "api_key": "must-not-return",
                        "api_key_value": "must-not-return",
                        "geminiApiKeyValue": "must-not-return",
                        "source": "legacy",
                    },
                    "checks": [
                        {
                            "status": "ok",
                            "secret": "must-not-return",
                            "nested": {
                                "access_token_hint": "must-not-return",
                                "private_key": "must-not-return",
                                "private_key_pem": "must-not-return",
                                "value": 7,
                            },
                        }
                    ],
                    "password": "must-not-return",
                },
            )
        )
        db.commit()

    response = client.get(
        "/api/v1/saas/token-discovery-history",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    history = response.json()["history"]
    assert history[0]["metadata"] == {
        "safe": {"score": 88},
        "token_address": "0xLEGACY",
        "credentials": {"source": "legacy"},
        "checks": [{"status": "ok", "nested": {"value": 7}}],
    }
    assert "must-not-return" not in response.text


def test_token_discovery_history_delete_is_account_scoped_and_audited():
    token_a = _token(_email("history-delete-a"))
    token_b = _token(_email("history-delete-b"))
    created_a = client.post(
        "/api/v1/saas/token-discovery-history",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"token_address": "0xAAA", "chain": "base", "metadata": {"risk": "low"}},
    )
    assert created_a.status_code == 201
    history = created_a.json()["history"]
    history_id = history["id"]
    org_id = history["org_id"]

    cross_org_delete = client.delete(
        f"/api/v1/saas/token-discovery-history/{history_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert cross_org_delete.status_code == 404

    deleted = client.delete(
        f"/api/v1/saas/token-discovery-history/{history_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"org_id": org_id, "deleted": True, "id": history_id}

    listed = client.get(
        "/api/v1/saas/token-discovery-history",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert listed.status_code == 200
    assert listed.json()["history"] == []

    with SessionLocal() as db:
        assert (
            db.query(TokenDiscoveryHistory)
            .filter(TokenDiscoveryHistory.id == history_id)
            .first()
            is None
        )
        audit_log = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == org_id,
                AuditLog.action == "token_discovery_history.deleted",
                AuditLog.target_id == str(history_id),
            )
            .one()
        )
        assert audit_log.before["token_address"] == "0xAAA"
        assert audit_log.before["metadata"] == {"risk": "low"}


def test_org_member_cannot_delete_token_discovery_history():
    owner_token = _token(_email("history-delete-owner"))
    created = client.post(
        "/api/v1/saas/token-discovery-history",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"token_address": "0xAAA", "chain": "base"},
    )
    assert created.status_code == 201
    history = created.json()["history"]
    member_token = _member_token(int(history["org_id"]))

    deleted = client.delete(
        f"/api/v1/saas/token-discovery-history/{history['id']}",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert deleted.status_code == 403
    assert deleted.json()["detail"] == "Tenant admin role required"

    listed = client.get(
        "/api/v1/saas/token-discovery-history",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["history"]] == [history["id"]]


def test_token_discovery_history_delete_requires_jwt():
    response = client.delete("/api/v1/saas/token-discovery-history/1")
    assert response.status_code == 401


def test_account_data_export_is_tenant_scoped_and_secret_safe():
    token_a = _token(_email("export-a"))
    token_b = _token(_email("export-b"))
    api_key_a = _api_key(token_a)
    api_key_b = _api_key(token_b)
    secret = "export-gemini-key-1234567890"

    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"api_key": secret, "model": "gemini-3-flash-preview"},
    )
    assert saved.status_code == 200
    history_a = client.post(
        "/api/v1/saas/token-discovery-history",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"token_address": "0xEXPORTA", "chain": "base", "metadata": {"risk": "low"}},
    )
    assert history_a.status_code == 201
    history_b = client.post(
        "/api/v1/saas/token-discovery-history",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"token_address": "0xEXPORTB", "chain": "base"},
    )
    assert history_b.status_code == 201

    response = client.get("/api/v1/saas/export", headers={"Authorization": f"Bearer {token_a}"})

    assert response.status_code == 200
    assert secret not in response.text
    assert api_key_a["plaintext"] not in response.text
    assert api_key_b["plaintext"] not in response.text
    payload = response.json()
    assert payload["account"]["org_id"] == api_key_a["org_id"]
    assert payload["account"]["email"].startswith("export-a-")
    assert [key["id"] for key in payload["api_keys"]] == [api_key_a["id"]]
    assert "key_hash" not in payload["api_keys"][0]
    assert "plaintext" not in payload["api_keys"][0]
    assert payload["provider_credentials"]["gemini"]["configured"] is True
    assert payload["provider_credentials"]["gemini"]["key_hint"] == "export...7890"
    assert [item["token_address"] for item in payload["token_discovery_history"]] == [
        "0xEXPORTA"
    ]
    assert "0xEXPORTB" not in response.text
    assert {event["org_id"] for event in payload["audit_events"]} == {api_key_a["org_id"]}


def test_account_data_export_requires_jwt():
    response = client.get("/api/v1/saas/export")
    assert response.status_code == 401


def test_org_member_cannot_export_org_account_data():
    owner_token = _token(_email("export-owner"))
    key = _api_key(owner_token)
    member_token = _member_token(int(key["org_id"]))

    response = client.get(
        "/api/v1/saas/export",
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Tenant admin role required"


def test_org_member_cannot_manage_org_api_keys_or_billing():
    owner_token = _token(_email("tenant-owner"))
    key = _api_key(owner_token)
    member_token = _member_token(int(key["org_id"]))
    headers = {"Authorization": f"Bearer {member_token}"}

    created = client.post("/api/v1/saas/api-keys", headers=headers, json={"name": "member"})
    listed = client.get("/api/v1/saas/api-keys", headers=headers)
    checkout = client.post("/api/v1/billing/checkout", headers=headers, json={"plan_code": "pro"})
    status_response = client.get("/api/v1/billing/status", headers=headers)
    invoices = client.get("/api/v1/saas/invoices", headers=headers)
    audit_log = client.get("/api/v1/saas/audit-log", headers=headers)

    assert created.status_code == 403
    assert created.json()["detail"] == "Tenant admin role required"
    assert listed.status_code == 403
    assert checkout.status_code == 403
    assert status_response.status_code == 403
    assert invoices.status_code == 403
    assert audit_log.status_code == 403
    assert audit_log.json()["detail"] == "Tenant admin role required"


def test_org_admin_can_manage_org_api_keys():
    owner_token = _token(_email("tenant-owner"))
    key = _api_key(owner_token)
    admin_token = _member_token(int(key["org_id"]), role="admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    listed = client.get(
        "/api/v1/saas/api-keys",
        headers=admin_headers,
    )
    audit_log = client.get("/api/v1/saas/audit-log", headers=admin_headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [key["id"]]
    assert audit_log.status_code == 200
    assert audit_log.json()["org_id"] == key["org_id"]

    revoked = client.delete(
        f"/api/v1/saas/api-keys/{key['id']}",
        headers=admin_headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["id"] == key["id"]

    created = client.post(
        "/api/v1/saas/api-keys",
        headers=admin_headers,
        json={"name": "admin-created"},
    )
    assert created.status_code == 201


def test_api_key_scope_limits_tenant_operation():
    token = _token()
    created = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "audit-only", "scopes": ["tokens:audit"]},
    )
    assert created.status_code == 201
    assert created.json()["scopes"] == "tokens:audit"

    audited = client.get(
        "/api/v1/tokens/0xSAFE/audit",
        headers={"X-API-Key": str(created.json()["plaintext"])},
    )
    assert audited.status_code == 200

    simulated = client.post(
        "/api/v1/simulate/order",
        headers={"X-API-Key": str(created.json()["plaintext"])},
        json={"token_address": "0xSAFE", "amount": 100, "side": "BUY"},
    )
    assert simulated.status_code == 403
    assert simulated.json()["detail"] == "API key scope denied"


def test_api_key_rate_limit_is_scoped_per_tenant_and_key(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_API_KEY_PER_MINUTE", "1")
    first_key = _api_key(_token(_email("api-key-rate-a")))
    second_key = _api_key(_token(_email("api-key-rate-b")))

    first_headers = {"X-API-Key": str(first_key["plaintext"])}
    second_headers = {"X-API-Key": str(second_key["plaintext"])}

    assert client.get("/api/v1/tokens/0xSAFE/audit", headers=first_headers).status_code == 200
    limited = client.get("/api/v1/tokens/0xSAFE/audit", headers=first_headers)
    isolated = client.get("/api/v1/tokens/0xSAFE/audit", headers=second_headers)

    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    assert limited.json()["detail"] == {
        "error": "rate_limited",
        "message": "Too many API key requests. Slow down and try again shortly.",
        "retry_after_seconds": 60,
    }
    assert str(first_key["plaintext"]) not in limited.text
    assert isolated.status_code == 200


def test_api_key_rate_limit_reset_hook_restores_budget(monkeypatch):
    from tothemoon_api.observability import rate_limiter

    monkeypatch.setenv("RATE_LIMIT_API_KEY_PER_MINUTE", "1")
    created = _api_key(_token(_email("api-key-rate-reset")))
    headers = {"X-API-Key": str(created["plaintext"])}

    assert client.get("/api/v1/tokens/0xSAFE/audit", headers=headers).status_code == 200
    assert client.get("/api/v1/tokens/0xSAFE/audit", headers=headers).status_code == 429
    rate_limiter.reset()

    assert client.get("/api/v1/tokens/0xSAFE/audit", headers=headers).status_code == 200


def test_invalid_api_key_scope_is_rejected():
    token = _token()

    response = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "invalid", "scopes": ["admin:all"]},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid API key scopes: admin:all"
