from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "bot"))

import main as bot_main_module  # noqa: E402
import scanner as scanner_module  # noqa: E402
from safety import BotSafetyError, assert_paper_or_guarded_testnet_status  # noqa: E402
from scanner import MarketScanner, ScannerProviderGuardrailError  # noqa: E402
from trader import create_copilot_proposal  # noqa: E402

from tothemoon_api.database import SessionLocal  # noqa: E402
from tothemoon_api.db_models import (  # noqa: E402
    ApiKey,
    AuditLog,
    CopilotProposal,
    CopilotScannerRun,
    Organization,
    Plan,
    SimulatedTrade,
    StrategyOutcome,
    TokenDiscoveryHistory,
)
from tothemoon_api.external.health import get_provider_health  # noqa: E402
from tothemoon_api.main import app  # noqa: E402
from tothemoon_api.observability import REDACTED_PLACEHOLDER  # noqa: E402

client = TestClient(app)
PASSWORD = "correct horse battery staple"


def _signup_login_key(prefix: str = "phase6", scopes: list[str] | None = None) -> tuple[str, str, int]:
    email = f"{prefix}-{uuid4().hex}@example.com"
    signup = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert signup.status_code == 201
    org_id = int(signup.json()["org_id"])
    login = client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["access_token"]
    create_payload: dict[str, object] = {"name": "phase6"}
    if scopes is not None:
        create_payload["scopes"] = scopes
    created = client.post(
        "/api/v1/saas/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json=create_payload,
    )
    assert created.status_code == 201
    return token, created.json()["plaintext"], org_id


def test_scanner_filters_by_volume_and_momentum():
    scanner = MarketScanner()
    tokens = [
        {
            "address": "0xlowvolume",
            "volume_24h": 99_999,
            "momentum": 20,
            "liquidity_usd": 60_000,
        },
        {
            "address": "0xlowmomentum",
            "volume_24h": 500_000,
            "momentum": 4.99,
            "liquidity_usd": 60_000,
        },
        {
            "address": "0xlowliquidity",
            "volume_24h": 500_000,
            "momentum": 6,
            "liquidity_usd": 49_999,
        },
        {
            "address": "0xpromising",
            "volume_24h": 500_000,
            "momentum": 6,
            "liquidity_usd": 60_000,
        },
    ]

    promising = scanner.filter_promising_tokens(tokens)

    assert [token["address"] for token in promising] == ["0xpromising"]


def test_copilot_scanner_uses_free_dexscreener_discovery_for_narratives(monkeypatch):
    from tothemoon_api.routers.copilot import CopilotScanRequest, _scan_market_candidates

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None):
            if url.endswith("/latest/dex/search"):
                return FakeResponse({"pairs": []})
            if "token-profiles/latest" in url:
                return FakeResponse(
                    [
                        {
                            "chainId": "base",
                            "tokenAddress": "0xAIDISCOVERY",
                            "description": "AI agent launch",
                        }
                    ]
                )
            if "token-boosts" in url:
                return FakeResponse([])
            if "/tokens/v1/base/" in url:
                return FakeResponse(
                    [
                        {
                            "chainId": "base",
                            "baseToken": {
                                "address": "0xAIDISCOVERY",
                                "symbol": "AID",
                            },
                            "volume": {"h24": 55_000},
                            "priceChange": {"h1": 2.5},
                            "liquidity": {"usd": 25_000},
                            "marketCap": 500_000,
                            "priceUsd": "0.01",
                        }
                    ]
                )
            raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr("tothemoon_api.routers.copilot.httpx.Client", FakeClient)

    candidates = _scan_market_candidates(
        CopilotScanRequest(
            query="AI",
            strategy_family="F",
            limit=5,
            min_volume=20_000,
            min_momentum=0,
            min_liquidity=20_000,
        )
    )

    assert candidates == [
        {
            "token_address": "0xAIDISCOVERY",
            "chain": "base",
            "symbol": "AID",
            "volume_24h": 55_000.0,
            "momentum_h1": 2.5,
            "liquidity_usd": 25_000.0,
            "market_cap_usd": 500_000.0,
            "price_usd": 0.01,
            "source": "dexscreener_free_discovery",
        }
    ]


def test_roundtrip_cost_filter_allows_high_cost_paper_scout():
    from decimal import Decimal

    from tothemoon_api.routers.copilot import _paper_roundtrip_cost_viability

    scout = _paper_roundtrip_cost_viability(
        candidate={"chain": "solana"},
        audit={"buy_tax": 6, "sell_tax": 6},
        amount_brl=Decimal("30"),
    )
    unviable = _paper_roundtrip_cost_viability(
        candidate={"chain": "solana"},
        audit={"buy_tax": 15, "sell_tax": 15},
        amount_brl=Decimal("30"),
    )

    assert scout["allowed"] is True
    assert scout["cost_tier"] == "high_cost_scout"
    assert scout["estimated_roundtrip_cost_pct"] > scout["high_cost_scout_pct"]
    assert unviable["allowed"] is False
    assert unviable["cost_tier"] == "unviable"


def test_scanner_uses_external_provider_guardrails_before_http(monkeypatch):
    calls: dict[str, int] = {"guardrail": 0}
    statuses: list[dict[str, object]] = []
    assert scanner_module.SCANNER_PROVIDER_NAME == "dexscreener"

    class SafeAdapter:
        def assert_safe_for_paper_mode(self) -> None:
            calls["guardrail"] += 1

        def contract_payload(self) -> dict[str, object]:
            return {
                "name": scanner_module.SCANNER_PROVIDER_NAME,
                "capabilities": sorted(scanner_module.SCANNER_PROVIDER_CAPABILITIES),
                "read_only": True,
                "supports_order_submission": False,
                "mainnet_order_submission": False,
                "public_exposure": False,
            }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "pairs": [
                    {
                        "chainId": "base",
                        "baseToken": {"address": "0xSAFE", "symbol": "SAFE"},
                        "volume": {"h24": "250000"},
                        "priceChange": {"h1": "6.5"},
                        "liquidity": {"usd": "90000"},
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            assert kwargs["timeout"] == 2.5

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str, *, params: dict[str, object]):
            assert url == scanner_module.DEXSCREENER_LATEST
            assert params == {"q": "USDC"}
            return FakeResponse()

    monkeypatch.setattr(scanner_module, "provider_adapter", lambda *args, **kwargs: SafeAdapter())
    monkeypatch.setattr(scanner_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(
        scanner_module,
        "get_settings",
        lambda: type("Settings", (), {"external_http_timeout_s": 2.5})(),
    )
    monkeypatch.setattr(
        scanner_module,
        "record_provider_status",
        lambda provider, **kwargs: statuses.append({"provider": provider, **kwargs}),
    )

    tokens = asyncio.run(MarketScanner().scan_new_pairs())

    assert calls["guardrail"] == 1
    assert statuses[-1]["provider"] == scanner_module.SCANNER_PROVIDER_NAME
    assert statuses[-1]["status"] == "ok"
    assert tokens == [
        {
            "address": "0xSAFE",
            "chain": "base",
            "symbol": "SAFE",
            "volume_24h": 250000.0,
            "momentum": 6.5,
            "liquidity_usd": 90000.0,
        }
    ]


def test_scanner_blocks_unsafe_provider_before_http(monkeypatch):
    class UnsafeAdapter:
        def assert_safe_for_paper_mode(self) -> None:
            return None

        def contract_payload(self) -> dict[str, object]:
            return {
                "name": scanner_module.SCANNER_PROVIDER_NAME,
                "capabilities": sorted(scanner_module.SCANNER_PROVIDER_CAPABILITIES),
                "read_only": False,
                "supports_order_submission": True,
                "mainnet_order_submission": False,
                "public_exposure": False,
            }

    class FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("unsafe scanner provider must not reach external HTTP")

    monkeypatch.setattr(scanner_module, "provider_adapter", lambda *args, **kwargs: UnsafeAdapter())
    monkeypatch.setattr(scanner_module.httpx, "AsyncClient", FailingAsyncClient)

    with pytest.raises(ScannerProviderGuardrailError, match="paper-only guardrails"):
        asyncio.run(MarketScanner().scan_new_pairs())


def test_scanner_blocks_malformed_provider_contract_before_http(monkeypatch):
    class MalformedAdapter:
        def assert_safe_for_paper_mode(self) -> None:
            return None

        def contract_payload(self) -> dict[str, object]:
            return {
                "name": scanner_module.SCANNER_PROVIDER_NAME,
                "capabilities": sorted(scanner_module.SCANNER_PROVIDER_CAPABILITIES),
                "read_only": True,
                "supports_order_submission": False,
                "mainnet_order_submission": False,
            }

    class FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("malformed scanner provider contract must not reach external HTTP")

    monkeypatch.setattr(scanner_module, "provider_adapter", lambda *args, **kwargs: MalformedAdapter())
    monkeypatch.setattr(scanner_module.httpx, "AsyncClient", FailingAsyncClient)

    with pytest.raises(ScannerProviderGuardrailError, match="paper-only guardrails"):
        asyncio.run(MarketScanner().scan_new_pairs())


def test_scanner_records_degraded_provider_health_on_http_failure(monkeypatch):
    class SafeAdapter:
        def assert_safe_for_paper_mode(self) -> None:
            return None

        def contract_payload(self) -> dict[str, object]:
            return {
                "name": scanner_module.SCANNER_PROVIDER_NAME,
                "capabilities": sorted(scanner_module.SCANNER_PROVIDER_CAPABILITIES),
                "read_only": True,
                "supports_order_submission": False,
                "mainnet_order_submission": False,
                "public_exposure": False,
            }

    class FailingResponse:
        def raise_for_status(self) -> None:
            raise RuntimeError("401 Authorization=Bearer scanner.secret.token")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str, *, params: dict[str, object]):
            return FailingResponse()

    monkeypatch.setattr(scanner_module, "provider_adapter", lambda *args, **kwargs: SafeAdapter())
    monkeypatch.setattr(scanner_module.httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(RuntimeError, match="Authorization=Bearer"):
        asyncio.run(MarketScanner().scan_new_pairs())

    health = get_provider_health()[scanner_module.SCANNER_PROVIDER_NAME]
    assert health["status"] == "degraded"
    assert health["last_error"] == f"401 {REDACTED_PLACEHOLDER}"


def test_scanner_records_degraded_provider_health_when_pairs_missing(monkeypatch):
    statuses: list[dict[str, object]] = []

    class SafeAdapter:
        def assert_safe_for_paper_mode(self) -> None:
            return None

        def contract_payload(self) -> dict[str, object]:
            return {
                "name": scanner_module.SCANNER_PROVIDER_NAME,
                "capabilities": sorted(scanner_module.SCANNER_PROVIDER_CAPABILITIES),
                "read_only": True,
                "supports_order_submission": False,
                "mainnet_order_submission": False,
                "public_exposure": False,
            }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"pairs": {"unexpected": "object"}}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str, *, params: dict[str, object]):
            return FakeResponse()

    monkeypatch.setattr(scanner_module, "provider_adapter", lambda *args, **kwargs: SafeAdapter())
    monkeypatch.setattr(scanner_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(
        scanner_module,
        "record_provider_status",
        lambda provider, **kwargs: statuses.append({"provider": provider, **kwargs}),
    )

    assert asyncio.run(MarketScanner().scan_new_pairs()) == []
    assert statuses[-1]["provider"] == scanner_module.SCANNER_PROVIDER_NAME
    assert statuses[-1]["status"] == "degraded"
    assert statuses[-1]["last_error"] == "DexScreener scanner response did not include pairs"


def test_copilot_proposal_persists():
    token, api_key, org_id = _signup_login_key("proposal")
    response = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "symbol": "SAFE",
            "amount_usd": 100,
            "score": 72,
            "rationale": "High momentum with sufficient liquidity.",
        },
    )
    assert response.status_code == 201
    proposal = response.json()["proposal"]
    assert proposal["org_id"] == org_id
    assert proposal["status"] == "pending"

    listed = client.get("/api/v1/copilot/proposals", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    assert listed.json()["proposals"][0]["id"] == proposal["id"]
    streamed = client.get(
        "/api/v1/copilot/stream?once=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert streamed.status_code == 200
    assert "event: proposal" in streamed.text

    with SessionLocal() as db:
        row = db.query(CopilotProposal).filter(CopilotProposal.id == proposal["id"]).one()
        assert row.org_id == org_id
        assert row.token_address == "0xSAFE"


def test_copilot_proposal_creation_rejects_tripped_circuit_breaker():
    _token, api_key, org_id = _signup_login_key("proposal-circuit")
    with SessionLocal() as db:
        org = db.query(Organization).filter(Organization.id == org_id).one()
        org.bot_consecutive_failures = 3
        db.commit()

    response = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "symbol": "SAFE",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Circuit breaker should stop new paper proposals.",
        },
    )

    assert response.status_code == 423
    assert response.json()["detail"] == "Bot circuit breaker is tripped"
    with SessionLocal() as db:
        proposals = db.query(CopilotProposal).filter(CopilotProposal.org_id == org_id).all()
        assert proposals == []

