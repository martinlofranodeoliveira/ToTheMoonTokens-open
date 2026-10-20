"""Focused release guards: placeholders cannot act as JWT signing keys."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from tothemoon_api import auth


@pytest.mark.parametrize("secret", ["", "short", " " * 40, "x" * 31])
def test_unconfigured_signing_never_encodes_a_token(monkeypatch, secret):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(jwt_secret=secret))
    encode = Mock(side_effect=AssertionError("JWT encoder must not run"))
    monkeypatch.setattr(auth.jwt, "encode", encode)
    user = SimpleNamespace(id=42, email="synthetic-user@example.com")

    with pytest.raises(HTTPException) as caught:
        auth.create_jwt_for_user(user)

    assert caught.value.status_code == 503
    assert caught.value.detail == "Authentication is not configured"
    encode.assert_not_called()


@pytest.mark.parametrize("secret", ["", "short", " " * 40, "x" * 31])
def test_unconfigured_verification_never_decodes_a_token(monkeypatch, secret):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(jwt_secret=secret))
    decode = Mock(side_effect=AssertionError("JWT decoder must not run"))
    monkeypatch.setattr(auth.jwt, "decode", decode)
    database = Mock(side_effect=AssertionError("No database lookup is needed"))
    request = SimpleNamespace(url=SimpleNamespace(path="/api/v1/auth/me"))

    with pytest.raises(HTTPException) as caught:
        auth.current_user_from_jwt("synthetic-not-a-real-token", database, request)

    assert caught.value.status_code == 503
    assert caught.value.detail == "Authentication is not configured"
    decode.assert_not_called()
    database.assert_not_called()
