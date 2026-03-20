"""Tests for PTC session ownership verification."""
import hashlib
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.services.ptc.sandbox import (
    PTCSandboxExecutor,
    SandboxSession,
)


def _make_session(owner_key_hash: str = "") -> SandboxSession:
    """Create a minimal SandboxSession for testing."""
    now = datetime.now()
    return SandboxSession(
        session_id="test-session-1",
        container=MagicMock(),
        socket=MagicMock(),
        created_at=now,
        expires_at=now + timedelta(seconds=300),
        last_used_at=now,
        owner_key_hash=owner_key_hash,
    )


class TestSessionOwnership:
    """Tests for session ownership verification."""

    def test_same_key_hash_grants_access(self):
        """Session created with key hash A, accessed with key hash A → allowed."""
        key_hash = hashlib.sha256(b"api-key-123").hexdigest()
        session = _make_session(owner_key_hash=key_hash)

        assert PTCSandboxExecutor.verify_session_ownership(session, key_hash) is True

    def test_different_key_hash_denies_access(self):
        """Session created with key hash A, accessed with key hash B → denied."""
        key_a = hashlib.sha256(b"api-key-123").hexdigest()
        key_b = hashlib.sha256(b"api-key-456").hexdigest()
        session = _make_session(owner_key_hash=key_a)

        assert PTCSandboxExecutor.verify_session_ownership(session, key_b) is False

    def test_legacy_session_without_hash_allows_any_key(self):
        """Legacy session (no owner hash) allows access from any key."""
        session = _make_session(owner_key_hash="")
        key_hash = hashlib.sha256(b"any-key").hexdigest()

        assert PTCSandboxExecutor.verify_session_ownership(session, key_hash) is True

    def test_empty_key_hash_with_owned_session_denies(self):
        """Empty request key hash cannot access an owned session."""
        key_hash = hashlib.sha256(b"api-key-123").hexdigest()
        session = _make_session(owner_key_hash=key_hash)

        assert PTCSandboxExecutor.verify_session_ownership(session, "") is False

    def test_session_dataclass_stores_owner_hash(self):
        """SandboxSession stores owner_key_hash field."""
        key_hash = "abc123"
        session = _make_session(owner_key_hash=key_hash)

        assert session.owner_key_hash == key_hash

    def test_session_default_owner_hash_is_empty(self):
        """SandboxSession defaults to empty owner_key_hash."""
        session = _make_session()

        assert session.owner_key_hash == ""


class TestGetOrCreateSessionOwnership:
    """Tests for ownership verification in _get_or_create_session."""

    @pytest.mark.asyncio
    async def test_ownership_mismatch_raises_permission_error(self):
        """Accessing another user's session raises PermissionError."""
        key_a = hashlib.sha256(b"user-a-key").hexdigest()
        key_b = hashlib.sha256(b"user-b-key").hexdigest()

        session = _make_session(owner_key_hash=key_a)

        service = MagicMock()
        service.sandbox_executor = MagicMock()
        service.sandbox_executor.get_session.return_value = session
        service.sandbox_executor.verify_session_ownership = PTCSandboxExecutor.verify_session_ownership

        # Import the actual method to test
        from app.services.ptc_service import PTCService

        ptc = PTCService.__new__(PTCService)
        ptc._sandbox_executor = service.sandbox_executor

        with pytest.raises(PermissionError, match="permission"):
            await ptc._get_or_create_session(
                container_id="test-session-1",
                tools=[],
                owner_key_hash=key_b,
            )