def test_copilot_proposal_creation_rejects_unsafe_adapter_contract(monkeypatch):
    _token, api_key, org_id = _signup_login_key("proposal-unsafe-adapter")
    monkeypatch.setattr(
        "tothemoon_api.routers.copilot.get_external_adapter_contract",
        lambda: {
            "order_submission_enabled": True,
            "mainnet_order_submission_enabled": False,
            "providers": [
                {
                    "name": "unsafe",
                    "capabilities": ["market_data"],
                    "requires_api_key": False,
                    "read_only": False,
                    "supports_order_submission": True,
                    "mainnet_order_submission": False,
                }
            ],
        },
    )

    response = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "symbol": "SAFE",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Unsafe adapters must not queue proposals.",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "External adapter contract is not paper-only safe"
    with SessionLocal() as db:
        proposals = db.query(CopilotProposal).filter(CopilotProposal.org_id == org_id).all()
        assert proposals == []


def test_copilot_scan_paper_auto_persists_run_summary_and_trade(monkeypatch):
    token, _api_key, org_id = _signup_login_key("scan-paper-auto")

    monkeypatch.setattr(
        "tothemoon_api.routers.copilot._scan_market_candidates",
        lambda payload: [
            {
                "token_address": "0xSAFE",
                "chain": "base",
                "symbol": "SAFE",
                "volume_24h": 2_000_000,
                "momentum_h1": 12,
                "liquidity_usd": 400_000,
                "price_usd": 1.05,
            },
            {
                "token_address": "0xSCAM",
                "chain": "base",
                "symbol": "SCAM",
                "volume_24h": 3_000_000,
                "momentum_h1": 20,
                "liquidity_usd": 800_000,
                "price_usd": 1.10,
            },
        ],
    )

    response = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "SAFE", "amount_usd": 125, "limit": 2, "execution_mode": "paper_auto"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["org_id"] == org_id
    assert payload["execution_mode"] == "paper_auto"
    assert payload["scanner_run"] == {
        **payload["scanner_run"],
        "candidate_count": 2,
        "blocked_count": 1,
        "queued_count": 0,
        "paper_executed_count": 1,
    }
    assert len(payload["created"]) == 1
    assert payload["created"][0]["status"] == "executed"
    assert payload["created"][0]["scanner_run_id"] == payload["scanner_run"]["id"]
    assert payload["created"][0]["execution_payload"]["source"] == "paper_simulation"
    assert payload["skipped"] == [
        {"token_address": "0xSCAM", "reason": "honeypot_detected", "score": 75.25}
    ]

    with SessionLocal() as db:
        run = db.query(CopilotScannerRun).filter(CopilotScannerRun.org_id == org_id).one()
        assert run.candidate_count == 2
        assert run.blocked_count == 1
        assert run.queued_count == 0
        assert run.paper_executed_count == 1
        assert run.completed_at is not None
        proposal = db.query(CopilotProposal).filter(CopilotProposal.org_id == org_id).one()
        assert proposal.scanner_run_id == run.id
        assert db.query(SimulatedTrade).filter(SimulatedTrade.org_id == org_id).count() == 1
        assert db.query(TokenDiscoveryHistory).filter(TokenDiscoveryHistory.org_id == org_id).count() == 2


def test_copilot_proposal_creation_requires_valid_org_api_key():
    payload = {
        "token_address": "0xSAFE",
        "chain": "evm",
        "symbol": "SAFE",
        "amount_usd": 100,
        "score": 72,
        "rationale": "High momentum with sufficient liquidity.",
    }

    missing = client.post("/api/v1/copilot/proposals", json=payload)
    invalid = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": "ttm_sk_live_invalid"},
        json=payload,
    )

    assert missing.status_code == 401
    assert invalid.status_code == 403


def test_copilot_proposal_creation_fails_closed_on_unsafe_adapter_contract(monkeypatch):
    _token, api_key, _org_id = _signup_login_key("proposal-unsafe-adapter")

    monkeypatch.setattr(
        "tothemoon_api.routers.copilot.get_external_adapter_contract",
        lambda: {
            "mode": "paper_only_read_through",
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": [
                {
                    "name": "unsafe-provider",
                    "capabilities": ["market_data"],
                    "requires_api_key": False,
                    "read_only": False,
                    "supports_order_submission": True,
                    "mainnet_order_submission": False,
                    "public_exposure": False,
                }
            ],
        },
    )

    response = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xUNSAFEADAPTER",
            "chain": "evm",
            "symbol": "SAFE",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Unsafe adapter contracts must block bot proposals.",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "External adapter contract is not paper-only safe"
    with SessionLocal() as db:
        assert (
            db.query(CopilotProposal)
            .filter(CopilotProposal.token_address == "0xUNSAFEADAPTER")
            .count()
            == 0
        )


def test_copilot_bot_status_is_operator_visible_and_paper_only(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_USE_VERTEXAI", "false")
    _token, api_key, org_id = _signup_login_key("bot-status")

    response = client.get("/api/v1/copilot/bot/status", headers={"X-API-Key": api_key})

    assert response.status_code == 200
    payload = response.json()
    assert payload["org_id"] == org_id
    assert payload["runtime_mode"] == "paper"
    assert payload["paper_only_default"] is True
    assert payload["order_submission_enabled"] is False
    assert payload["mainnet_order_submission_enabled"] is False
    assert payload["workflows"] == {
        "scanner": "api_adapter_ready",
        "auditor": "api_adapter_ready",
        "trader": "paper_proposal_only",
        "consecutive_failures": 0,
        "circuit_breaker_tripped": False,
    }
    assert payload["safeguards"]["production_mainnet_blocked"] is True
    assert payload["safeguards"]["adapter_contract_enforced"] is True
    assert payload["provider_readiness"]["paper_only"] is True
    assert payload["provider_readiness"]["order_submission_enabled"] is False
    assert payload["provider_readiness"]["mainnet_order_submission_enabled"] is False
    assert {provider["name"] for provider in payload["provider_readiness"]["providers"]} == {
        "goplus",
        "honeypotis",
        "tokensniffer",
        "dexscreener",
        "birdeye",
    }
    assert payload["providers"]["gemini"] == {
        "configured": False,
        "source": "unconfigured",
        "provider": "gemini",
        "model": "gemini-3.1-pro-preview",
    }
    assert payload["external_adapter_contract"]["order_submission_enabled"] is False


def test_copilot_api_key_endpoints_require_copilot_scope():
    _token, api_key, _org_id = _signup_login_key("copilot-scope", scopes=["tokens:audit"])

    status_response = client.get("/api/v1/copilot/bot/status", headers={"X-API-Key": api_key})
    create_response = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Copilot proposal creation requires a copilot-scoped key.",
        },
    )
    failure_response = client.post(
        "/api/v1/copilot/circuit-breaker/failures",
        headers={"X-API-Key": api_key},
    )

    assert status_response.status_code == 403
    assert create_response.status_code == 403
    assert failure_response.status_code == 403
    assert status_response.json()["detail"] == "API key scope denied"
    assert create_response.json()["detail"] == "API key scope denied"
    assert failure_response.json()["detail"] == "API key scope denied"


def test_copilot_api_key_endpoints_require_valid_org_api_key():
    assert client.get("/api/v1/copilot/bot/status").status_code == 401
    assert client.post("/api/v1/copilot/circuit-breaker/failures").status_code == 401

    invalid_headers = {"X-API-Key": "ttm_sk_live_invalid"}
    status_response = client.get("/api/v1/copilot/bot/status", headers=invalid_headers)
    breaker_response = client.post(
        "/api/v1/copilot/circuit-breaker/failures",
        headers=invalid_headers,
    )

    assert status_response.status_code == 403
    assert breaker_response.status_code == 403


def test_copilot_bot_status_reports_org_gemini_readiness_without_secret(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "global-gemini-key-should-not-win")
    token, api_key, _org_id = _signup_login_key("bot-status-gemini")
    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        headers={"Authorization": f"Bearer {token}"},
        json={"api_key": "org-gemini-key-123456", "model": "gemini-3-pro-preview"},
    )
    assert saved.status_code == 200

    response = client.get("/api/v1/copilot/bot/status", headers={"X-API-Key": api_key})

    assert response.status_code == 200
    assert response.json()["providers"]["gemini"] == {
        "configured": True,
        "source": "organization",
        "provider": "gemini",
        "model": "gemini-3-pro-preview",
        "key_hint": "org-ge...3456",
    }
    assert "org-gemini-key-123456" not in response.text
    assert "global-gemini-key-should-not-win" not in response.text


