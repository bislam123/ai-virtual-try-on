from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from ..auth.dependencies import get_current_user_required
from ..auth.security import create_access_token, hash_password, verify_password
from ..config import settings
from ..core.errors import UserFacingError
from ..db import User, get_session
from ..models.schemas import (
    AuthResponse,
    DeleteAccountRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SignupRequest,
    UserResponse,
)
from ..services.email_service import EmailService
from ..services.password_reset_service import consume_reset_token, issue_reset_token
from ..services.rate_limiter import RateLimiter
from ..services.storage import StorageService

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Accounts are entirely optional (brief: don't force sign-up before the core
# try-on flow works) — this router only exists for people who want to save
# results to a permanent library. See app/api/tryon.py's /save endpoint.

GENERIC_LOGIN_ERROR = "Incorrect email or password."

# Forgot-password's whole security model rests on this being *the one and
# only* response for that endpoint — sent whether the account exists or
# not, whether an email actually got sent or not, identical byte-for-byte
# either way. See forgot_password() below.
FORGOT_PASSWORD_GENERIC_MESSAGE = "If an account exists for that email, we've sent a link to reset your password."

# Same principle as GENERIC_LOGIN_ERROR: one message for every way a reset
# token can fail to be honored (never existed, expired, already used) —
# never differentiated, so a response can't be used to probe which case
# applies. See reset_password() below / password_reset_service.consume_reset_token.
RESET_TOKEN_INVALID_ERROR = "This password reset link is invalid or has expired. Please request a new one."

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


def get_auth_forgot_password_ip_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.auth_forgot_password_ip_rate_limiter


def get_auth_forgot_password_email_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.auth_forgot_password_email_rate_limiter


def get_email_service(request: Request) -> EmailService:
    return request.app.state.email_service


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


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    ip_limiter: RateLimiter = Depends(get_auth_forgot_password_ip_rate_limiter),
    email_limiter: RateLimiter = Depends(get_auth_forgot_password_email_rate_limiter),
    email_service: EmailService = Depends(get_email_service),
):
    """Always returns FORGOT_PASSWORD_GENERIC_MESSAGE, whether or not
    `body.email` belongs to a real account and whether or not an email was
    actually sent -- the same never-reveal-account-existence guarantee
    login() applies to GENERIC_LOGIN_ERROR, applied here to the response
    itself rather than to an error message, since this endpoint has no
    failure mode that's safe to expose at all. Rate-limited exactly like
    login (dual IP + email key, same generic 429 either way, see
    _check_rate_limit) before ever touching the database, so a rate-limit
    response itself is equally uninformative about whether the email is
    registered.
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"ip:{client_ip}", ip_limiter)
    _check_rate_limit(f"email:{body.email.lower()}", email_limiter)

    with get_session() as session:
        user = session.query(User).filter(User.email == body.email.lower()).first()
        if user is not None:
            raw_token = issue_reset_token(session, user)
            reset_url = f"{settings.frontend_base_url.rstrip('/')}/?reset_token={raw_token}"
            email_service.send_password_reset_email(user.email, reset_url)
        # else: deliberately a no-op -- no account is created, nothing is
        # logged that would distinguish this from the found-user branch.

    return MessageResponse(message=FORGOT_PASSWORD_GENERIC_MESSAGE)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(body: ResetPasswordRequest):
    """Consumes a single-use reset token and sets the account's new
    password. See password_reset_service.consume_reset_token for the
    atomic single-use/race-safety guarantee and RESET_TOKEN_INVALID_ERROR
    for why an invalid, expired, and already-used token are all reported
    identically.

    Session/JWT invalidation on password change -- inspected, not
    implemented: this app's auth is fully stateless (auth/security.py's
    decode_access_token only verifies a JWT's signature and expiry, no
    server-side session/allow-list lookup — see that module). There is no
    revocation mechanism to hook into, and building one (a token
    blocklist/version column plus a check on every authenticated request)
    is a materially larger, separate architecture change than this
    milestone's scope -- explicitly not undertaken here rather than
    invented ad hoc. Known, accepted limitation: any access token issued
    before a reset remains valid (up to jwt_expire_minutes, 7 days by
    default) until it naturally expires. The new password itself is
    required immediately for any *new* login, which is the property this
    endpoint can actually guarantee today.
    """
    with get_session() as session:
        user = consume_reset_token(session, body.token)
        if user is None:
            raise UserFacingError(RESET_TOKEN_INVALID_ERROR, status_code=400)
        user.password_hash = hash_password(body.new_password)

    return MessageResponse(message="Your password has been reset. Please sign in with your new password.")


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
