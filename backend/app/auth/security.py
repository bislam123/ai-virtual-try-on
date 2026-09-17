"""Password hashing and JWT helpers.

Deliberately small and dependency-light: bcrypt directly (not passlib —
recent passlib/bcrypt version pins are a known source of breakage) and PyJWT
directly. This is the *only* file that should import either.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from ..config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash — never let this crash the request.
        return False


@dataclass(frozen=True)
class AccessTokenClaims:
    """What a valid, decoded access token actually contains — deliberately
    just an id and a version, nothing a leaked/logged token would make
    sensitive (see this module's own docstring: no password, no email).
    `auth_version` is what auth/dependencies.py's get_current_user_optional
    checks against the live User row to implement revocation — see
    db/models.py's User.auth_version for the full design."""

    user_id: int
    auth_version: int


def create_access_token(user_id: int, auth_version: int) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "exp": expires_at, "ver": auth_version}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[AccessTokenClaims]:
    """Returns the claims encoded in `token`, or None if it's missing,
    malformed, has an invalid signature, is expired, or lacks the "ver"
    claim.

    That last case is deliberate, not an oversight: a token issued before
    User.auth_version existed (or otherwise missing "ver") has nothing to
    validate a revocation check against, so it's rejected outright — a
    fail-closed default rather than treating an absent version as
    automatically current/trusted. In this project's actual timeline this
    only ever matters for one moment (any token issued before this feature
    shipped stops working the instant it does, forcing a one-time
    re-login) rather than an ongoing compatibility concern, since every
    token minted by create_access_token from this point on always includes
    it. This function itself does NOT check auth_version against the
    database — it has no session to do that with, by design (pure,
    stateless JWT verification); auth/dependencies.py's
    get_current_user_optional does that comparison against the live User
    row once it has one.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return AccessTokenClaims(user_id=int(payload["sub"]), auth_version=int(payload["ver"]))
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None
