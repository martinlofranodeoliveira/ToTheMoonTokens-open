from __future__ import annotations

import base64
import logging
import re
import time
import uuid
from typing import Any, ClassVar

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from .config import get_settings

logger = logging.getLogger(__name__)


class CircleDeveloperClient:
    """Client for Circle developer-controlled wallets on Arc testnet."""

    ARC_TESTNET = "ARC-TESTNET"
    TERMINAL_STATES: ClassVar[set[str]] = {"COMPLETE", "FAILED", "CANCELLED", "DENIED"}

    def __init__(self, api_key: str | None = None, entity_secret: str | None = None):
        self._api_key_override = api_key
        self._entity_secret_override = entity_secret
        self.wallets_by_role: dict[str, dict[str, Any]] = {}
        self.wallets_loaded = False
        self._public_key_pem: str | None = None
        self.roles = [
            "RESEARCH_00",
            "RESEARCH_01",
            "RESEARCH_02",
            "RESEARCH_03",
            "CONSUMER_01",
            "CONSUMER_02",
            "AUDITOR",
            "TREASURY",
        ]

    @property
    def api_key(self) -> str:
        if self._api_key_override is not None:
            return self._api_key_override
        return get_settings().circle_api_key

    @property
    def entity_secret(self) -> str:
        if self._entity_secret_override is not None:
            return self._entity_secret_override
        return get_settings().circle_entity_secret

    @property
    def wallet_set_id(self) -> str:
        return get_settings().circle_wallet_set_id

    @property
    def base_url(self) -> str:
        return "https://api.circle.com/v1/w3s"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _request_id(self) -> str:
        return str(uuid.uuid4())

    def _entity_public_key(self) -> str:
        if self._public_key_pem:
            return self._public_key_pem
        response = httpx.get(
            f"{self.base_url}/config/entity/publicKey",
            headers=self.headers,
            timeout=5.0,
        )
        response.raise_for_status()
        payload = response.json()
        public_key = payload.get("data", {}).get("publicKey", "")
        if not public_key:
            raise RuntimeError("Circle did not return an entity public key.")
        self._public_key_pem = str(public_key)
        return self._public_key_pem

    def entity_secret_ciphertext(self) -> str:
        secret = self.entity_secret.strip()
        if not secret:
            raise RuntimeError("Missing Circle entity secret.")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", secret):
            return secret
        public_key = serialization.load_pem_public_key(self._entity_public_key().encode("utf-8"))
        if not isinstance(public_key, RSAPublicKey):
            raise RuntimeError("Circle entity public key must be RSA.")
        ciphertext = public_key.encrypt(
            bytes.fromhex(secret),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        return base64.b64encode(ciphertext).decode("ascii")

    def load_wallets(self) -> None:
        """Load wallets from the configured wallet set and map them to demo roles."""
        if not self.api_key or not self.wallet_set_id:
            logger.warning("Circle credentials missing. Skipping wallet load.")
            return

        try:
            response = httpx.get(
                f"{self.base_url}/wallets?walletSetId={self.wallet_set_id}",
                headers=self.headers,
                timeout=5.0,
            )
            response.raise_for_status()
            payload = response.json()
            wallets = payload.get("data", {}).get("wallets", [])
            wallets.sort(key=lambda wallet: wallet.get("id", ""))

            self.wallets_by_role = {}
            for index, role in enumerate(self.roles):
                if index < len(wallets):
                    self.wallets_by_role[role] = wallets[index]

            self.wallets_loaded = True
            logger.info(
                "Circle client ready, wallet set=%s, %s wallets loaded",
                self.wallet_set_id,
                len(self.wallets_by_role),
            )
        except Exception as exc:  # pragma: no cover - defensive logging path
            logger.error("Failed to load Circle wallets: %s", exc)
            self.wallets_loaded = False

    def ensure_wallets_loaded(self) -> None:
        if not self.wallets_loaded and self.wallet_set_id:
            self.load_wallets()

    def get_wallet_address(self, role: str) -> str | None:
        wallet = self.wallets_by_role.get(role.strip().upper())
        if wallet:
            return wallet.get("address")
        return None

    def get_wallet_role_for_address(self, address: str) -> str | None:
        normalized = address.strip().lower()
        if not normalized:
            return None
        for role, wallet in self.wallets_by_role.items():
            wallet_address = str(wallet.get("address", "")).strip().lower()
            if wallet_address == normalized:
                return role
        return None

    def create_wallet(
        self, wallet_set_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Create a developer-controlled wallet on Arc testnet."""
        payload = {
            "idempotencyKey": idempotency_key or self._request_id(),
            "walletSetId": wallet_set_id,
            "blockchains": [self.ARC_TESTNET],
            "count": 1,
            "accountType": "SCA",
            "entitySecretCiphertext": self.entity_secret_ciphertext(),
        }
        response = httpx.post(
            f"{self.base_url}/developer/wallets",
            headers=self.headers,
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()

    def fund_with_testnet_usdc(self, address: str) -> dict[str, Any]:
        """Request testnet USDC from Circle faucet for the given address."""
        payload = {"address": address, "blockchain": self.ARC_TESTNET, "usdAmount": "10.0"}
        response = httpx.post(
            "https://api.circle.com/v1/faucet/drips",
            headers=self.headers,
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()

    def create_transfer(
        self,
        *,
        wallet_address: str,
        destination_address: str,
        amount: str,
        token_address: str,
        blockchain: str = ARC_TESTNET,
        fee_level: str = "MEDIUM",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Submit a developer-controlled transfer using the current Circle API shape."""
        payload = {
            "idempotencyKey": idempotency_key or self._request_id(),
            "entitySecretCiphertext": self.entity_secret_ciphertext(),
            "blockchain": blockchain,
            "walletAddress": wallet_address,
            "destinationAddress": destination_address,
            "amounts": [amount],
            "tokenAddress": token_address,
            "feeLevel": fee_level,
        }
        response = httpx.post(
            f"{self.base_url}/developer/transactions/transfer",
            headers=self.headers,
            json=payload,
            timeout=20.0,
        )
        response.raise_for_status()
        return response.json()

    def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        response = httpx.get(
            f"{self.base_url}/transactions/{transaction_id}",
            headers=self.headers,
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()

    def wait_for_transaction(
        self,
        transaction_id: str,
        *,
        timeout_s: float = 90.0,
        poll_interval_s: float = 2.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            payload = self.get_transaction(transaction_id)
            transaction = payload.get("data", {}).get("transaction")
            if isinstance(transaction, dict):
                state = str(transaction.get("state", "")).upper()
                if state in self.TERMINAL_STATES:
                    return transaction
            time.sleep(poll_interval_s)
        raise TimeoutError(f"Timed out waiting for Circle transaction {transaction_id}.")


circle_client = CircleDeveloperClient()