def test_copilot_bot_status_reports_global_gemini_readiness_without_secret(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "global-gemini-key-123456")
    _token, api_key, _org_id = _signup_login_key("bot-status-global-gemini")

    response = client.get("/api/v1/copilot/bot/status", headers={"X-API-Key": api_key})

    assert response.status_code == 200
    assert response.json()["providers"]["gemini"] == {
        "configured": True,
        "source": "global",
        "provider": "gemini",
        "model": "gemini-3.1-pro-preview",
    }
    assert "global-gemini-key-123456" not in response.text


def test_copilot_bot_status_reports_vertexai_gemini_readiness(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GEMINI_VERTEX_PROJECT", "ttm-prod-project")
    monkeypatch.setenv("GEMINI_VERTEX_LOCATION", "us-central1")
    _token, api_key, _org_id = _signup_login_key("bot-status-vertex-gemini")

    response = client.get("/api/v1/copilot/bot/status", headers={"X-API-Key": api_key})

    assert response.status_code == 200
    assert response.json()["providers"]["gemini"] == {
        "configured": True,
        "source": "vertexai",
        "model": "gemini-3.1-pro-preview",
        "vertex_project_configured": True,
        "vertex_location": "us-central1",
        "provider": "gemini",
    }
    assert "ttm-prod-project" not in response.text


def test_bot_safety_accepts_paper_and_guarded_testnet_only():
    base_status = {
        "runtime_mode": "paper",
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "readiness": {"ready": True, "blocking_reasons": []},
        "provider_readiness": {
            "status": "degraded",
            "providers": [],
            "capabilities": {},
            "paper_only": True,
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
        },
        "safeguards": {
            "guarded_testnet_enabled": False,
            "production_mainnet_blocked": True,
            "adapter_contract_enforced": True,
        },
    }
    assert_paper_or_guarded_testnet_status(base_status)
    assert_paper_or_guarded_testnet_status(
        {
            **base_status,
            "runtime_mode": "guarded_testnet",
            "safeguards": {**base_status["safeguards"], "guarded_testnet_enabled": True},
        }
    )

    for unsafe_status in (
        {**base_status, "runtime_mode": "blocked_mainnet"},
        {**base_status, "order_submission_enabled": True},
        {**base_status, "mainnet_order_submission_enabled": True},
        {**base_status, "readiness": {"ready": False, "blocking_reasons": ["gemini_not_configured"]}},
        {**base_status, "readiness": {"blocking_reasons": []}},
        {key: value for key, value in base_status.items() if key != "readiness"},
        {**base_status, "provider_readiness": {"paper_only": False}},
        {**base_status, "provider_readiness": {"order_submission_enabled": True}},
        {**base_status, "provider_readiness": {"mainnet_order_submission_enabled": True}},
        {**base_status, "provider_readiness": {**base_status["provider_readiness"], "status": None}},
        {
            **base_status,
            "provider_readiness": {**base_status["provider_readiness"], "providers": None},
        },
        {
            **base_status,
            "provider_readiness": {**base_status["provider_readiness"], "capabilities": None},
        },
        {
            **base_status,
            "safeguards": {**base_status["safeguards"], "adapter_contract_enforced": False},
        },
        {
            **base_status,
            "runtime_mode": "guarded_testnet",
            "safeguards": {**base_status["safeguards"], "guarded_testnet_enabled": False},
        },
    ):
        try:
            assert_paper_or_guarded_testnet_status(unsafe_status)
        except BotSafetyError:
            continue
        raise AssertionError("unsafe bot status must be rejected")


def test_bot_trader_entrypoint_rejects_real_mode_before_api_call():
    class FailingClient:
        async def post(self, *args, **kwargs):
            raise AssertionError("real-mode bot proposal must not call the API")

    try:
        asyncio.run(
            create_copilot_proposal(
                FailingClient(),
                api_key="ttm_sk_live_redacted",
                token={"address": "0xSAFE"},
                mode="real",
            )
        )
    except ValueError as exc:
        assert "paper-only" in str(exc)
        return
    raise AssertionError("real-mode bot proposal must be rejected")


def test_bot_orchestration_rejects_real_mode_before_scan(monkeypatch):
    async def fail_scan_market(*args, **kwargs):
        raise AssertionError("real-mode bot orchestration must not scan providers")

    monkeypatch.setattr(bot_main_module, "scan_market", fail_scan_market)

    with pytest.raises(ValueError, match="paper-only"):
        asyncio.run(
            bot_main_module.run_once(
                api_key="ttm_sk_live_redacted",
                api_base_url="http://127.0.0.1:8010",
                query="USDC",
                amount_usd=100,
                limit=1,
                mode="real",
            )
        )


def test_gen1174_bot_orchestration_skips_scan_when_provider_status_degraded(monkeypatch):
    async def fail_scan_market(*args, **kwargs):
        raise AssertionError("provider-degraded status must skip scanner network calls")

    async def degraded_fetch_bot_status(client, *, api_key: str):
        return {
            "runtime_mode": "paper",
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "workflows": {"provider_degraded": True, "scanner": "provider_degraded"},
        }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(bot_main_module, "scan_market", fail_scan_market)
    monkeypatch.setattr(bot_main_module, "fetch_bot_status", degraded_fetch_bot_status)
    monkeypatch.setattr(bot_main_module.httpx, "AsyncClient", FakeAsyncClient)

    proposals = asyncio.run(
        bot_main_module.run_once(
            api_key="ttm_sk_live_redacted",
            api_base_url="http://127.0.0.1:8010",
            query="USDC",
            amount_usd=100,
            limit=1,
            mode="paper",
        )
    )

    assert proposals == []


def test_bot_orchestration_fetches_status_before_scan(monkeypatch):
    calls: list[str] = []

    async def fake_fetch_bot_status(client, *, api_key):
        calls.append("status")
        return {
            "runtime_mode": "paper",
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "workflows": {},
            "readiness": {"ready": True, "blocking_reasons": []},
            "provider_readiness": {
                "paper_only": True,
                "order_submission_enabled": False,
                "mainnet_order_submission_enabled": False,
            },
            "safeguards": {
                "production_mainnet_blocked": True,
                "adapter_contract_enforced": True,
                "guarded_testnet_enabled": False,
            },
        }

    async def fake_scan_market(*args, **kwargs):
        calls.append("scan")
        return []

    monkeypatch.setattr(bot_main_module, "fetch_bot_status", fake_fetch_bot_status)
    monkeypatch.setattr(bot_main_module, "scan_market", fake_scan_market)

    proposals = asyncio.run(
        bot_main_module.run_once(
            api_key="ttm_sk_live_redacted",
            api_base_url="http://127.0.0.1:8010",
            query="USDC",
            amount_usd=100,
            limit=1,
            mode="paper",
        )
    )

    assert proposals == []
    assert calls == ["status", "scan"]


def test_bot_orchestration_does_not_scan_when_status_is_unsafe(monkeypatch):
    async def unsafe_fetch_bot_status(client, *, api_key):
        raise BotSafetyError("Provider readiness must preserve paper-only execution")

    async def fail_scan_market(*args, **kwargs):
        raise AssertionError("unsafe bot status must stop before provider scans")

    monkeypatch.setattr(bot_main_module, "fetch_bot_status", unsafe_fetch_bot_status)
    monkeypatch.setattr(bot_main_module, "scan_market", fail_scan_market)

    with pytest.raises(BotSafetyError, match="paper-only"):
        asyncio.run(
            bot_main_module.run_once(
                api_key="ttm_sk_live_redacted",
                api_base_url="http://127.0.0.1:8010",
                query="USDC",
                amount_usd=100,
                limit=1,
                mode="paper",
            )
        )


def test_bot_orchestration_does_not_scan_when_backend_readiness_blocked(monkeypatch):
    async def blocked_fetch_bot_status(client, *, api_key):
        status = {
            "runtime_mode": "paper",
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "workflows": {},
            "readiness": {"ready": False, "blocking_reasons": ["gemini_not_configured"]},
            "provider_readiness": {
                "paper_only": True,
                "order_submission_enabled": False,
                "mainnet_order_submission_enabled": False,
            },
            "safeguards": {
                "production_mainnet_blocked": True,
                "adapter_contract_enforced": True,
                "guarded_testnet_enabled": False,
            },
        }
        assert_paper_or_guarded_testnet_status(status)
        return status

    async def fail_scan_market(*args, **kwargs):
        raise AssertionError("backend readiness blocks must stop before provider scans")

    monkeypatch.setattr(bot_main_module, "fetch_bot_status", blocked_fetch_bot_status)
    monkeypatch.setattr(bot_main_module, "scan_market", fail_scan_market)

    with pytest.raises(BotSafetyError, match="gemini_not_configured"):
        asyncio.run(
            bot_main_module.run_once(
                api_key="ttm_sk_live_redacted",
                api_base_url="http://127.0.0.1:8010",
                query="USDC",
                amount_usd=100,
                limit=1,
                mode="paper",
            )
        )


def test_copilot_proposal_approval_executes_paper_simulation():
    token, api_key, org_id = _signup_login_key("proposal-approve")
    created = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "symbol": "SAFE",
            "amount_usd": 100,
            "score": 72,
            "rationale": "High momentum with sufficient liquidity.",
        },
    )
    assert created.status_code == 201
    proposal_id = created.json()["proposal"]["id"]

    approved = client.post(
        f"/api/v1/copilot/proposals/{proposal_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert approved.status_code == 200
    proposal = approved.json()["proposal"]
    assert proposal["status"] == "executed"
    assert proposal["execution_payload"]["trade_id"]

    with SessionLocal() as db:
        audit = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == org_id,
                AuditLog.action == "copilot.proposal.approved",
                AuditLog.target_id == str(proposal_id),
            )
            .one()
        )
        assert audit.before == {"status": "pending", "scanner_run_id": None}
        assert audit.after == {
            "status": "executed",
            "scanner_run_id": None,
            "execution": "paper_simulated",
            "trade_id": proposal["execution_payload"]["trade_id"],
        }


def test_copilot_proposal_rejects_blank_required_fields():
    _token, api_key, _org_id = _signup_login_key("proposal-blank-fields")

    for payload in (
        {"token_address": "   ", "chain": "evm", "amount_usd": 100, "rationale": "ok"},
        {"token_address": "0xSAFE", "chain": "   ", "amount_usd": 100, "rationale": "ok"},
        {"token_address": "0xSAFE", "chain": "evm", "amount_usd": 100, "rationale": "   "},
    ):
        response = client.post(
            "/api/v1/copilot/proposals",
            headers={"X-API-Key": api_key},
            json=payload,
        )

        assert response.status_code == 422


