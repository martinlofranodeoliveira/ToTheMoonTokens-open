from __future__ import annotations

from tothemoon_api.observability import (
    REDACTED_PLACEHOLDER,
    redact_sensitive_fields,
    redact_sensitive_value,
)


def _run(event_dict: dict[str, object]) -> dict[str, object]:
    return redact_sensitive_fields(None, "info", event_dict)


def test_top_level_token_is_redacted():
    out = _run({"event": "artifact_request", "approval_token": "ttm_approval_abc"})
    assert out["approval_token"] == REDACTED_PLACEHOLDER
    assert out["event"] == "artifact_request"


def test_nested_secret_is_redacted():
    out = _run({"event": "x", "settings": {"api_secret": "abc", "exchange": "binance"}})
    assert out["settings"]["api_secret"] == REDACTED_PLACEHOLDER
    assert out["settings"]["exchange"] == "binance"


def test_acknowledgement_key_is_redacted():
    out = _run({"acknowledgement": "I_ACCEPT_TESTNET_ONLY"})
    assert out["acknowledgement"] == REDACTED_PLACEHOLDER


def test_authorization_header_like_field_is_redacted():
    out = _run({"Authorization": "Bearer abc.def.ghi"})
    assert out["Authorization"] == REDACTED_PLACEHOLDER


def test_regular_fields_pass_through():
    payload = {
        "event": "backtest_completed",
        "strategy_id": "ema_crossover",
        "net_profit": 123.45,
        "edge_status": "positive",
    }
    out = _run(dict(payload))
    assert out == payload


def test_list_under_sensitive_key_is_replaced_wholesale():
    out = _run({"seeds": ["a", "b", "c"]})
    assert out["seeds"] == REDACTED_PLACEHOLDER


def test_deeply_nested_sensitive_key_still_redacted():
    out = _run({"outer": {"inner": {"private_key": "deadbeef"}}})
    assert out["outer"]["inner"]["private_key"] == REDACTED_PLACEHOLDER


def test_sensitive_value_inside_non_sensitive_error_field_is_redacted():
    out = _run({"event": "provider_failed", "error": "401 Authorization=Bearer abc.def.ghi"})
    assert out["error"] == f"401 {REDACTED_PLACEHOLDER}"


def test_provider_secret_patterns_inside_string_values_are_redacted():
    assert redact_sensitive_value("provider rejected api_key=sk_live_deadbeef") == (
        f"provider rejected {REDACTED_PLACEHOLDER}"
    )
    assert redact_sensitive_value("provider rejected api_key=sk-live-deadbeef") == (
        f"provider rejected {REDACTED_PLACEHOLDER}"
    )
    assert redact_sensitive_value("provider rejected 401 sk-live-deadbeef") == (
        f"provider rejected 401 {REDACTED_PLACEHOLDER}"
    )
    assert redact_sensitive_value("stripe signature whsec_deadbeef failed") == (
        f"stripe signature {REDACTED_PLACEHOLDER} failed"
    )
