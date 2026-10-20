from __future__ import annotations

import httpx

from tothemoon_api.external.adapters import refresh_freshness_metadata
from tothemoon_api.external.cache import clear_external_cache
from tothemoon_api.external.health import get_provider_health
from tothemoon_api.external.market import get_token_market_data
from tothemoon_api.external.security import get_token_security_audit
from tothemoon_api.observability import REDACTED_PLACEHOLDER


def test_get_token_security_audit():
    clear_external_cache()
    audit = get_token_security_audit("0xSAFE")
    assert audit["is_honeypot"] is False
    assert audit["liquidity_locked_pct"] > 0
    assert audit["provider"] == "local_fallback"
    assert audit["degraded"] is True
    assert audit["confidence"] == "low"
    assert audit["freshness"]["status"] == "placeholder"

    audit_scam = get_token_security_audit("0xSCAM")
    assert audit_scam["is_honeypot"] is True


def test_get_token_market_data():
    clear_external_cache()
    market = get_token_market_data("0xABC")
    assert "price" in market
    assert "volatility_index" in market
    assert market["provider"] == "local_fallback"
    assert market["degraded"] is True
    assert market["confidence"] == "low"
    assert market["freshness"]["status"] == "placeholder"


def test_security_consensus_true_when_2_of_3_say_honeypot(monkeypatch):
    clear_external_cache()

    monkeypatch.setattr(
        "tothemoon_api.external.security._fetch_goplus",
        lambda token, chain: {"provider": "goplus", "is_honeypot": True, "risk_score": 80},
    )
    monkeypatch.setattr(
        "tothemoon_api.external.security._fetch_honeypotis",
        lambda token, chain: {"provider": "honeypotis", "is_honeypot": True, "risk_score": 70},
    )
    monkeypatch.setattr(
        "tothemoon_api.external.security._fetch_tokensniffer",
        lambda token, chain: {"provider": "tokensniffer", "is_honeypot": False, "risk_score": 20},
    )

    audit = get_token_security_audit("0x000000000000000000000000000000000000001c")

    assert audit["is_honeypot"] is True
    assert audit["providers"] == ["goplus", "honeypotis", "tokensniffer"]
    assert audit["provider"] == "security_consensus"
    assert audit["confidence"] == "high"
    assert audit["freshness"]["status"] == "live"


def test_security_uses_max_risk_score(monkeypatch):
    clear_external_cache()

    monkeypatch.setattr(
        "tothemoon_api.external.security._fetch_goplus",
        lambda token, chain: {
            "provider": "goplus",
            "is_honeypot": False,
            "risk_score": 25,
            "buy_tax": 1,
            "sell_tax": 2,
        },
    )
    monkeypatch.setattr(
        "tothemoon_api.external.security._fetch_honeypotis",
        lambda token, chain: {
            "provider": "honeypotis",
            "is_honeypot": False,
            "risk_score": 92,
            "buy_tax": 3,
            "sell_tax": 4,
        },
    )
    monkeypatch.setattr(
        "tothemoon_api.external.security._fetch_tokensniffer",
        lambda token, chain: {"provider": "tokensniffer", "is_honeypot": False, "risk_score": 55},
    )

    audit = get_token_security_audit("0x000000000000000000000000000000000000001d")

    assert audit["risk_score"] == 92
    assert audit["buy_tax"] == 3
    assert audit["sell_tax"] == 4


def test_market_caches_for_30_seconds(monkeypatch):
    clear_external_cache()
    calls = {"count": 0}

    def fake_fetch(token_address: str) -> dict[str, object]:
        calls["count"] += 1
        return {
            "token_address": token_address,
            "price": 2.0,
            "chain": "base",
            "volatility_index": 0.1,
            "provider": "dexscreener",
        }

    monkeypatch.setattr("tothemoon_api.external.market._fetch_dexscreener", fake_fetch)

    first = get_token_market_data("0x000000000000000000000000000000000000001e")
    second = get_token_market_data("0x000000000000000000000000000000000000001e")

    assert first == second
    assert calls["count"] == 1