def test_copilot_proposal_normalizes_trimmed_fields():
    _token, api_key, org_id = _signup_login_key("proposal-trim-fields")

    response = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": " 0xSAFE ",
            "chain": " EVM ",
            "symbol": "   ",
            "amount_usd": 100,
            "score": 72,
            "rationale": " High momentum with sufficient liquidity. ",
        },
    )

    assert response.status_code == 201
    proposal = response.json()["proposal"]
    assert proposal["token_address"] == "0xSAFE"
    assert proposal["chain"] == "evm"
    assert proposal["symbol"] is None
    assert proposal["rationale"] == "High momentum with sufficient liquidity."

    with SessionLocal() as db:
        audit = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == org_id,
                AuditLog.action == "copilot.proposal.created",
                AuditLog.target_id == str(proposal["id"]),
            )
            .one()
        )
        assert audit.actor_type == "api_key"
        assert audit.after == {
            "status": "pending",
            "mode": "paper",
            "token_address": "0xSAFE",
            "chain": "evm",
            "api_key_prefix": api_key[:12],
        }
        assert api_key not in str(audit.after)


def test_copilot_proposal_approval_is_single_use():
    token, api_key, _org_id = _signup_login_key("proposal-once")
    created = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "symbol": "SAFE",
            "amount_usd": 100,
            "score": 72,
            "rationale": "High momentum with sufficient liquidity.",
        },
    )
    assert created.status_code == 201
    proposal_id = created.json()["proposal"]["id"]

    first = client.post(
        f"/api/v1/copilot/proposals/{proposal_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/v1/copilot/proposals/{proposal_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "Proposal is not pending"


def test_mode2_approval_queue_contract_auth_and_tenant_scope():
    token, api_key, _org_id = _signup_login_key("mode2-queue-a")
    other_token, other_api_key, _other_org_id = _signup_login_key("mode2-queue-b")

    first = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xQUEUEA",
            "chain": "base",
            "symbol": "QA",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Tenant A proposal awaiting Mode 2 approval.",
        },
    )
    assert first.status_code == 201
    first_id = first.json()["proposal"]["id"]
    second = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": other_api_key},
        json={
            "token_address": "0xQUEUEB",
            "chain": "base",
            "symbol": "QB",
            "amount_usd": 200,
            "score": 75,
            "rationale": "Tenant B proposal awaiting Mode 2 approval.",
        },
    )
    assert second.status_code == 201
    second_id = second.json()["proposal"]["id"]

    unauthenticated = client.get("/api/v1/copilot/approval-queue")
    assert unauthenticated.status_code == 401

    queue = client.get("/api/v1/copilot/approval-queue", headers={"Authorization": f"Bearer {token}"})
    assert queue.status_code == 200
    payload = queue.json()
    assert payload["pending_count"] == 1
    assert payload["contract"] == {
        "queue": "mode2_approval_queue",
        "approval_result": "paper_simulated_intent",
        "rejection_result": "audit_only_no_execution",
        "paper_only": True,
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "live_funds_enabled": False,
        "autonomous_signing_enabled": False,
    }
    assert [proposal["id"] for proposal in payload["approvals"]] == [first_id]

    other_queue = client.get(
        "/api/v1/copilot/approval-queue", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert other_queue.status_code == 200
    assert [proposal["id"] for proposal in other_queue.json()["approvals"]] == [second_id]


def test_mode2_approval_queue_approve_records_paper_only_intent():
    token, api_key, org_id = _signup_login_key("mode2-queue-approve")
    created = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "base",
            "symbol": "SAFE",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Approval must execute only a paper simulation.",
        },
    )
    assert created.status_code == 201
    proposal_id = created.json()["proposal"]["id"]

    approved = client.post(
        f"/api/v1/copilot/proposals/{proposal_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert approved.status_code == 200
    proposal = approved.json()["proposal"]
    assert proposal["status"] == "executed"
    assert proposal["mode"] == "paper"
    assert proposal["execution_payload"]["trade_id"]

    queue = client.get("/api/v1/copilot/approval-queue", headers={"Authorization": f"Bearer {token}"})
    assert queue.status_code == 200
    assert queue.json()["approvals"] == []

    with SessionLocal() as db:
        trade = db.query(SimulatedTrade).filter(SimulatedTrade.org_id == org_id).one()
        assert trade.token_address == "0xSAFE"
        assert trade.status == "OPEN"
        audit = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == org_id,
                AuditLog.action == "copilot.proposal.approved",
                AuditLog.target_id == str(proposal_id),
            )
            .one()
        )
        assert audit.after["execution"] == "paper_simulated"
        assert audit.after["trade_id"] == proposal["execution_payload"]["trade_id"]


def test_mode2_approval_queue_rejection_smoke(monkeypatch):
    token, _api_key, org_id = _signup_login_key("mode2-reject")

    def fake_candidates(payload):
        assert payload.execution_mode == "copilot"
        return [
            {
                "token_address": "0xQUEUE",
                "chain": "base",
                "symbol": "QUEUE",
                "volume_24h": 500_000,
                "momentum_h1": 8,
                "liquidity_usd": 100_000,
                "price_usd": 1.25,
            },
            {
                "token_address": "0xKEEP",
                "chain": "base",
                "symbol": "KEEP",
                "volume_24h": 450_000,
                "momentum_h1": 7,
                "liquidity_usd": 90_000,
                "price_usd": 1.10,
            },
        ]

    def fake_audit(token_address: str, chain: str = "evm") -> dict[str, object]:
        return {
            "token_address": token_address,
            "is_honeypot": False,
            "risk_score": 10,
            "buy_tax": 0,
            "sell_tax": 0,
            "providers": ["test"],
        }

    monkeypatch.setattr("tothemoon_api.routers.copilot._scan_market_candidates", fake_candidates)
    monkeypatch.setattr("tothemoon_api.routers.copilot.get_token_security_audit", fake_audit)

    scan_response = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "QUEUE", "execution_mode": "copilot", "limit": 2},
    )
    assert scan_response.status_code == 201
    scan_payload = scan_response.json()
    proposal_id = scan_payload["created"][0]["id"]
    scanner_run_id = scan_payload["scanner_run"]["id"]
    assert scan_payload["scanner_run"]["queued_count"] == 2
    assert scan_payload["scanner_run"]["paper_executed_count"] == 0

    reject_response = client.post(
        f"/api/v1/copilot/proposals/{proposal_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "No longer matches the trading thesis."},
    )
    assert reject_response.status_code == 200
    rejected = reject_response.json()["proposal"]
    assert rejected["status"] == "rejected"
    assert rejected["approved_at"] is None
    assert rejected["execution_payload"] == {
        "rejection": {
            "reason": "No longer matches the trading thesis.",
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
        }
    }

    list_response = client.get(
        "/api/v1/copilot/proposals",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_response.status_code == 200
    listed_rejected = next(
        proposal for proposal in list_response.json()["proposals"] if proposal["id"] == proposal_id
    )
    assert listed_rejected["status"] == "rejected"

    detail_response = client.get(
        f"/api/v1/copilot/scanner-runs/{scanner_run_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["scanner_run"]["queued_count"] == 1
    detail_rejected = next(
        proposal for proposal in detail_payload["proposals"] if proposal["id"] == proposal_id
    )
    assert detail_rejected["status"] == "rejected"

    second_response = client.post(
        f"/api/v1/copilot/proposals/{proposal_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second_response.status_code == 409
    assert second_response.json()["detail"] == "Proposal is not pending"

    with SessionLocal() as db:
        proposal = db.query(CopilotProposal).filter(CopilotProposal.id == proposal_id).one()
        scanner_run = db.query(CopilotScannerRun).filter(CopilotScannerRun.id == scanner_run_id).one()
        audit = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == org_id,
                AuditLog.action == "copilot.proposal.rejected",
                AuditLog.target_id == str(proposal_id),
            )
            .one()
        )
        assert proposal.status == "rejected"
        assert proposal.approved_at is None
        assert proposal.execution_payload == {
            "rejection": {
                "reason": "No longer matches the trading thesis.",
                "order_submission_enabled": False,
                "mainnet_order_submission_enabled": False,
            }
        }
        assert scanner_run.queued_count == 1
        assert scanner_run.paper_executed_count == 0
        assert audit.after == {
            "status": "rejected",
            "scanner_run_id": scanner_run_id,
            "rejection_reason": "No longer matches the trading thesis.",
            "execution": "none",
        }


def test_real_mode_blocked_when_flag_off():
    _token, api_key, _org_id = _signup_login_key("real-block")
    response = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Real mode proposal should be rejected until enabled.",
            "mode": "real",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Real mode is disabled for this organization"


def test_copilot_proposal_blocks_unsafe_external_adapter_contract(monkeypatch):
    _token, api_key, _org_id = _signup_login_key("proposal-unsafe-adapter")
    monkeypatch.setattr(
        "tothemoon_api.routers.copilot.get_external_adapter_contract",
        lambda: {
            "mode": "paper_only_read_through",
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": [
                {
                    "name": "unsafe-provider",
                    "capabilities": ["market_data"],
                    "requires_api_key": False,
                    "read_only": False,
                    "supports_order_submission": True,
                    "mainnet_order_submission": False,
                    "public_exposure": False,
                }
            ],
        },
    )

    response = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "symbol": "SAFE",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Unsafe adapters must block 24/7 proposal creation.",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "External adapter contract is not paper-only safe"


def test_copilot_proposal_hard_stops_blocked_mainnet_runtime(monkeypatch):
    _token, api_key, _org_id = _signup_login_key("mainnet-stop")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("ALLOW_MAINNET_TRADING", "true")

    response = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Mainnet runtime must hard-stop before proposal persistence.",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Mainnet runtime is blocked by project policy"


def test_real_mode_approval_stays_manual_when_enabled():
    token, api_key, org_id = _signup_login_key("real-manual")
    with SessionLocal() as db:
        org = db.query(Organization).filter(Organization.id == org_id).one()
        org.real_mode_enabled = True
        db.commit()

    created = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Real mode requires a manual wallet action.",
            "mode": "real",
        },
    )
    assert created.status_code == 201
    proposal_id = created.json()["proposal"]["id"]

    approved = client.post(
        f"/api/v1/copilot/proposals/{proposal_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert approved.status_code == 200
    proposal = approved.json()["proposal"]
    assert proposal["mode"] == "real"
    assert proposal["status"] == "approved_manual_required"
    assert proposal["execution_payload"] is None

    with SessionLocal() as db:
        row = db.query(CopilotProposal).filter(CopilotProposal.id == proposal_id).one()
        assert row.status == "approved_manual_required"
        assert row.execution_payload is None
        audit = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == org_id,
                AuditLog.action == "copilot.proposal.approved",
                AuditLog.target_id == str(proposal_id),
            )
            .one()
        )
        assert audit.after == {
            "status": "approved_manual_required",
            "scanner_run_id": None,
            "execution": "manual_required",
        }


