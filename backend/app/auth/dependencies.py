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
        session.expunge(user)
        return user


def get_current_user_required(user: Optional[User] = Depends(get_current_user_optional)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in to use this feature.")
    return user
