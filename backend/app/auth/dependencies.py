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
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        return None
    with get_session() as session:
        user = session.get(User, user_id)
        if user is None:
            return None
        session.expunge(user)
        return user


def get_current_user_required(user: Optional[User] = Depends(get_current_user_optional)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in to use this feature.")
    return user