def test_mode2_paper_queue_excludes_real_mode_manual_approvals():
    token, api_key, org_id = _signup_login_key("mode2-real-hidden")
    with SessionLocal() as db:
        org = db.query(Organization).filter(Organization.id == org_id).one()
        org.real_mode_enabled = True
        db.commit()

    paper_response = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xPAPERQUEUE",
            "chain": "base",
            "symbol": "PAPERQ",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Paper Mode 2 approval belongs in the paper-only queue.",
        },
    )
    assert paper_response.status_code == 201
    paper_id = paper_response.json()["proposal"]["id"]
    real_response = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xREALQUEUE",
            "chain": "base",
            "symbol": "REALQ",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Real approvals require a separate manual wallet path.",
            "mode": "real",
        },
    )
    assert real_response.status_code == 201
    real_id = real_response.json()["proposal"]["id"]

    queue_response = client.get(
        "/api/v1/copilot/approval-queue",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert queue_response.status_code == 200
    payload = queue_response.json()
    assert payload["pending_count"] == 1
    assert [proposal["id"] for proposal in payload["approvals"]] == [paper_id]
    assert real_id not in {proposal["id"] for proposal in payload["approvals"]}


def test_copilot_approval_rolls_back_when_quota_blocks_execution():
    token, api_key, org_id = _signup_login_key("proposal-quota")
    with SessionLocal() as db:
        tiny = Plan(
            code=f"copilot-tiny-{uuid4().hex}",
            name="Copilot Tiny",
            monthly_request_limit=0,
            monthly_token_audit_limit=0,
            active_api_key_limit=1,
        )
        db.add(tiny)
        db.flush()
        org = db.query(Organization).filter(Organization.id == org_id).one()
        org.plan_id = tiny.id
        db.commit()

    created = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "symbol": "SAFE",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Quota should block execution before state changes.",
        },
    )
    assert created.status_code == 201
    proposal_id = created.json()["proposal"]["id"]

    approved = client.post(
        f"/api/v1/copilot/proposals/{proposal_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert approved.status_code == 429
    assert approved.json()["detail"] == "Plan quota exceeded"

    with SessionLocal() as db:
        row = db.query(CopilotProposal).filter(CopilotProposal.id == proposal_id).one()
        assert row.status == "pending"
        assert row.approved_at is None
        assert row.execution_payload is None


def test_copilot_approval_fails_when_source_api_key_revoked():
    token, api_key, _org_id = _signup_login_key("proposal-revoked")
    created = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "symbol": "SAFE",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Revoked proposal API key should block execution.",
        },
    )
    assert created.status_code == 201
    proposal_id = created.json()["proposal"]["id"]

    with SessionLocal() as db:
        row = db.query(CopilotProposal).filter(CopilotProposal.id == proposal_id).one()
        api_key_row = db.query(ApiKey).filter(ApiKey.id == row.api_key_id).one()
        api_key_row.revoked_at = row.created_at
        db.commit()

    approved = client.post(
        f"/api/v1/copilot/proposals/{proposal_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert approved.status_code == 409
    assert approved.json()["detail"] == "Proposal API key is unavailable"

    with SessionLocal() as db:
        row = db.query(CopilotProposal).filter(CopilotProposal.id == proposal_id).one()
        assert row.status == "pending"
        assert row.execution_payload is None


def test_copilot_approval_requires_source_key_simulation_scope():
    token, api_key, _org_id = _signup_login_key(
        "proposal-sim-scope",
        scopes=["copilot:proposals"],
    )
    created = client.post(
        "/api/v1/copilot/proposals",
        headers={"X-API-Key": api_key},
        json={
            "token_address": "0xSAFE",
            "chain": "evm",
            "symbol": "SAFE",
            "amount_usd": 100,
            "score": 72,
            "rationale": "Approval should not bypass simulate scope on the source key.",
        },
    )
    assert created.status_code == 201
    proposal_id = created.json()["proposal"]["id"]

    approved = client.post(
        f"/api/v1/copilot/proposals/{proposal_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert approved.status_code == 403
    assert approved.json()["detail"] == "API key scope denied"

    with SessionLocal() as db:
        row = db.query(CopilotProposal).filter(CopilotProposal.id == proposal_id).one()
        assert row.status == "pending"
        assert row.approved_at is None
        assert row.execution_payload is None


def test_circuit_breaker_after_3_failures():
    _token, api_key, org_id = _signup_login_key("breaker")
    with SessionLocal() as db:
        org = db.query(Organization).filter(Organization.id == org_id).one()
        org.real_mode_enabled = True
        db.commit()

    for attempt in range(1, 4):
        response = client.post(
            "/api/v1/copilot/circuit-breaker/failures",
            headers={"X-API-Key": api_key},
        )
        assert response.status_code == 200
        assert response.json()["bot_consecutive_failures"] == attempt

    body = response.json()
    assert body["circuit_breaker_tripped"] is True
    assert body["real_mode_enabled"] is False

    with SessionLocal() as db:
        org = db.query(Organization).filter(Organization.id == org_id).one()
        assert org.real_mode_enabled is False
        assert org.bot_consecutive_failures == 3


def test_copilot_scan_creates_proposals_and_discovery_history(monkeypatch):
    token, _api_key, org_id = _signup_login_key("copilot-scan")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "pairs": [
                    {
                        "chainId": "base",
                        "baseToken": {"address": "0xSAFE", "symbol": "SAFE"},
                        "volume": {"h24": "500000"},
                        "priceChange": {"h1": "8"},
                        "liquidity": {"usd": "100000"},
                        "priceUsd": "1.25",
                    },
                    {
                        "chainId": "base",
                        "baseToken": {"address": "0xSCAM", "symbol": "SCAM"},
                        "volume": {"h24": "600000"},
                        "priceChange": {"h1": "9"},
                        "liquidity": {"usd": "120000"},
                        "priceUsd": "0.01",
                    },
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url: str, *, params: dict[str, object]):
            assert url == "https://api.dexscreener.com/latest/dex/search"
            assert params == {"q": "USDC"}
            return FakeResponse()

    def fake_audit(token_address: str, chain: str = "evm") -> dict[str, object]:
        return {
            "token_address": token_address,
            "is_honeypot": token_address == "0xSCAM",
            "risk_score": 10 if token_address == "0xSAFE" else 99,
            "buy_tax": 0,
            "sell_tax": 0,
            "providers": ["test"],
        }

    monkeypatch.setattr("tothemoon_api.routers.copilot.httpx.Client", FakeClient)
    monkeypatch.setattr("tothemoon_api.routers.copilot.get_token_security_audit", fake_audit)

    response = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "USDC", "execution_mode": "copilot", "limit": 2},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["execution_mode"] == "copilot"
    assert payload["scanner_run"]["query"] == "USDC"
    assert payload["scanner_run"]["execution_mode"] == "copilot"
    assert payload["scanner_run"]["status"] == "completed"
    assert payload["scanner_run"]["candidate_count"] == 2
    assert payload["scanner_run"]["blocked_count"] == 1
    assert payload["scanner_run"]["queued_count"] == 1
    assert payload["scanner_run"]["paper_executed_count"] == 0
    assert [item["token_address"] for item in payload["created"]] == ["0xSAFE"]
    assert payload["created"][0]["status"] == "pending"
    assert payload["skipped"][0]["token_address"] == "0xSCAM"
    assert payload["skipped"][0]["reason"] == "honeypot_detected"

    listed = client.get(
        "/api/v1/copilot/scanner-runs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listed.status_code == 200
    assert listed.json()["scanner_runs"][0]["id"] == payload["scanner_run"]["id"]

    with SessionLocal() as db:
        proposals = db.query(CopilotProposal).filter(CopilotProposal.org_id == org_id).all()
        runs = db.query(CopilotScannerRun).filter(CopilotScannerRun.org_id == org_id).all()
        history = (
            db.query(TokenDiscoveryHistory)
            .filter(TokenDiscoveryHistory.org_id == org_id)
            .order_by(TokenDiscoveryHistory.token_address)
            .all()
        )
        assert [row.token_address for row in proposals] == ["0xSAFE"]
        assert [(row.execution_mode, row.status, row.queued_count, row.paper_executed_count) for row in runs] == [
            ("copilot", "completed", 1, 0)
        ]
        assert [row.token_address for row in history] == ["0xSAFE", "0xSCAM"]


def test_copilot_scan_records_sanitized_failed_run(monkeypatch):
    token, _api_key, org_id = _signup_login_key("failed-scan")
    with SessionLocal() as db:
        org = db.query(Organization).filter(Organization.id == org_id).one()
        org.real_mode_enabled = True
        org.bot_consecutive_failures = 2
        db.commit()

    def fail_candidates(payload):
        raise RuntimeError("upstream rejected Authorization=Bearer scanner.secret.token")

    monkeypatch.setattr("tothemoon_api.routers.copilot._scan_market_candidates", fail_candidates)

    with pytest.raises(RuntimeError, match=r"scanner\.secret\.token"):
        client.post(
            "/api/v1/copilot/scan",
            headers={"Authorization": f"Bearer {token}"},
            json={"query": "USDC", "execution_mode": "copilot", "limit": 2},
        )

    listed = client.get(
        "/api/v1/copilot/scanner-runs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listed.status_code == 200
    scanner_run = listed.json()["scanner_runs"][0]
    assert scanner_run["status"] == "failed"
    assert scanner_run["candidate_count"] == 0
    assert scanner_run["completed_at"] is not None
    assert scanner_run["error_summary"] == "RuntimeError: upstream rejected [REDACTED]"
    assert "scanner.secret.token" not in str(scanner_run)

    with SessionLocal() as db:
        run = db.query(CopilotScannerRun).filter(CopilotScannerRun.org_id == org_id).one()
        assert run.status == "failed"
        assert run.error_summary == "RuntimeError: upstream rejected [REDACTED]"
        org = db.query(Organization).filter(Organization.id == org_id).one()
        assert org.bot_consecutive_failures == 3
        assert org.real_mode_enabled is False
        audit = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == org_id,
                AuditLog.action == "copilot.scan.failed",
                AuditLog.target_id == str(run.id),
            )
            .one()
        )
        assert audit.actor_type == "user"
        assert audit.after == {
            "scanner_run_id": run.id,
            "query": "USDC",
            "execution_mode": "copilot",
            "candidate_count": 0,
            "error_summary": "RuntimeError: upstream rejected [REDACTED]",
            "bot_consecutive_failures": 3,
            "real_mode_enabled": False,
        }
        assert "scanner.secret.token" not in str(audit.after)


def test_paper_auto_scan_executes_paper_trade_without_order_submission(monkeypatch):
    token, _api_key, org_id = _signup_login_key("paper-auto-scan")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "pairs": [
                    {
                        "chainId": "base",
                        "baseToken": {"address": "0xPAPER", "symbol": "PAPER"},
                        "volume": {"h24": "700000"},
                        "priceChange": {"h1": "11"},
                        "liquidity": {"usd": "150000"},
                        "priceUsd": "2.50",
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url: str, *, params: dict[str, object]):
            assert url == "https://api.dexscreener.com/latest/dex/search"
            assert params == {"q": "USDC"}
            return FakeResponse()

    def fake_audit(token_address: str, chain: str = "evm") -> dict[str, object]:
        return {
            "token_address": token_address,
            "is_honeypot": False,
            "risk_score": 8,
            "buy_tax": 0,
            "sell_tax": 0,
            "providers": ["test"],
        }

    monkeypatch.setattr("tothemoon_api.routers.copilot.httpx.Client", FakeClient)
    monkeypatch.setattr("tothemoon_api.routers.copilot.get_token_security_audit", fake_audit)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["orderSubmissionEnabled"] is False

    response = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "USDC", "execution_mode": "paper_auto", "limit": 1, "amount_usd": 42},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["execution_mode"] == "paper_auto"
    assert payload["scanner_run"]["execution_mode"] == "paper_auto"
    assert payload["scanner_run"]["candidate_count"] == 1
    assert payload["scanner_run"]["blocked_count"] == 0
    assert payload["scanner_run"]["queued_count"] == 0
    assert payload["scanner_run"]["paper_executed_count"] == 1
    assert len(payload["created"]) == 1
    created = payload["created"][0]
    assert created["token_address"] == "0xPAPER"
    assert created["mode"] == "paper"
    assert created["status"] == "executed"
    assert created["execution_payload"]["status"] == "SUCCESS"

    with SessionLocal() as db:
        proposal = db.query(CopilotProposal).filter(CopilotProposal.org_id == org_id).one()
        run = db.query(CopilotScannerRun).filter(CopilotScannerRun.org_id == org_id).one()
        trade = db.query(SimulatedTrade).filter(SimulatedTrade.org_id == org_id).one()
        assert proposal.status == "executed"
        assert proposal.mode == "paper"
        assert proposal.execution_payload["status"] == "SUCCESS"
        assert run.execution_mode == "paper_auto"
        assert run.paper_executed_count == 1
        assert trade.token_address == "0xPAPER"
        assert trade.api_key_id == proposal.api_key_id


def test_scanner_runs_can_include_strategy_profile_and_paper_outcome(monkeypatch):
    token, _api_key, org_id = _signup_login_key("strategy-outcome")

    def fake_candidates(payload):
        assert payload.strategy_profile == "ai_momentum_test"
        return [
            {
                "token_address": "0xAILEARN",
                "chain": "base",
                "symbol": "AIL",
                "volume_24h": 500_000,
                "momentum_h1": 9,
                "liquidity_usd": 150_000,
                "market_cap_usd": 2_000_000,
                "price_usd": 1.0,
            }
        ]

    def fake_audit(token_address: str, chain: str = "evm") -> dict[str, object]:
        return {
            "token_address": token_address,
            "is_honeypot": False,
            "risk_score": 5,
            "buy_tax": 0,
            "sell_tax": 0,
            "providers": ["test"],
        }

    monkeypatch.setattr("tothemoon_api.routers.copilot._scan_market_candidates", fake_candidates)
    monkeypatch.setattr("tothemoon_api.routers.copilot.get_token_security_audit", fake_audit)
    monkeypatch.setattr("tothemoon_api.simulation.get_token_security_audit", fake_audit)
    monkeypatch.setattr(
        "tothemoon_api.simulation.get_token_market_data",
        lambda token_address: {"token_address": token_address, "chain": "base", "price": 1.05},
    )

    created = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": "AI",
            "strategy_profile": "ai_momentum_test",
            "strategy_family": "F",
            "execution_mode": "paper_auto",
            "limit": 1,
            "amount_usd": 3,
        },
    )

    assert created.status_code == 201
    listed = client.get(
        "/api/v1/copilot/scanner-runs?include_outcomes=true",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert listed.status_code == 200
    scanner_run = listed.json()["scanner_runs"][0]
    assert scanner_run["thresholds"]["strategy_profile"] == "ai_momentum_test"
    assert scanner_run["thresholds"]["strategy_family"] == "F"
    assert scanner_run["paper_outcome"] == {
        **scanner_run["paper_outcome"],
        "trade_count": 1,
        "open_count": 1,
        "closed_count": 0,
        "volume_brl": 3.0,
    }
    assert scanner_run["paper_outcome"]["total_pnl_brl"] < 3.0
    assert scanner_run["paper_outcome"]["valuation_errors"] == 0

    outcomes = client.get(
        "/api/v1/copilot/strategy-outcomes?days=30",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert outcomes.status_code == 200
    outcome_payload = outcomes.json()
    assert outcome_payload["outcomes"][0] == {
        **outcome_payload["outcomes"][0],
        "profile_name": "ai_momentum_test",
        "family": "F",
        "decision": "approved",
        "classification": None,
    }
    assert outcome_payload["outcomes"][0]["paper_position_id"] is not None
    assert outcome_payload["by_profile"]["ai_momentum_test"]["outcomes"] == 1

    with SessionLocal() as db:
        runs = db.query(CopilotScannerRun).filter(CopilotScannerRun.org_id == org_id).all()
        assert len(runs) == 1
        assert runs[0].thresholds_json["strategy_profile"] == "ai_momentum_test"


def test_paper_cycle_status_and_reset_close_open_positions(monkeypatch):
    token, _api_key, org_id = _signup_login_key("paper-cycle-reset")

    def fake_candidates(payload):
        return [
            {
                "token_address": "0xCYCLE",
                "chain": "base",
                "symbol": "CYC",
                "volume_24h": 500_000,
                "momentum_h1": 5,
                "liquidity_usd": 150_000,
                "market_cap_usd": 2_000_000,
                "price_usd": 1.0,
            }
        ]

    def fake_audit(token_address: str, chain: str = "evm") -> dict[str, object]:
        return {
            "token_address": token_address,
            "is_honeypot": False,
            "risk_score": 5,
            "buy_tax": 0,
            "sell_tax": 0,
            "providers": ["test"],
        }

    monkeypatch.setattr("tothemoon_api.routers.copilot._scan_market_candidates", fake_candidates)
    monkeypatch.setattr("tothemoon_api.routers.copilot.get_token_security_audit", fake_audit)
    monkeypatch.setattr("tothemoon_api.simulation.get_token_security_audit", fake_audit)
    monkeypatch.setattr(
        "tothemoon_api.simulation.get_token_market_data",
        lambda token_address: {"token_address": token_address, "chain": "base", "price": 1.05},
    )

    created = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "SOL", "execution_mode": "paper_auto", "limit": 1, "amount_usd": 30},
    )
    assert created.status_code == 201

    status = client.get("/api/v1/copilot/paper-cycle", headers={"Authorization": f"Bearer {token}"})
    assert status.status_code == 200
    assert status.json()["paper_cycle"]["open_capital_brl"] == 30.0
    assert status.json()["paper_cycle"]["target_positions_per_cycle"] == 10
    assert status.json()["paper_cycle"]["position_pct_per_trade"] == 10.0

    reset = client.post(
        "/api/v1/copilot/paper-cycle/reset",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert reset.status_code == 200
    assert len(reset.json()["closed"]) == 1
    assert reset.json()["closed"][0]["strategy_profile"] == "default"
    assert reset.json()["closed"][0]["entry_price"] is not None
    assert reset.json()["closed"][0]["exit_price"] is not None
    assert reset.json()["paper_cycle"]["open_capital_brl"] == 0.0

    with SessionLocal() as db:
        trade = db.query(SimulatedTrade).filter(SimulatedTrade.org_id == org_id).one()
        assert trade.status == "CLOSED"
        outcome = db.query(StrategyOutcome).filter(StrategyOutcome.org_id == org_id).one()
        assert outcome.paper_position_id == trade.id
        assert outcome.paper_pnl_brl == trade.realized_pnl_usd
        assert outcome.metadata_json["closed_by"] == "paper_cycle_reset"


def test_paper_slot_allocation_reserves_three_onchain_scout_slots():
    from decimal import Decimal
    from types import SimpleNamespace

    from tothemoon_api.routers.copilot import _paper_slot_allocation_viability

    _token, _api_key, org_id = _signup_login_key("paper-slot-allocation")

    with SessionLocal() as db:
        for index in range(3):
            trade = SimulatedTrade(
                org_id=org_id,
                token_address=f"0xSCOUT{index}",
                side="BUY",
                amount=Decimal("30"),
                entry_price=Decimal("1"),
                fees_total=Decimal("0.05"),
                slippage_bps=5,
                status="OPEN",
            )
            db.add(trade)
            db.flush()
            db.add(
                StrategyOutcome(
                    org_id=org_id,
                    profile_name=f"fresh_scout_{index}",
                    family="B",
                    paper_position_id=trade.id,
                    candidate_address=trade.token_address,
                    chain="base",
                    decision="approved",
                    entry_price=Decimal("1"),
                    metadata_json={},
                )
            )
        db.commit()

        settings = SimpleNamespace(paper_liquid_slots=7, paper_onchain_scout_slots=3)
        scout = _paper_slot_allocation_viability(
            org_id=org_id,
            db=db,
            strategy_family="B",
            settings=settings,
        )
        liquid = _paper_slot_allocation_viability(
            org_id=org_id,
            db=db,
            strategy_family="A",
            settings=settings,
        )

    assert scout == {
        **scout,
        "allowed": False,
        "reason": "paper_onchain_scout_slots_full",
        "category": "onchain_scout",
        "open_slots": 3,
        "slot_budget": 3,
    }
    assert liquid == {
        **liquid,
        "allowed": True,
        "reason": "allowed",
        "category": "liquid_binance",
        "open_slots": 0,
        "slot_budget": 7,
    }


def test_paper_symbol_allocation_limits_duplicate_open_symbols():
    from decimal import Decimal
    from types import SimpleNamespace

    from tothemoon_api.routers.copilot import _paper_symbol_allocation_viability

    _token, _api_key, org_id = _signup_login_key("paper-symbol-allocation")

    with SessionLocal() as db:
        for index in range(2):
            trade = SimulatedTrade(
                org_id=org_id,
                token_address=f"BNB-{index}",
                side="BUY",
                amount=Decimal("20"),
                entry_price=Decimal("1"),
                fees_total=Decimal("0.02"),
                slippage_bps=5,
                status="OPEN",
            )
            db.add(trade)
            db.flush()
            db.add(
                StrategyOutcome(
                    org_id=org_id,
                    profile_name=f"bnb_{index}",
                    family="A",
                    paper_position_id=trade.id,
                    candidate_address=trade.token_address,
                    chain="binance_spot",
                    decision="approved",
                    entry_price=Decimal("1"),
                    metadata_json={"symbol": "BNB"},
                )
            )
        db.commit()

        settings = SimpleNamespace(paper_max_open_per_symbol=2)
        bnb = _paper_symbol_allocation_viability(
            org_id=org_id,
            db=db,
            candidate={"symbol": "BNB"},
            settings=settings,
        )
        sol = _paper_symbol_allocation_viability(
            org_id=org_id,
            db=db,
            candidate={"symbol": "SOL"},
            settings=settings,
        )

    assert bnb == {
        **bnb,
        "allowed": False,
        "reason": "paper_symbol_slots_full",
        "symbol": "BNB",
        "open_symbol_positions": 2,
        "symbol_position_budget": 2,
    }
    assert sol == {
        **sol,
        "allowed": True,
        "reason": "allowed",
        "symbol": "SOL",
        "open_symbol_positions": 0,
        "symbol_position_budget": 2,
    }


def test_paper_auto_records_late_simulation_honeypot_as_blocked(monkeypatch):
    token, _api_key, org_id = _signup_login_key("late-sim-honeypot")

    def fake_candidates(payload):
        return [
            {
                "token_address": "0xLATEHONEYPOT",
                "chain": "base",
                "symbol": "LATE",
                "volume_24h": 500_000,
                "momentum_h1": 5,
                "liquidity_usd": 150_000,
                "market_cap_usd": 2_000_000,
                "price_usd": 1.0,
            }
        ]

    def safe_router_audit(token_address: str, chain: str = "evm") -> dict[str, object]:
        return {
            "token_address": token_address,
            "is_honeypot": False,
            "risk_score": 5,
            "buy_tax": 0,
            "sell_tax": 0,
            "providers": ["router-test"],
        }

    def blocked_simulation_audit(token_address: str, chain: str = "evm") -> dict[str, object]:
        return {
            "token_address": token_address,
            "is_honeypot": True,
            "risk_score": 99,
            "buy_tax": 0,
            "sell_tax": 0,
            "providers": ["simulation-test"],
        }

    monkeypatch.setattr("tothemoon_api.routers.copilot._scan_market_candidates", fake_candidates)
    monkeypatch.setattr("tothemoon_api.routers.copilot.get_token_security_audit", safe_router_audit)
    monkeypatch.setattr(
        "tothemoon_api.simulation.get_token_security_audit",
        blocked_simulation_audit,
    )

    response = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "LATE", "execution_mode": "paper_auto", "limit": 1, "amount_usd": 30},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["scanner_run"]["candidate_count"] == 1
    assert payload["scanner_run"]["blocked_count"] == 1
    assert payload["scanner_run"]["paper_executed_count"] == 0
    assert payload["skipped"] == [
        {"token_address": "0xLATEHONEYPOT", "reason": "Honeypot detected, blocked", "score": 48.75}
    ]

    with SessionLocal() as db:
        discovery = db.query(TokenDiscoveryHistory).filter(TokenDiscoveryHistory.org_id == org_id).one()
        assert discovery.token_address == "0xLATEHONEYPOT"
        proposal = db.query(CopilotProposal).filter(CopilotProposal.org_id == org_id).one()
        assert proposal.status == "rejected"
        assert proposal.execution_payload["stage"] == "simulate_trade"
        assert db.query(SimulatedTrade).filter(SimulatedTrade.org_id == org_id).count() == 0
        outcome = db.query(StrategyOutcome).filter(StrategyOutcome.org_id == org_id).one()
        assert outcome.decision == "rejected_audit"
        assert outcome.classification == "rug"