def test_market_falls_back_when_dexscreener_500s(monkeypatch):
    clear_external_cache()

    def fail_dex(token_address: str) -> dict[str, object]:
        request = httpx.Request("GET", "https://api.dexscreener.com/latest/dex/tokens/x")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr("tothemoon_api.external.market._fetch_dexscreener", fail_dex)
    monkeypatch.setattr(
        "tothemoon_api.external.market._fetch_birdeye",
        lambda token_address: {
            "token_address": token_address,
            "price": 3.0,
            "chain": "solana",
            "volatility_index": 0.03,
            "provider": "birdeye",
        },
    )

    market = get_token_market_data("So11111111111111111111111111111111111111112")

    assert market["provider"] == "birdeye"
    assert market["price"] == 3.0
    assert market["confidence"] == "high"
    assert market["freshness"]["status"] == "live"


def test_market_missing_birdeye_key_is_skipped_not_degraded(monkeypatch):
    clear_external_cache()

    def fail_dex(token_address: str) -> dict[str, object]:
        raise RuntimeError("dexscreener unavailable")

    monkeypatch.setattr("tothemoon_api.external.market._fetch_dexscreener", fail_dex)
    monkeypatch.setattr("tothemoon_api.config.Settings.birdeye_api_key", "", raising=False)

    market = get_token_market_data("So11111111111111111111111111111111111111112")

    assert market["provider"] == "local_fallback"
    health = get_provider_health()
    assert health["birdeye"]["status"] == "skipped"
    assert health["birdeye"]["last_error"] == "missing API key"


def test_security_fails_closed_when_all_real_providers_fail(monkeypatch):
    clear_external_cache()

    def fail_provider(token_address: str, chain: str) -> dict[str, object]:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("tothemoon_api.external.security._fetch_goplus", fail_provider)
    monkeypatch.setattr("tothemoon_api.external.security._fetch_honeypotis", fail_provider)
    monkeypatch.setattr("tothemoon_api.external.security._fetch_tokensniffer", fail_provider)

    audit = get_token_security_audit("0x000000000000000000000000000000000000001f")

    assert audit["is_honeypot"] is True
    assert audit["risk_score"] == 99
    assert audit["contract_verified"] is False
    assert audit["providers"] == ["provider_unavailable"]
    assert audit["provider_health"] == "degraded"
    assert audit["provider"] == "provider_unavailable"
    assert audit["confidence"] == "none"
    assert audit["degraded"] is True
    health = get_provider_health()
    assert health["goplus"]["status"] == "degraded"
    assert health["honeypotis"]["status"] == "degraded"
    assert health["tokensniffer"]["status"] == "degraded"


def test_security_missing_key_path_marks_unavailable_not_high_confidence(monkeypatch):
    clear_external_cache()

    def timeout_provider(token_address: str, chain: str) -> dict[str, object]:
        raise RuntimeError("timeout")

    monkeypatch.setattr("tothemoon_api.external.security._fetch_goplus", timeout_provider)
    monkeypatch.setattr("tothemoon_api.external.security._fetch_honeypotis", timeout_provider)
    monkeypatch.setattr("tothemoon_api.config.Settings.tokensniffer_api_key", "", raising=False)

    audit = get_token_security_audit("0x0000000000000000000000000000000000000020")

    assert audit["provider"] == "provider_unavailable"
    assert audit["confidence"] == "none"
    assert audit["degraded"] is True


def test_freshness_metadata_marks_expired_cache_payload_stale():
    payload = {
        "provider": "dexscreener",
        "observed_at": "2020-01-01T00:00:00Z",
        "freshness": {"status": "live", "ttl_seconds": 30, "age_seconds": 0, "stale": False},
        "confidence": "high",
        "degraded": False,
    }

    refreshed = refresh_freshness_metadata(payload)

    assert refreshed["freshness"]["age_seconds"] > 30
    assert refreshed["freshness"]["stale"] is True
    assert refreshed["degraded"] is True


def test_external_adapter_contract_is_paper_only_read_through():
    from tothemoon_api.external import get_external_adapter_contract
    from tothemoon_api.external.adapters import is_paper_only_adapter_contract

    contract = get_external_adapter_contract()

    assert contract["mode"] == "paper_only_read_through"
    assert contract["order_submission_enabled"] is False
    assert contract["mainnet_order_submission_enabled"] is False
    assert {provider["name"] for provider in contract["providers"]} == {
        "goplus",
        "honeypotis",
        "tokensniffer",
        "dexscreener",
        "birdeye",
    }
    assert all(provider["read_only"] is True for provider in contract["providers"])
    assert all(provider["supports_order_submission"] is False for provider in contract["providers"])
    assert all(provider["mainnet_order_submission"] is False for provider in contract["providers"])
    assert all(provider["public_exposure"] is False for provider in contract["providers"])
    assert all(isinstance(provider["requires_api_key"], bool) for provider in contract["providers"])
    assert all(isinstance(provider["api_key_configured"], bool) for provider in contract["providers"])
    assert all("secret" not in str(provider).lower() for provider in contract["providers"])
    assert is_paper_only_adapter_contract(contract) is True


