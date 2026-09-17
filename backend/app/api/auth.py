from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from ..auth.dependencies import get_current_user_required
from ..auth.security import create_access_token, hash_password, verify_password
from ..core.errors import UserFacingError
from ..db import User, get_session
from ..models.schemas import AuthResponse, DeleteAccountRequest, LoginRequest, SignupRequest, UserResponse
from ..services.rate_limiter import RateLimiter
from ..services.storage import StorageService

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Accounts are entirely optional (brief: don't force sign-up before the core
# try-on flow works) — this router only exists for people who want to save
# results to a permanent library. See app/api/tryon.py's /save endpoint.

GENERIC_LOGIN_ERROR = "Incorrect email or password."

# login/signup rate limiting -- a materially different threat profile than
# the abuse/cost protection elsewhere (extraction, try-on): a high-value
# target for automated credential-stuffing and brute-force. Two independent
# keys, both enforced, because either alone has a bypass:
#   - IP-only would let an attacker brute-force ONE account from many/
#     rotating IPs without ever tripping a per-IP limit.
#   - account(email)-only would let an attacker hammer MANY different
#     accounts from ONE IP (credential stuffing) without ever tripping a
#     per-account limit, since each targeted account gets its own fresh
#     budget.
# The 429 response is identical either way (same generic message, same
# Retry-After header shape) regardless of which check failed or whether the
# submitted email corresponds to a real account -- rate-limiting must never
# become a side channel for account enumeration on top of the existing
# same-message guarantee on login failures themselves (see GENERIC_LOGIN_ERROR).
#
# Known limitation, not pretended otherwise: RateLimiter (services/
# rate_limiter.py) is in-memory and process-local. Behind multiple worker
# processes/instances, each has its own independent counters -- an attacker
# spread across enough concurrent connections could get a multiple of the
# configured limit in aggregate. Real distributed protection needs a shared
# backend (e.g. Redis) and is out of scope for this change; see
# docs/DEVELOPMENT.md for where this is tracked.


def get_storage(request: Request) -> StorageService:
    return request.app.state.storage


def get_auth_login_ip_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.auth_login_ip_rate_limiter


def get_auth_login_account_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.auth_login_account_rate_limiter


def get_auth_signup_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.auth_signup_rate_limiter


def _check_rate_limit(client_key: str, limiter: RateLimiter) -> None:
    """Same convention as api/extraction.py's _check_rate_limit / api/
    tryon.py's inline equivalent: HTTPException(429) with a Retry-After
    header. Takes an explicit key rather than deriving one from `request`
    internally, so this one helper covers both the IP-keyed and
    account-keyed checks below with one generic, never-differentiating
    message -- see the module-level note above on why that matters here
    specifically."""
    limit_result = limiter.check(client_key)
    if not limit_result.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Please try again in about {limit_result.retry_after_seconds} seconds.",
            headers={"Retry-After": str(limit_result.retry_after_seconds)},
        )


@router.post("/signup", response_model=AuthResponse, status_code=201)
async def signup(
    body: SignupRequest,
    request: Request,
    limiter: RateLimiter = Depends(get_auth_signup_rate_limiter),
):
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"ip:{client_ip}", limiter)

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
async def login(
    body: LoginRequest,
    request: Request,
    ip_limiter: RateLimiter = Depends(get_auth_login_ip_rate_limiter),
    account_limiter: RateLimiter = Depends(get_auth_login_account_rate_limiter),
):
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"ip:{client_ip}", ip_limiter)
    _check_rate_limit(f"email:{body.email.lower()}", account_limiter)

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