def test_real_approval_scan_is_blocked_until_org_gate_enabled(monkeypatch):
    token, _api_key, _org_id = _signup_login_key("real-scan-blocked")

    def fail_scan(_payload):
        raise AssertionError("real approval gate must stop before market provider scans")

    monkeypatch.setattr("tothemoon_api.routers.copilot._scan_market_candidates", fail_scan)

    response = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": "USDC",
            "execution_mode": "real_approval",
            "wallet_address": "0x000000000000000000000000000000000000001c",
            "wallet_chain_id": "0x1",
        },
    )

    assert response.status_code == 403
    assert "Real Mode 2 approval is disabled" in response.json()["detail"]


def test_real_approval_scan_creates_manual_wallet_proposal_without_trade(monkeypatch):
    token, _api_key, org_id = _signup_login_key("real-scan-enabled")
    wallet = "0x000000000000000000000000000000000000001c"

    with SessionLocal() as db:
        org = db.query(Organization).filter(Organization.id == org_id).one()
        org.real_mode_enabled = True
        org.real_mode_approved_at = None
        db.commit()

    def fake_candidates(_payload):
        return [
            {
                "token_address": "0xREAL",
                "chain": "base",
                "symbol": "REAL",
                "volume_24h": 800000,
                "momentum_h1": 12,
                "liquidity_usd": 250000,
                "price_usd": 2,
            }
        ]

    def fake_audit(token_address: str, chain: str = "evm") -> dict[str, object]:
        return {
            "token_address": token_address,
            "is_honeypot": False,
            "risk_score": 7,
            "buy_tax": 0,
            "sell_tax": 0,
            "providers": ["test"],
        }

    monkeypatch.setattr("tothemoon_api.routers.copilot._scan_market_candidates", fake_candidates)
    monkeypatch.setattr("tothemoon_api.routers.copilot.get_token_security_audit", fake_audit)

    response = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": "USDC",
            "execution_mode": "real_approval",
            "limit": 1,
            "wallet_address": wallet,
            "wallet_chain_id": "0x1",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["execution_mode"] == "real_approval"
    assert payload["scanner_run"]["execution_mode"] == "real_approval"
    assert payload["scanner_run"]["queued_count"] == 1
    assert payload["scanner_run"]["paper_executed_count"] == 0
    proposal = payload["created"][0]
    assert proposal["mode"] == "real"
    assert proposal["status"] == "pending"
    assert proposal["execution_payload"]["manual_gate"] == "metamask_signature_required"
    assert proposal["execution_payload"]["wallet_address"] == wallet
    assert proposal["execution_payload"]["order_submission_enabled"] is False

    approved = client.post(
        f"/api/v1/copilot/proposals/{proposal['id']}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "wallet_address": wallet,
            "wallet_chain_id": "0x1",
            "signed_message": "TTM Mode 2 manual approval",
            "wallet_signature": "0xsigned",
        },
    )

    assert approved.status_code == 200
    approved_payload = approved.json()["proposal"]
    assert approved_payload["status"] == "approved_manual_required"
    assert approved_payload["execution_payload"]["manual_approval"]["wallet_address"] == wallet
    assert approved_payload["execution_payload"]["manual_approval"]["order_submission_enabled"] is False

    run_detail = client.get(
        f"/api/v1/copilot/scanner-runs/{payload['scanner_run']['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run_detail.status_code == 200
    assert run_detail.json()["scanner_run"]["queued_count"] == 0
    assert run_detail.json()["scanner_run"]["paper_executed_count"] == 0

    with SessionLocal() as db:
        run = db.query(CopilotScannerRun).filter(CopilotScannerRun.org_id == org_id).one()
        proposal_row = db.query(CopilotProposal).filter(CopilotProposal.org_id == org_id).one()
        trades = db.query(SimulatedTrade).filter(SimulatedTrade.org_id == org_id).all()
        assert run.execution_mode == "real_approval"
        assert run.queued_count == 0
        assert run.paper_executed_count == 0
        assert proposal_row.mode == "real"
        assert proposal_row.status == "approved_manual_required"
        assert trades == []


def test_copilot_scanner_runs_are_tenant_scoped_and_secret_safe(monkeypatch):
    token, _api_key, org_id = _signup_login_key("scanner-runs-a")
    other_token, _other_api_key, other_org_id = _signup_login_key("scanner-runs-b")

    def fake_candidates(payload):
        assert payload.query == "MOON"
        return [
            {
                "token_address": "0xMOON",
                "chain": "base",
                "symbol": "MOON",
                "volume_24h": 900000,
                "momentum_h1": 12,
                "liquidity_usd": 300000,
                "price_usd": 2,
            },
            {
                "token_address": "0xBLOCKED",
                "chain": "base",
                "symbol": "BAD",
                "volume_24h": 800000,
                "momentum_h1": 10,
                "liquidity_usd": 250000,
                "price_usd": 1,
            },
        ]

    def fake_status(org, db):
        return {
            "runtime_mode": "paper",
            "mainnet_order_submission_enabled": False,
            "safeguards": {"adapter_contract_enforced": True},
            "readiness": {"blocking_reasons": []},
            "provider_readiness": {
                "status": "healthy",
                "paper_only": True,
                "order_submission_enabled": False,
                "mainnet_order_submission_enabled": False,
                "api_key": "raw-provider-secret",
            },
            "providers": {
                "gemini": {
                    "configured": True,
                    "source": "org",
                    "key_hint": "sk-...abcd",
                    "api_key": "raw-gemini-secret",
                }
            },
        }

    def fake_audit(token_address: str, chain: str = "evm") -> dict[str, object]:
        return {
            "token_address": token_address,
            "is_honeypot": token_address == "0xBLOCKED",
            "risk_score": 5 if token_address == "0xMOON" else 95,
            "buy_tax": 0,
            "sell_tax": 0,
            "providers": ["test"],
        }

    monkeypatch.setattr("tothemoon_api.routers.copilot._scan_market_candidates", fake_candidates)
    monkeypatch.setattr("tothemoon_api.routers.copilot._paper_bot_status", fake_status)
    monkeypatch.setattr("tothemoon_api.routers.copilot.get_token_security_audit", fake_audit)

    response = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "MOON", "execution_mode": "copilot", "limit": 2},
    )

    assert response.status_code == 201
    scanner_run = response.json()["scanner_run"]
    assert scanner_run["execution_mode"] == "copilot"
    assert scanner_run["candidate_count"] == 2
    assert scanner_run["blocked_count"] == 1
    assert scanner_run["queued_count"] == 1
    assert scanner_run["paper_executed_count"] == 0
    assert scanner_run["provider_readiness_summary"] == {
        "status": "healthy",
        "paper_only": True,
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "blocking_reasons": [],
        "gemini": {"configured": True, "source": "org", "key_hint": "sk-...abcd"},
    }
    assert "raw-provider-secret" not in str(scanner_run)
    assert "raw-gemini-secret" not in str(scanner_run)

    own_list = client.get(
        "/api/v1/copilot/scanner-runs",
        headers={"Authorization": f"Bearer {token}"},
    )
    other_list = client.get(
        "/api/v1/copilot/scanner-runs",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    own_detail = client.get(
        f"/api/v1/copilot/scanner-runs/{scanner_run['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    other_detail = client.get(
        f"/api/v1/copilot/scanner-runs/{scanner_run['id']}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert own_list.status_code == 200
    assert other_list.status_code == 200
    assert own_detail.status_code == 200
    assert other_detail.status_code == 404
    assert [item["id"] for item in own_list.json()["scanner_runs"]] == [scanner_run["id"]]
    assert other_list.json()["scanner_runs"] == []
    detail_payload = own_detail.json()
    assert detail_payload["org_id"] == org_id
    assert detail_payload["scanner_run"] == scanner_run
    assert [proposal["scanner_run_id"] for proposal in detail_payload["proposals"]] == [
        scanner_run["id"]
    ]
    assert detail_payload["proposals"][0]["token_address"] == "0xMOON"
    assert detail_payload["proposals"][0]["status"] == "pending"

    with SessionLocal() as db:
        rows = db.query(CopilotScannerRun).filter(CopilotScannerRun.org_id == org_id).all()
        assert [(row.execution_mode, row.blocked_count, row.paper_executed_count) for row in rows] == [
            ("copilot", 1, 0)
        ]
        assert "raw-provider-secret" not in str(rows[0].provider_readiness_summary)
        assert "raw-gemini-secret" not in str(rows[0].provider_readiness_summary)
        decisions = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == org_id,
                AuditLog.action == "paper_execution.decision_recorded",
            )
            .order_by(AuditLog.id.asc())
            .all()
        )
        assert [row.target_type for row in decisions] == [
            "copilot_proposal",
            "token_discovery_history",
        ]
        assert [row.after["decision"] for row in decisions] == ["queued", "blocked"]
        assert [row.after["reason"] for row in decisions] == [
            "copilot_review_required",
            "honeypot_detected",
        ]
        assert all(row.after["scanner_run_id"] == scanner_run["id"] for row in decisions)
        assert {row.after["token_address"] for row in decisions} == {"0xMOON", "0xBLOCKED"}
        assert "raw-provider-secret" not in str([row.after for row in decisions])
        assert "raw-gemini-secret" not in str([row.after for row in decisions])
        other_decisions = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == other_org_id,
                AuditLog.action == "paper_execution.decision_recorded",
            )
            .all()
        )
        assert other_decisions == []