def test_operator_readiness_evidence_redacts_provider_errors_and_blocks_unsafe_contract():
    from tothemoon_api.external.health import (
        build_operator_readiness_evidence,
        build_provider_readiness,
    )

    provider_readiness = build_provider_readiness(
        contract={
            "mode": "paper_only_read_through",
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": [
                {
                    "name": "unsafe_exchange",
                    "capabilities": ["market_data"],
                    "requires_api_key": True,
                    "api_key_configured": True,
                    "read_only": False,
                    "supports_order_submission": True,
                    "mainnet_order_submission": True,
                    "public_exposure": True,
                }
            ],
        },
        health={
            "unsafe_exchange": {
                "status": "degraded",
                "latency_ms": 1.0,
                "observed_at": "2026-05-08T00:00:00Z",
                "last_error": "provider rejected api_key=raw-provider-secret",
            }
        },
    )

    evidence = build_operator_readiness_evidence(
        provider_readiness=provider_readiness,
        runtime_mode="paper",
    )

    assert evidence["status"] == "blocked"
    assert evidence["safety"] == {
        "paper_only": False,
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "autonomous_signing_enabled": False,
        "live_funds_enabled": False,
    }
    assert evidence["modes"][0]["status"] == "blocked"
    assert "external providers are not paper-only safe" in evidence["modes"][0]["blocking_reasons"]
    assert evidence["modes"][2]["status"] == "locked"
    assert "raw-provider-secret" not in str(evidence)
    assert "[REDACTED]" in str(evidence)


def test_adapter_rejects_order_capable_provider():
    from tothemoon_api.external.adapters import ExternalProviderAdapter, UnsafeProviderAdapterError

    adapter = ExternalProviderAdapter(
        name="unsafe_exchange",
        capabilities=frozenset({"market_data"}),
        fetch=lambda token: {"token_address": token},
        supports_order_submission=True,
    )

    try:
        adapter.assert_safe_for_paper_mode()
    except UnsafeProviderAdapterError:
        return
    raise AssertionError("order-capable providers must be rejected")


def test_adapter_rejects_public_exposure_provider():
    from tothemoon_api.external.adapters import ExternalProviderAdapter, UnsafeProviderAdapterError

    adapter = ExternalProviderAdapter(
        name="public_feed",
        capabilities=frozenset({"market_data"}),
        fetch=lambda token: {"provider": "public_feed", "token_address": token},
        public_exposure=True,
    )

    try:
        adapter.assert_safe_for_paper_mode()
    except UnsafeProviderAdapterError:
        return
    raise AssertionError("publicly exposed providers must be rejected")


def test_provider_call_rejects_mismatched_provider_identity():
    from tothemoon_api.external.adapters import ExternalProviderAdapter, call_provider

    adapter = ExternalProviderAdapter(
        name="dexscreener",
        capabilities=frozenset({"market_data"}),
        fetch=lambda token: {"provider": "evil_exchange", "token_address": token},
    )

    try:
        call_provider(adapter, "0xabc")
    except ValueError as exc:
        assert "mismatched provider identity" in str(exc)
    else:
        raise AssertionError("provider identity mismatches must fail closed")

    health = get_provider_health()
    assert health["dexscreener"]["status"] == "degraded"
    assert health["dexscreener"]["last_error"] == "provider identity mismatch"


def test_contract_validator_rejects_public_or_order_enabled_contracts():
    from tothemoon_api.external.adapters import is_paper_only_adapter_contract

    provider = {
        "name": "dexscreener",
        "capabilities": ["market_data"],
        "requires_api_key": False,
        "api_key_configured": True,
        "read_only": True,
        "supports_order_submission": False,
        "mainnet_order_submission": False,
        "public_exposure": False,
    }
    contract = {
        "mode": "paper_only_read_through",
        "order_submission_enabled": False,
        "mainnet_order_submission_enabled": False,
        "providers": [provider],
    }

    assert is_paper_only_adapter_contract(contract) is True
    assert (
        is_paper_only_adapter_contract(
            {**contract, "providers": [{**provider, "public_exposure": True}]}
        )
        is False
    )
    assert is_paper_only_adapter_contract({**contract, "order_submission_enabled": True}) is False


