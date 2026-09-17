from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError

from ..auth.dependencies import get_current_user_required
from ..auth.security import create_access_token, hash_password, verify_password
from ..core.errors import UserFacingError
from ..db import User, get_session
from ..models.schemas import AuthResponse, DeleteAccountRequest, LoginRequest, SignupRequest, UserResponse
from ..services.storage import StorageService

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Accounts are entirely optional (brief: don't force sign-up before the core
# try-on flow works) — this router only exists for people who want to save
# results to a permanent library. See app/api/tryon.py's /save endpoint.

GENERIC_LOGIN_ERROR = "Incorrect email or password."


def get_storage(request: Request) -> StorageService:
    return request.app.state.storage


@router.post("/signup", response_model=AuthResponse, status_code=201)
async def signup(body: SignupRequest):
    with get_session() as session:
        user = User(email=body.email.lower(), password_hash=hash_password(body.password))
        session.add(user)
        try:
            session.flush()  # assigns user.id, surfaces the unique-email constraint now
        except IntegrityError:
            raise UserFacingError("An account with that email already exists.", status_code=409)
        token = create_access_token(user.id)
    return AuthResponse(access_token=token)


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest):
    with get_session() as session:
        user = session.query(User).filter(User.email == body.email.lower()).first()
        if user is None or not verify_password(body.password, user.password_hash):
            # Same message either way — never reveal whether the email is registered.
            raise UserFacingError(GENERIC_LOGIN_ERROR, status_code=401)
        token = create_access_token(user.id)
    return AuthResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user_required)):
    return UserResponse(id=user.id, email=user.email, plan=user.plan, created_at=user.created_at)


@router.delete("/me", status_code=204)
async def delete_account(
    body: DeleteAccountRequest,
    user: User = Depends(get_current_user_required),
    storage: StorageService = Depends(get_storage),
):
    """Permanently deletes the signed-in account and everything tied to it:
    every job's result image and any leftover temp files, then the job rows
    themselves (DB-level ON DELETE CASCADE on jobs.user_id, see db/models.py)
    and finally the user row. Requires the current password, not just a
    valid access token — a leaked/stolen token alone must not be enough to
    trigger an irreversible destructive action. `user` (from the dependency)
    is detached from its own session by this point, so the id is the only
    field read from it directly; everything else is re-fetched fresh here.
    """
    with get_session() as session:
        db_user = session.get(User, user.id)
        if db_user is None or not verify_password(body.password, db_user.password_hash):
            raise UserFacingError(GENERIC_LOGIN_ERROR, status_code=401)

        for job in db_user.jobs:
            storage.delete_result(job.id)
            storage.cleanup_temp(job.id)

        session.delete(db_user)
    return None
