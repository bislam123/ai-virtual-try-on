"""Tests for backend/scripts/promote_admin.py -- the only in-repo mechanism
that grants users.is_admin (see that script's own module docstring and
docs/ARCHITECTURE.md's Admin/Operations section).

Same real-database convention as test_admin_api.py: promotion relies on a
real persisted users.id, not worth faking with a mock session. Auto-skips
if Postgres isn't reachable.

Run with: ai\\.venv\\Scripts\\python.exe -m pytest tests/test_promote_admin.py
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.auth.security import hash_password
from backend.app.db import AdminAuditLog, User, engine, get_session
from backend.scripts.promote_admin import promote_to_admin


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="Postgres isn't reachable at settings.database_url — see docs/DEVELOPMENT.md",
)


def _unique_email() -> str:
    return f"promote-{uuid.uuid4().hex}@example.com"


@pytest.fixture
def existing_account():
    email = _unique_email()
    with get_session() as session:
        user = User(email=email, password_hash=hash_password("irrelevant-password"))
        session.add(user)
        session.flush()
        user_id = user.id
    yield email, user_id
    with get_session() as session:
        user = session.get(User, user_id)
        if user is not None:
            session.delete(user)


def test_promote_to_admin_sets_is_admin_true(existing_account):
    email, user_id = existing_account
    result = promote_to_admin(email)
    assert result == 0
    with get_session() as session:
        user = session.get(User, user_id)
        assert user.is_admin is True


def test_promote_to_admin_writes_an_audit_entry_with_no_actor(existing_account):
    email, user_id = existing_account
    promote_to_admin(email)

    with get_session() as session:
        entries = (
            session.query(AdminAuditLog)
            .filter(AdminAuditLog.target_type == "user", AdminAuditLog.target_id == str(user_id))
            .all()
        )
        assert len(entries) == 1
        entry = entries[0]
        assert entry.action == "admin_promoted"
        # No HTTP-authenticated admin session exists for this out-of-band
        # bootstrap action -- see AdminAuditLog's own docstring.
        assert entry.admin_user_id is None
        assert entry.details["email"] == email


def test_promote_to_admin_already_admin_writes_no_second_entry(existing_account):
    email, user_id = existing_account
    promote_to_admin(email)
    result = promote_to_admin(email)  # already an admin -- should be a no-op

    assert result == 0
    with get_session() as session:
        count = (
            session.query(AdminAuditLog)
            .filter(AdminAuditLog.target_type == "user", AdminAuditLog.target_id == str(user_id))
            .count()
        )
        assert count == 1


def test_promote_to_admin_nonexistent_email_returns_error_and_writes_no_entry():
    email = _unique_email()
    result = promote_to_admin(email)
    assert result == 1
    with get_session() as session:
        count = session.query(AdminAuditLog).filter(AdminAuditLog.action == "admin_promoted").count()
        # Can't assert count == 0 globally (other tests write entries too),
        # but nothing here should reference this specific never-created email.
        entries = session.query(AdminAuditLog).filter(AdminAuditLog.details["email"].astext == email).all()
        assert entries == []
        assert count >= 0