def test_adapter_rejects_missing_capabilities():
    from tothemoon_api.external.adapters import ExternalProviderAdapter, UnsafeProviderAdapterError

    adapter = ExternalProviderAdapter(
        name="empty_capabilities",
        capabilities=frozenset(),
        fetch=lambda token: {"token_address": token},
    )

    try:
        adapter.assert_safe_for_paper_mode()
    except UnsafeProviderAdapterError:
        return
    raise AssertionError("providers must declare at least one known capability")


def test_adapter_rejects_unknown_capabilities():
    from tothemoon_api.external.adapters import ExternalProviderAdapter, UnsafeProviderAdapterError

    adapter = ExternalProviderAdapter(
        name="unknown_capability",
        capabilities=frozenset({"wallet_write"}),
        fetch=lambda token: {"token_address": token},
    )

    try:
        adapter.assert_safe_for_paper_mode()
    except UnsafeProviderAdapterError:
        return
    raise AssertionError("providers must only declare approved read-through capabilities")


def test_adapter_rejects_blank_provider_name():
    from tothemoon_api.external.adapters import ExternalProviderAdapter, UnsafeProviderAdapterError

    adapter = ExternalProviderAdapter(
        name=" ",
        capabilities=frozenset({"market_data"}),
        fetch=lambda token: {"token_address": token},
    )

    try:
        adapter.assert_safe_for_paper_mode()
    except UnsafeProviderAdapterError:
        return
    raise AssertionError("providers must declare a non-empty name")


def test_provider_health_redacts_secret_values_from_last_error():
    from tothemoon_api.external.health import record_provider_status

    record_provider_status(
        "dexscreener",
        status="degraded",
        last_error="upstream rejected Authorization=Bearer abc.def.ghi api_key=sk_live_deadbeef",
    )

    health = get_provider_health()
    assert health["dexscreener"]["last_error"] == (
        f"upstream rejected {REDACTED_PLACEHOLDER} {REDACTED_PLACEHOLDER}"
    )
    assert "abc.def.ghi" not in health["dexscreener"]["last_error"]
    assert "sk_live_deadbeef" not in health["dexscreener"]["last_error"]


def test_provider_health_rejects_unknown_provider_name():
    from tothemoon_api.external.health import record_provider_status

    try:
        record_provider_status("unsafe_exchange", status="ok")
    except ValueError as exc:
        assert "Unknown external provider" in str(exc)
        return
    raise AssertionError("provider health must not accept unregistered integrations")


def test_provider_health_rejects_unknown_status():
    from tothemoon_api.external.health import record_provider_status

    try:
        record_provider_status("dexscreener", status="live_trading")
    except ValueError as exc:
        assert "Invalid external provider" in str(exc)
        return
    raise AssertionError("provider health must only expose known safe statuses")


def test_external_cache_disables_redis_after_read_failure(monkeypatch):
    import tothemoon_api.external.cache as cache

    class FailingRedisClient:
        get_calls = 0

        def get(self, key: str) -> bytes | None:
            self.get_calls += 1
            raise RuntimeError("redis unavailable")

        def setex(self, key: str, ttl: int, payload: str) -> None:
            raise AssertionError("redis writes should be skipped after read failure")

    client = FailingRedisClient()
    calls = {"count": 0}

    monkeypatch.setenv("REDIS_URL", "redis://cache.example:6379/0")
    monkeypatch.setattr(cache, "redis", object())
    monkeypatch.setattr(cache, "_REDIS_CLIENT", client)
    monkeypatch.setattr(cache, "_REDIS_DISABLED", False)
    clear_external_cache()
    monkeypatch.setattr(cache, "_REDIS_CLIENT", client)

    @cache.cached(ttl=60, namespace="redis-fallback-test")
    def fetch_value(token_address: str) -> dict[str, object]:
        calls["count"] += 1
        return {"token_address": token_address, "value": calls["count"]}

    first = fetch_value("0xredis")
    second = fetch_value("0xredis")

    assert first == second == {"token_address": "0xredis", "value": 1}
    assert client.get_calls == 1
    assert cache._REDIS_CLIENT is None
    assert cache._REDIS_DISABLED is True
    assert calls["count"] == 1