def test_successful_copilot_scan_resets_backend_failure_streak(monkeypatch):
    token, _api_key, org_id = _signup_login_key("scanner-run-recovers")

    with SessionLocal() as db:
        org = db.query(Organization).filter(Organization.id == org_id).one()
        org.bot_consecutive_failures = 2
        db.commit()

    def fake_candidates(payload):
        assert payload.query == "MOON"
        return [
            {
                "token_address": "0xMOON",
                "chain": "base",
                "symbol": "MOON",
                "volume_24h": 900000,
                "momentum_h1": 12,
                "liquidity_usd": 300000,
                "price_usd": 2,
            }
        ]

    def fake_audit(token_address: str, chain: str = "evm") -> dict[str, object]:
        return {
            "token_address": token_address,
            "is_honeypot": False,
            "risk_score": 5,
            "buy_tax": 0,
            "sell_tax": 0,
            "providers": ["test"],
        }

    monkeypatch.setattr("tothemoon_api.routers.copilot._scan_market_candidates", fake_candidates)
    monkeypatch.setattr("tothemoon_api.routers.copilot.get_token_security_audit", fake_audit)

    response = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "MOON", "execution_mode": "copilot", "limit": 1},
    )

    assert response.status_code == 201
    assert response.json()["scanner_run"]["queued_count"] == 1
    with SessionLocal() as db:
        org = db.query(Organization).filter(Organization.id == org_id).one()
        assert org.bot_consecutive_failures == 0


