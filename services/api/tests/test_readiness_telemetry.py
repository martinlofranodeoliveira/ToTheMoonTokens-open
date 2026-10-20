from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from tothemoon_api.database import SessionLocal
from tothemoon_api.db_models import AuditLog
from tothemoon_api.main import app

client = TestClient(app)


BOT_DIR = Path(__file__).resolve().parents[3] / "bot"


def _load_bot_module(name: str) -> Any:
    import sys

    if str(BOT_DIR) not in sys.path:
        sys.path.insert(0, str(BOT_DIR))
    spec = importlib.util.spec_from_file_location(name, BOT_DIR / f"{name}.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


auditor = _load_bot_module("auditor")
bot_main = _load_bot_module("main")


def _create_api_key(email: str = "synthetic-user-35@example.com") -> str:
    password = "correct horse battery staple"
    client.post("/api/v1/auth/signup", json={"email": email, "password": password})
    login = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    token = login.json()["access_token"]
    created = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "readiness-test"},
    )
    return created.json()["plaintext"]


def _create_api_key_with_org(email: str = "synthetic-user-35@example.com") -> tuple[str, int]:
    password = "correct horse battery staple"
    signup = client.post("/api/v1/auth/signup", json={"email": email, "password": password})
    org_id = int(signup.json()["org_id"])
    login = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    token = login.json()["access_token"]
    created = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "readiness-test"},
    )
    return created.json()["plaintext"], org_id


def test_token_audit_includes_proposal_readiness_payload() -> None:
    response = client.get(
        "/api/v1/tokens/0xSCAM/audit",
        headers={"X-API-Key": _create_api_key("synthetic-user-36@example.com")},
    )

    assert response.status_code == 200
    readiness = response.json()["proposal_readiness"]
    assert readiness["ready"] is False
    assert readiness["skip_reasons"] == [
        "honeypot_detected",
        "risk_score_high",
        "provider_degraded",
    ]
    assert readiness["risk_score"] == 99
    assert readiness["is_honeypot"] is True
    assert readiness["provider_degraded"] is True
    assert readiness["provider_diagnostics"] == {}


def test_token_audit_records_tenant_scoped_readiness_audit() -> None:
    api_key, org_id = _create_api_key_with_org("synthetic-user-37@example.com")

    response = client.get("/api/v1/tokens/0xSCAM/audit", headers={"X-API-Key": api_key})

    assert response.status_code == 200
    assert "X-API-Key" not in response.text
    with SessionLocal() as db:
        row = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == org_id,
                AuditLog.action == "token_audit.evaluated",
                AuditLog.target_type == "token",
                AuditLog.target_id == "0xSCAM",
            )
            .one()
        )
        assert row.actor_type == "api_key"
        assert row.after == {
            "ready": False,
            "skip_reasons": ["honeypot_detected", "risk_score_high", "provider_degraded"],
            "risk_score": 99,
            "is_honeypot": True,
            "provider_degraded": True,
        }
        assert api_key not in str(row.after)


def test_token_safety_decision_fails_closed_on_provider_diagnostics() -> None:
    audit = {
        "security": {"is_honeypot": False, "risk_score": 20, "confidence": "high"},
        "proposal_readiness": {
            "ready": False,
            "skip_reasons": ["provider_degraded", "api_key=secret"],
            "risk_score": 20,
            "is_honeypot": False,
            "provider_degraded": True,
            "provider_diagnostics": {"goplus": {"status": "degraded"}},
        },
    }

    decision = auditor.token_safety_decision_from_audit(audit)

    assert decision == {
        "safe": False,
        "skip_reasons": ["provider_degraded"],
        "risk_score": 20,
        "is_honeypot": False,
        "provider_degraded": True,
    }


def test_analyze_and_propose_logs_sanitized_token_skipped(monkeypatch, caplog) -> None:
    async def fake_decision(client: httpx.AsyncClient, token_address: str, *, api_key: str) -> dict[str, Any]:
        return {
            "safe": False,
            "skip_reasons": ["provider_degraded"],
            "risk_score": 99,
            "is_honeypot": True,
            "provider_degraded": True,
        }

    async def fail_create(*args: Any, **kwargs: Any) -> dict[str, object]:
        raise AssertionError("skip decisions must not create proposals")

    monkeypatch.setattr(bot_main, "token_safety_decision", fake_decision)
    monkeypatch.setattr(bot_main, "create_copilot_proposal", fail_create)

    caplog.set_level(logging.INFO, logger=bot_main.logger.name)
    result = httpx.AsyncClient()

    import asyncio

    proposal = asyncio.run(
        bot_main.analyze_and_propose(
            result,
            {"address": "0xSCAM", "symbol": "SCAM"},
            api_key="test-api-key",
            amount_usd=100.0,
            mode="paper",
        )
    )
    asyncio.run(result.aclose())

    assert proposal is None
    event = next(record for record in caplog.records if record.message == "token_skipped")
    assert event.token_address == "0xSCAM"
    assert event.skip_reasons == ["provider_degraded"]
    assert event.risk_score == 99
    assert event.is_honeypot is True
    assert event.provider_degraded is True
    assert "test-api-key" not in caplog.text
