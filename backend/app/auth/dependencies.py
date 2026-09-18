from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..db import User, get_session
from .security import decode_access_token

# auto_error=False: a missing Authorization header is not an error by
# itself — most endpoints (try-on included) work anonymously. Endpoints that
# require login use get_current_user_required below instead.
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[User]:
    if credentials is None:
        return None
    claims = decode_access_token(credentials.credentials)
    if claims is None:
        return None
    with get_session() as session:
        user = session.get(User, claims.user_id)
        if user is None:
            return None
        # Revocation check: a token stays valid only as long as its
        # embedded auth_version still matches the live row's. A password
        # reset (api/auth.py's reset_password) bumps User.auth_version,
        # which is what makes every token issued before that reset stop
        # working from this exact check, on its very next use — no
        # blocklist, no session table, just this one comparison against
        # data the request already has to load to authenticate at all.
        # Account deletion needs no equivalent check here: the row is gone
        # entirely, so `user is None` above already covers it.
        if user.auth_version != claims.auth_version:
            return None
        # Admin/Operations milestone: a disabled account's existing tokens
        # stop working immediately, on this exact next request, the same
        # way a revoked auth_version does above -- not just future logins
        # (see api/auth.py's login). Fails closed like every other branch
        # here: never distinguishes "disabled" from "invalid token" to the
        # caller, same treatment as a deleted/nonexistent user.
        if not user.is_active:
            return None
        session.expunge(user)
        return user


def get_current_user_required(user: Optional[User] = Depends(get_current_user_optional)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in to use this feature.")
    return user


def get_current_admin_user(user: User = Depends(get_current_user_required)) -> User:
    """Admin/Operations milestone: gates every /api/admin/* route.

    Composes on top of get_current_user_required, not a separate check --
    an unauthenticated request gets the exact same 401 every other
    protected endpoint already gives ("please sign in"), never a
    different, admin-specific unauthenticated response that could hint
    this route is special. Only an authenticated non-admin gets 403,
    matching the milestone brief exactly. is_admin is read from the same
    already-loaded User row every other check here uses -- never accepted
    from the request itself (no such field exists on any request schema),
    so nothing client-supplied can ever satisfy this.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user
