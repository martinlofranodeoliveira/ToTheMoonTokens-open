from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tothemoon_api.circle import CircleDeveloperClient


@pytest.fixture
def circle_client():
    return CircleDeveloperClient(  # pragma: allowlist secret
        api_key="TEST_API_KEY",  # pragma: allowlist secret
        entity_secret="TEST_ENTITY_VALUE",  # pragma: allowlist secret
    )


def test_create_wallet(circle_client):
    with (
        patch.object(circle_client, "entity_secret_ciphertext", return_value="ciphertext"),
        patch("httpx.post") as mock_post,
    ):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "wallets": [{"id": "wallet-123", "address": "0x123", "blockchain": "ARC-TESTNET"}]
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = circle_client.create_wallet(wallet_set_id="ws-123", idempotency_key="idemp-1")

        assert result["data"]["wallets"][0]["id"] == "wallet-123"
        mock_post.assert_called_once()
        _args, kwargs = mock_post.call_args
        assert "ARC-TESTNET" in kwargs["json"]["blockchains"]
        assert kwargs["json"]["entitySecretCiphertext"] == "ciphertext"


def test_fund_with_testnet_usdc(circle_client):
    with patch("httpx.post") as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"status": "success"}}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = circle_client.fund_with_testnet_usdc(address="0x123")

        assert result["data"]["status"] == "success"
        mock_post.assert_called_once()
        _args, kwargs = mock_post.call_args
        assert kwargs["json"]["address"] == "0x123"


def test_create_transfer_uses_wallet_address_and_token_address(circle_client):
    with (
        patch.object(circle_client, "entity_secret_ciphertext", return_value="ciphertext"),
        patch("httpx.post") as mock_post,
    ):
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"id": "tx-123", "state": "INITIATED"}}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = circle_client.create_transfer(
            wallet_address="0x456",
            destination_address="0x789",
            amount="0.001",
            token_address="0x0000000000000000000000000000000000000006",
            idempotency_key="idemp-tx-1",
        )

        assert result["data"]["id"] == "tx-123"
        mock_post.assert_called_once()
        _args, kwargs = mock_post.call_args
        assert kwargs["json"]["walletAddress"] == "0x456"
        assert kwargs["json"]["destinationAddress"] == "0x789"
        assert kwargs["json"]["tokenAddress"] == "0x0000000000000000000000000000000000000006"
        assert kwargs["json"]["feeLevel"] == "MEDIUM"
        assert kwargs["json"]["entitySecretCiphertext"] == "ciphertext"


def test_entity_secret_ciphertext_reuses_pre_encrypted_secret():
    client = CircleDeveloperClient(
        api_key="TEST_API_KEY",  # pragma: allowlist secret
        entity_secret="already-encrypted-ciphertext",  # pragma: allowlist secret
    )

    assert client.entity_secret_ciphertext() == "already-encrypted-ciphertext"


def test_entity_secret_ciphertext_encrypts_raw_hex_secret_with_fresh_ciphertext():
    client = CircleDeveloperClient(
        api_key="TEST_API_KEY",  # pragma: allowlist secret
        entity_secret="a" * 64,  # pragma: allowlist secret
    )

    with patch.object(client, "_entity_public_key", return_value=_test_public_key_pem()):
        ciphertext = client.entity_secret_ciphertext()
        second_ciphertext = client.entity_secret_ciphertext()

    assert ciphertext
    assert ciphertext != "a" * 64
    assert second_ciphertext
    assert second_ciphertext != "a" * 64
    assert second_ciphertext != ciphertext


def test_get_transaction(circle_client):
    with patch("httpx.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"transaction": {"id": "tx-123", "state": "COMPLETE", "txHash": "0xmock"}}
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = circle_client.get_transaction("tx-123")

        assert result["data"]["transaction"]["state"] == "COMPLETE"
        mock_get.assert_called_once()


def _test_public_key_pem() -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def test_wait_for_transaction_polls_until_terminal(circle_client):
    responses = [
        {"data": {"transaction": {"id": "tx-123", "state": "INITIATED"}}},
        {
            "data": {
                "transaction": {
                    "id": "tx-123",
                    "state": "COMPLETE",
                    "txHash": "0xmocktransferhash",
                }
            }
        },
    ]

    with (
        patch.object(circle_client, "get_transaction", side_effect=responses) as mock_get,
        patch("time.sleep") as mock_sleep,
    ):
        result = circle_client.wait_for_transaction("tx-123", timeout_s=5.0, poll_interval_s=0.01)

        assert result["state"] == "COMPLETE"
        assert result["txHash"] == "0xmocktransferhash"
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once()