def test_latest_paper_scanner_evidence_returns_null_without_runs():
    token, _api_key, org_id = _signup_login_key("paper-evidence-empty")

    response = client.get(
        "/api/v1/copilot/scanner-runs/latest-paper-evidence",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"org_id": org_id, "evidence": None}


def test_latest_paper_scanner_evidence_ignores_running_runs():
    token, _api_key, org_id = _signup_login_key("paper-evidence-running")
    with SessionLocal() as db:
        api_key = db.query(ApiKey).filter(ApiKey.org_id == org_id).one()
        completed = CopilotScannerRun(
            org_id=org_id,
            api_key_id=api_key.id,
            query="DONE",
            execution_mode="paper_auto",
            status="completed",
            thresholds_json={"limit": 1},
            candidate_count=1,
            blocked_count=0,
            queued_count=0,
            paper_executed_count=1,
            provider_readiness_summary={"status": "healthy", "paper_only": True},
        )
        running = CopilotScannerRun(
            org_id=org_id,
            api_key_id=api_key.id,
            query="RUNNING",
            execution_mode="paper_auto",
            status="running",
            thresholds_json={"limit": 1},
            provider_readiness_summary={"status": "pending", "api_key": "raw-provider-secret"},
        )
        db.add_all([completed, running])
        db.commit()
        completed_id = completed.id

    response = client.get(
        "/api/v1/copilot/scanner-runs/latest-paper-evidence",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    evidence = response.json()["evidence"]
    assert evidence["run_id"] == completed_id
    assert evidence["status"] == "completed"
    assert evidence["paper_execution_result"]["paper_executed_count"] == 1
    assert "raw-provider-secret" not in str(evidence)


def test_latest_paper_scanner_evidence_is_tenant_scoped_and_redacted(monkeypatch):
    token, _api_key, org_id = _signup_login_key("paper-evidence-a")
    other_token, _other_api_key, other_org_id = _signup_login_key("paper-evidence-b")

    def fake_candidates(payload):
        assert payload.query == "EVIDENCE"
        return [
            {
                "token_address": "0xEVIDENCE",
                "chain": "base",
                "symbol": "EVD",
                "volume_24h": 950000,
                "momentum_h1": 15,
                "liquidity_usd": 450000,
                "price_usd": 4,
            }
        ]

    def fake_status(org, db):
        return {
            "runtime_mode": "paper",
            "mainnet_order_submission_enabled": False,
            "safeguards": {"adapter_contract_enforced": True},
            "readiness": {"blocking_reasons": []},
            "provider_readiness": {
                "status": "healthy",
                "paper_only": True,
                "order_submission_enabled": False,
                "mainnet_order_submission_enabled": False,
                "api_key": "raw-provider-secret",
            },
            "providers": {
                "gemini": {
                    "configured": True,
                    "source": "org",
                    "key_hint": "sk-...abcd",
                    "secret": "raw-gemini-secret",
                }
            },
        }

    def fake_audit(token_address: str, chain: str = "evm") -> dict[str, object]:
        return {
            "token_address": token_address,
            "is_honeypot": False,
            "risk_score": 4,
            "buy_tax": 0,
            "sell_tax": 0,
            "providers": ["test"],
        }

    monkeypatch.setattr("tothemoon_api.routers.copilot._scan_market_candidates", fake_candidates)
    monkeypatch.setattr("tothemoon_api.routers.copilot._paper_bot_status", fake_status)
    monkeypatch.setattr("tothemoon_api.routers.copilot.get_token_security_audit", fake_audit)

    created = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "EVIDENCE", "execution_mode": "paper_auto", "limit": 1, "amount_usd": 42},
    )

    assert created.status_code == 201
    scanner_run = created.json()["scanner_run"]
    response = client.get(
        "/api/v1/copilot/scanner-runs/latest-paper-evidence",
        headers={"Authorization": f"Bearer {token}"},
    )
    other_response = client.get(
        "/api/v1/copilot/scanner-runs/latest-paper-evidence",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 200
    assert other_response.status_code == 200
    payload = response.json()
    assert payload["org_id"] == org_id
    evidence = payload["evidence"]
    assert evidence == {
        **evidence,
        "run_id": scanner_run["id"],
        "mode": "paper_auto",
        "status": "completed",
        "paper_execution_result": {
            "paper_only": True,
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "candidate_count": 1,
            "blocked_count": 0,
            "queued_count": 0,
            "paper_executed_count": 1,
            "error_summary": None,
        },
        "provider_context": {
            "status": "healthy",
            "paper_only": True,
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "blocking_reasons": [],
            "gemini": {"configured": True, "source": "org", "key_hint": "sk-...abcd"},
        },
    }
    assert evidence["audit_evidence_timestamp"] == scanner_run["completed_at"]
    assert "raw-provider-secret" not in str(payload)
    assert "raw-gemini-secret" not in str(payload)
    assert other_response.json() == {"org_id": other_org_id, "evidence": None}


def test_latest_paper_scanner_evidence_includes_failed_mode_1_run(monkeypatch):
    token, _api_key, org_id = _signup_login_key("paper-evidence-failed")

    def failing_candidates(payload):
        assert payload.query == "FAILSAFE"
        raise RuntimeError("dex api_key=raw-provider-secret failed")

    monkeypatch.setattr("tothemoon_api.routers.copilot._scan_market_candidates", failing_candidates)

    no_raise_client = TestClient(app, raise_server_exceptions=False)
    created = no_raise_client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "FAILSAFE", "execution_mode": "paper_auto", "limit": 1},
    )
    assert created.status_code == 500

    response = client.get(
        "/api/v1/copilot/scanner-runs/latest-paper-evidence",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["org_id"] == org_id
    evidence = payload["evidence"]
    assert evidence == {
        **evidence,
        "mode": "paper_auto",
        "status": "failed",
        "paper_execution_result": {
            "paper_only": True,
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "candidate_count": 0,
            "blocked_count": 0,
            "queued_count": 0,
            "paper_executed_count": 0,
            "error_summary": "RuntimeError: dex [REDACTED] failed",
        },
    }


def test_paper_auto_scan_records_tenant_audit_decision_event(monkeypatch):
    token, _api_key, org_id = _signup_login_key("paper-auto-audit")

    def fake_candidates(payload):
        assert payload.query == "AUDIT"
        return [
            {
                "token_address": "0xAUDIT",
                "chain": "base",
                "symbol": "AUDIT",
                "volume_24h": 750000,
                "momentum_h1": 14,
                "liquidity_usd": 175000,
                "price_usd": 3,
            }
        ]

    def fake_audit(token_address: str, chain: str = "evm") -> dict[str, object]:
        return {
            "token_address": token_address,
            "is_honeypot": False,
            "risk_score": 6,
            "buy_tax": 0,
            "sell_tax": 0,
            "providers": ["test"],
        }

    monkeypatch.setattr("tothemoon_api.routers.copilot._scan_market_candidates", fake_candidates)
    monkeypatch.setattr("tothemoon_api.routers.copilot.get_token_security_audit", fake_audit)

    response = client.post(
        "/api/v1/copilot/scan",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "AUDIT", "execution_mode": "paper_auto", "limit": 1, "amount_usd": 55},
    )

    assert response.status_code == 201
    created = response.json()["created"][0]
    scanner_run = response.json()["scanner_run"]

    with SessionLocal() as db:
        decision = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == org_id,
                AuditLog.action == "paper_execution.decision_recorded",
            )
            .one()
        )
        trade = db.query(SimulatedTrade).filter(SimulatedTrade.org_id == org_id).one()
        assert decision.actor_type == "user"
        assert decision.target_type == "copilot_proposal"
        assert decision.target_id == str(created["id"])
        assert decision.after == {
            "scanner_run_id": scanner_run["id"],
            "execution_mode": "paper_auto",
            "decision": "paper_executed",
            "reason": "mode_1_paper_auto",
            "token_address": "0xAUDIT",
            "chain": "base",
            "symbol": "AUDIT",
            "score": created["score"],
            "recommendation": "paper_auto",
            "rationale": created["rationale"],
            "proposal_id": created["id"],
            "proposal_status": "executed",
            "proposal_mode": "paper",
            "amount_usd": "55.000000",
            "trade_id": trade.id,
        }