def test_provider_readiness_aggregate_reports_healthy_state():
    from datetime import UTC, datetime

    from tothemoon_api.external.health import build_provider_readiness

    now = datetime(2026, 5, 5, 2, 0, tzinfo=UTC)
    readiness = build_provider_readiness(
        health={
            "dexscreener": {
                "status": "ok",
                "latency_ms": 12.5,
                "last_error": None,
                "observed_at": "2026-05-05T01:59:30Z",
            },
            "birdeye": {
                "status": "ok",
                "latency_ms": 33.1,
                "last_error": None,
                "observed_at": "2026-05-05T01:59:50Z",
            },
        },
        contract={
            "providers": [
                {
                    "name": "dexscreener",
                    "capabilities": ["market_data"],
                    "requires_api_key": False,
                    "read_only": True,
                },
                {
                    "name": "birdeye",
                    "capabilities": ["market_data"],
                    "requires_api_key": True,
                    "read_only": True,
                },
            ]
        },
        now=now,
        stale_after_seconds=300,
    )

    assert readiness["status"] == "healthy"
    assert readiness["capabilities"]["market_data"]["status"] == "healthy"
    assert {provider["status"] for provider in readiness["providers"]} == {"ready"}
    assert readiness["order_submission_enabled"] is False
    assert readiness["mainnet_order_submission_enabled"] is False


def test_provider_readiness_aggregate_reports_skipped_missing_key_and_degraded():
    from datetime import UTC, datetime

    from tothemoon_api.external.health import build_provider_readiness

    readiness = build_provider_readiness(
        health={
            "tokensniffer": {
                "status": "skipped",
                "latency_ms": None,
                "last_error": "missing API key",
                "observed_at": "2026-05-05T02:00:00Z",
            },
            "goplus": {
                "status": "degraded",
                "latency_ms": 120.0,
                "last_error": "timeout with api_key=super-secret-token",
                "observed_at": "2026-05-05T02:00:00Z",
            },
        },
        contract={
            "providers": [
                {
                    "name": "tokensniffer",
                    "capabilities": ["security_audit"],
                    "requires_api_key": True,
                    "api_key_configured": False,
                    "read_only": True,
                },
                {
                    "name": "goplus",
                    "capabilities": ["security_audit"],
                    "requires_api_key": False,
                    "read_only": True,
                },
            ]
        },
        now=datetime(2026, 5, 5, 2, 1, tzinfo=UTC),
    )

    assert readiness["status"] == "not_ready"
    assert readiness["capabilities"]["security_audit"]["status"] == "not_ready"
    providers = {provider["name"]: provider for provider in readiness["providers"]}
    assert providers["tokensniffer"]["status"] == "skipped"
    assert providers["tokensniffer"]["requires_api_key"] is True
    assert providers["tokensniffer"]["api_key_configured"] is False
    assert providers["tokensniffer"]["last_error"] == "missing API key"
    assert providers["goplus"]["status"] == "degraded"
    assert providers["goplus"]["last_error"] == "timeout with [REDACTED]"
    assert "super-secret-token" not in str(readiness)


def test_external_adapter_contract_reports_required_key_presence(monkeypatch):
    from tothemoon_api.external import get_external_adapter_contract

    monkeypatch.setenv("TOKENSNIFFER_API_KEY", "token-sniffer-secret")
    monkeypatch.setenv("BIRDEYE_API_KEY", "")

    contract = get_external_adapter_contract()
    providers = {provider["name"]: provider for provider in contract["providers"]}

    assert providers["tokensniffer"]["requires_api_key"] is True
    assert providers["tokensniffer"]["api_key_configured"] is True
    assert providers["birdeye"]["requires_api_key"] is True
    assert providers["birdeye"]["api_key_configured"] is False
    assert "token-sniffer-secret" not in str(contract)


def test_provider_readiness_aggregate_reports_stale_and_unknown_states():
    from datetime import UTC, datetime

    from tothemoon_api.external.health import build_provider_readiness

    readiness = build_provider_readiness(
        health={
            "dexscreener": {
                "status": "ok",
                "latency_ms": 10.0,
                "last_error": None,
                "observed_at": "2026-05-05T01:00:00Z",
            },
        },
        contract={
            "providers": [
                {
                    "name": "dexscreener",
                    "capabilities": ["market_data"],
                    "requires_api_key": False,
                    "read_only": True,
                },
                {
                    "name": "birdeye",
                    "capabilities": ["market_data"],
                    "requires_api_key": True,
                    "read_only": True,
                },
            ]
        },
        now=datetime(2026, 5, 5, 2, 0, tzinfo=UTC),
        stale_after_seconds=300,
    )

    assert readiness["status"] == "not_ready"
    providers = {provider["name"]: provider for provider in readiness["providers"]}
    assert providers["dexscreener"]["status"] == "stale"
    assert providers["birdeye"]["status"] == "unknown"


