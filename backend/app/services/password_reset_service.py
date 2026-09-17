"""Password reset token lifecycle: issuing, atomically consuming, and
cleaning up. Kept separate from api/auth.py (which only handles HTTP
concerns — rate limiting, request/response shaping) the same way
job_recovery.py and quota_service.py separate their own domain logic from
the route handlers that call them.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..db import PasswordResetToken, User

logger = logging.getLogger(__name__)


def _hash_token(raw_token: str) -> str:
    # SHA-256, not bcrypt: this hash exists to make a database read
    # useless for reset (see PasswordResetToken's docstring), not to resist
    # offline guessing of a low-entropy secret like a password — the raw
    # token itself already has 256 bits of secrets.token_urlsafe entropy,
    # so a fast, deterministic hash (needed anyway for an indexed lookup by
    # hash) loses nothing here.
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_reset_token(session: Session, user: User) -> str:
    """Creates a new single-use reset token for `user`, first invalidating
    any earlier still-outstanding (unused) ones — at most one valid reset
    link exists for an account at a time, so an older, forgotten link (a
    previous request, a different device) can't remain usable alongside a
    newer one. Returns the raw token; callers must send it to the user and
    never store or log it themselves — only its hash is persisted here.
    """
    session.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)
    ).delete(synchronize_session=False)

    raw_token = secrets.token_urlsafe(32)  # 256 bits — not brute-forceable
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.password_reset_token_expire_minutes),
        )
    )
    session.flush()
    return raw_token


def consume_reset_token(session: Session, raw_token: str) -> Optional[User]:
    """Atomically claims `raw_token` for one-time use and returns the user
    it belongs to, or None if it's missing, expired, or already used —
    deliberately not distinguished (see api/auth.py's single generic error
    message for all three) so a response can't be used to probe which of
    those applies to a given token.

    Atomicity is what makes this race-safe under real concurrent requests
    for the *same* token, not an application-level check-then-act: the
    UPDATE's WHERE clause (used_at IS NULL AND expires_at > now) only
    matches a row that is still genuinely claimable, and only one
    concurrent UPDATE can ever win it. Two callers racing the same token:
    Postgres serializes the second UPDATE behind the first's row lock (it
    blocks), then re-evaluates the WHERE clause against the now-committed
    row once the first transaction commits — since used_at is no longer
    NULL, the second UPDATE matches zero rows. Same style of database-level
    guarantee as jobs.ix_jobs_idempotency_active's partial unique index
    (see db/models.py's JobRecord), just via an UPDATE...WHERE claim
    instead of a unique-index conflict. Verified directly against real
    concurrent threads in tests/test_password_reset.py, not just reasoned
    about.
    """
    token_hash = _hash_token(raw_token)
    now = datetime.now(timezone.utc)

    claimed = (
        session.query(PasswordResetToken)
        .filter(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .update({"used_at": now}, synchronize_session=False)
    )
    if claimed == 0:
        return None

    reset_token = session.query(PasswordResetToken).filter(PasswordResetToken.token_hash == token_hash).first()
    return session.get(User, reset_token.user_id)


@dataclass
class ResetTokenCleanupResult:
    deleted: int = 0


def cleanup_expired_password_reset_tokens(session: Session) -> ResetTokenCleanupResult:
    """Deletes reset tokens that can never be used again — already
    consumed (used_at set) or past expiry — so the table doesn't grow
    unboundedly. Purely tidying, not a security boundary:
    consume_reset_token's own WHERE clause already refuses either kind
    regardless of whether this has run. Called from
    backend/scripts/cleanup_expired_results.py's periodic sweep alongside
    its existing jobs — see that script for the scheduling caveat (not
    automatically scheduled anywhere in this repo yet).
    """
    now = datetime.now(timezone.utc)
    deleted = (
        session.query(PasswordResetToken)
        .filter(or_(PasswordResetToken.used_at.isnot(None), PasswordResetToken.expires_at < now))
        .delete(synchronize_session=False)
    )
    logger.info("Deleted %d expired/consumed password reset token(s).", deleted)
    return ResetTokenCleanupResult(deleted=deleted)
