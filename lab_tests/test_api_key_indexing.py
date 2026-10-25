"""Bounded API-key lookup regressions using a real, isolated SQL database."""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch

from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api/src"))

from tothemoon_api import auth  # noqa: E402
from tothemoon_api.database import Base  # noqa: E402
from tothemoon_api.db_models import ApiKey, Membership, Organization, Plan, User, utcnow_naive  # noqa: E402


class CountingHasher:
    """Cheap full-token test double; production Argon2 configuration is untouched."""

    def __init__(self) -> None:
        self.verified: list[str] = []

    def hash(self, plaintext: str) -> str:
        return "test$" + hashlib.sha256(plaintext.encode()).hexdigest()

    def verify(self, stored: str, plaintext: str) -> bool:
        self.verified.append(stored)
        if not stored.startswith("test$") or len(stored) != 69:
            raise InvalidHashError("corrupt test hash")
        if stored != self.hash(plaintext):
            raise VerifyMismatchError("test token mismatch")
        return True


class ApiKeyIndexingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.hasher = CountingHasher()
        patcher = patch.object(auth, "ph", self.hasher)
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.rate_limiter.reset()
        self.addCleanup(auth.rate_limiter.reset)
        self.request = Request(
            {"type": "http", "method": "GET", "path": "/lab/api-key-indexing", "headers": []}
        )
        self.plan = Plan(
            code="lab",
            name="Lab",
            monthly_request_limit=1000,
            monthly_token_audit_limit=1000,
            active_api_key_limit=1000,
        )
        self.owner = User(email="indexing@example.com", password_hash="unused", is_active=True)
        self.db.add_all([self.plan, self.owner])
        self.db.flush()
        self.org = Organization(name="Indexing", plan_id=self.plan.id)
        self.db.add(self.org)
        self.db.flush()
        self.db.add(Membership(user_id=self.owner.id, org_id=self.org.id, role="owner"))
        self.db.commit()
        self.key_queries: list[tuple[str, object]] = []

        @event.listens_for(self.engine, "before_cursor_execute")
        def record_key_queries(_connection, _cursor, statement, parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT") and "FROM api_keys" in statement:
                self.key_queries.append((statement, parameters))

    @staticmethod
    def _raw(selector: str, suffix: str = "original") -> str:
        assert len(selector) == 4
        return "ttm_sk_live_" + selector + "_" + suffix

    def _key(self, raw: str, *, legacy: bool = False, stored: str | None = None) -> ApiKey:
        row = ApiKey(
            user_id=self.owner.id,
            org_id=self.org.id,
            name="lab",
            prefix=raw[:12] if legacy else raw[:16],
            key_hash=self.hasher.hash(raw) if stored is None else stored,
            scopes="tokens:audit",
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _verify(self, raw: str) -> ApiKey:
        return auth.verify_api_key(raw, self.db, self.request)

    def _assert_rejected(self, row: ApiKey, raw: str, *, verification_count: int = 1) -> None:
        original_hash = row.key_hash
        with self.assertRaises(HTTPException) as raised:
            self._verify(raw)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail, "Invalid API Key")
        self.db.refresh(row)
        self.assertEqual(row.prefix, raw[:12])
        self.assertEqual(row.key_hash, original_hash)
        self.assertIsNone(row.last_used_at)
        self.assertEqual(len(self.hasher.verified), verification_count)

    def test_generated_selector_contains_random_token_characters(self) -> None:
        suffixes = ["AAAA" + "a" * 39, "BBBB" + "b" * 39]
        with patch.object(auth.secrets, "token_urlsafe", side_effect=suffixes) as token_urlsafe:
            first = auth.generate_api_key()
            second = auth.generate_api_key()
        token_urlsafe.assert_has_calls([call(32), call(32)])
        self.assertEqual(token_urlsafe.call_count, 2)
        for generated, suffix in zip((first, second), suffixes, strict=True):
            raw, selector, stored = generated
            self.assertEqual(raw, "ttm_sk_live_" + suffix)
            self.assertEqual(selector, raw[:16])
            self.assertEqual(len(selector), 16)
            self.assertEqual(stored, self.hasher.hash(raw))
        self.assertNotEqual(first[1], second[1])

    def test_unique_modern_lookup_bypasses_unrelated_modern_and_legacy_keys(self) -> None:
        for number in range(8):
            self._key(self._raw(f"M{number:03d}"))
            self._key(self._raw(f"L{number:03d}"), legacy=True)
        raw = self._raw("GOOD")
        target = self._key(raw)
        self.key_queries.clear()
        result = self._verify(raw)
        self.assertEqual(result.id, target.id)
        self.assertEqual(self.hasher.verified, [target.key_hash])
        self.assertEqual(len(self.key_queries), 1)
        self.assertIn(raw[:16], self.key_queries[0][1])
        self.assertIsNotNone(result.last_used_at)

    def test_selector_collision_requires_full_hash_verification(self) -> None:
        wrong = self._key(self._raw("SAME", "wrong"))
        raw = self._raw("SAME", "right")
        right = self._key(raw)
        self.key_queries.clear()
        result = self._verify(raw)
        self.assertEqual(result.id, right.id)
        self.assertEqual(self.hasher.verified, [wrong.key_hash, right.key_hash])
        self.assertIsNone(wrong.last_used_at)
        self.assertEqual(len(self.key_queries), 1)

    def test_legacy_key_promotes_without_rehash_and_next_use_skips_legacy(self) -> None:
        unrelated = self._key(self._raw("OLD1"), legacy=True)
        raw = self._raw("OLD2")
        target = self._key(raw, legacy=True)
        original_hash = target.key_hash
        self.key_queries.clear()
        result = self._verify(raw)
        self.assertEqual(result.id, target.id)
        self.assertEqual(self.hasher.verified, [unrelated.key_hash, original_hash])
        self.assertEqual(len(self.key_queries), 2)
        self.db.refresh(target)
        self.db.refresh(unrelated)
        self.assertEqual(target.prefix, raw[:16])
        self.assertEqual(target.key_hash, original_hash)
        self.assertIsNotNone(target.last_used_at)
        self.assertEqual(unrelated.prefix, self._raw("OLD1")[:12])
        self.assertIsNone(unrelated.last_used_at)
        self.hasher.verified.clear()
        self.key_queries.clear()
        self.assertEqual(self._verify(raw).id, target.id)
        self.assertEqual(self.hasher.verified, [original_hash])
        self.assertEqual(len(self.key_queries), 1)

    def test_wrong_legacy_hash_is_rejected_without_promotion(self) -> None:
        raw = self._raw("FAIL")
        row = self._key(raw, legacy=True, stored=self.hasher.hash(raw + "wrong"))
        self._assert_rejected(row, raw)

    def test_corrupt_legacy_hash_is_rejected_without_promotion(self) -> None:
        raw = self._raw("BADH")
        row = self._key(raw, legacy=True, stored="corrupt")
        self._assert_rejected(row, raw)

    def test_revoked_legacy_key_is_rejected_without_verification_or_promotion(self) -> None:
        raw = self._raw("REVK")
        row = self._key(raw, legacy=True)
        row.revoked_at = utcnow_naive()
        self.db.commit()
        self._assert_rejected(row, raw, verification_count=0)

    def test_inactive_legacy_owner_is_rejected_without_promotion(self) -> None:
        raw = self._raw("IDLE")
        row = self._key(raw, legacy=True)
        self.owner.is_active = False
        self.db.commit()
        self._assert_rejected(row, raw)

    def test_missing_legacy_membership_is_rejected_without_promotion(self) -> None:
        raw = self._raw("NONE")
        row = self._key(raw, legacy=True)
        self.db.query(Membership).delete()
        self.db.commit()
        self._assert_rejected(row, raw)

    def test_membership_in_another_tenant_does_not_authorize_legacy_key(self) -> None:
        raw = self._raw("OTHR")
        row = self._key(raw, legacy=True)
        other_org = Organization(name="Other tenant", plan_id=self.plan.id)
        self.db.add(other_org)
        self.db.flush()
        membership = self.db.query(Membership).one()
        membership.org_id = other_org.id
        self.db.commit()
        self._assert_rejected(row, raw)

    def test_missing_legacy_tenant_is_rejected_without_promotion(self) -> None:
        raw = self._raw("NULL")
        row = self._key(raw, legacy=True)
        row.org_id = None
        self.db.commit()
        self._assert_rejected(row, raw)


if __name__ == "__main__":
    unittest.main()