def test_provider_readiness_surfaces_unsafe_contract_flags():
    from datetime import UTC, datetime

    from tothemoon_api.external.health import build_provider_readiness

    readiness = build_provider_readiness(
        health={
            "dexscreener": {
                "status": "ok",
                "observed_at": "2026-05-05T02:00:00Z",
                "latency_ms": 11,
                "last_error": None,
            }
        },
        contract={
            "order_submission_enabled": True,
            "mainnet_order_submission_enabled": True,
            "providers": [
                {
                    "name": "dexscreener",
                    "capabilities": ["market_data"],
                    "requires_api_key": False,
                    "read_only": False,
                    "supports_order_submission": True,
                    "mainnet_order_submission": True,
                    "public_exposure": True,
                }
            ],
        },
        now=datetime(2026, 5, 5, 2, 1, tzinfo=UTC),
    )

    provider = readiness["providers"][0]
    assert readiness["status"] == "unsafe"
    assert readiness["capabilities"]["market_data"]["status"] == "unsafe"
    assert readiness["paper_only"] is False
    assert readiness["order_submission_enabled"] is True
    assert readiness["mainnet_order_submission_enabled"] is True
    assert provider["status"] == "unsafe"
    assert provider["paper_only_safe"] is False
    assert provider["safety_issue"] == "unsafe provider contract"
    assert provider["read_only"] is False
    assert provider["supports_order_submission"] is True
    assert provider["mainnet_order_submission"] is True
    assert provider["public_exposure"] is True


def test_provider_readiness_rejects_unsafe_credential_state():
    from datetime import UTC, datetime

    from tothemoon_api.external.health import build_provider_readiness

    readiness = build_provider_readiness(
        health={
            "tokensniffer": {
                "status": "ok",
                "observed_at": "2026-05-05T02:00:00Z",
                "latency_ms": 11,
                "last_error": None,
            }
        },
        contract={
            "mode": "paper_only_read_through",
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "providers": [
                {
                    "name": "tokensniffer",
                    "capabilities": ["security_audit"],
                    "requires_api_key": True,
                    "api_key_configured": "true",
                    "read_only": True,
                    "supports_order_submission": False,
                    "mainnet_order_submission": False,
                    "public_exposure": False,
                }
            ],
        },
        now=datetime(2026, 5, 5, 2, 1, tzinfo=UTC),
    )

    provider = readiness["providers"][0]
    assert readiness["status"] == "unsafe"
    assert readiness["paper_only"] is False
    assert readiness["capabilities"]["security_audit"]["status"] == "unsafe"
    assert provider["status"] == "unsafe"
    assert provider["paper_only_safe"] is False
    assert provider["safety_issue"] == "unsafe provider credential contract"


def test_optional_skipped_providers_do_not_block_capability_readiness() -> None:
    from datetime import UTC, datetime

    from tothemoon_api.external.health import build_provider_readiness

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
    assert providers["birdeye"]["safety_issue"] is None
    assert providers["birdeye"]["last_error"] == "missing API key"
    assert providers["tokensniffer"]["status"] == "skipped"


def test_skipped_only_capability_remains_not_ready() -> None:
    from datetime import UTC, datetime

    from tothemoon_api.external.health import build_provider_readiness

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


def test_provider_health_summary_tracks_skipped_without_degraded(monkeypatch) -> None:
    from tothemoon_api.external.health import summarize_provider_health

    monkeypatch.setattr(
        "tothemoon_api.external.health.get_provider_health",
        lambda: {
            "goplus": {
                "status": "ok",
                "latency_ms": 10,
                "observed_at": "2026-05-07T00:00:00Z",
            },
            "tokensniffer": {
                "status": "skipped",
                "latency_ms": None,
                "observed_at": "2026-05-07T00:00:00Z",
            },
        },
    )

    summary = summarize_provider_health("goplus", "tokensniffer")

    assert summary["total"] == 2
    assert summary["unknown"] == 0
    assert summary["skipped"] == 1
    assert summary["skipped_providers"] == ["tokensniffer"]
    assert summary["degraded_providers"] == []
    assert summary["provider_degraded"] is False
