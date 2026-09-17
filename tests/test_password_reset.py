"""Password reset tests (see backend/app/services/password_reset_service.py
and api/auth.py's forgot-password/reset-password endpoints).

Same real-Postgres, auto-skip-if-unreachable convention as
test_auth_api.py — issuing/consuming a reset token relies on real
uniqueness/atomicity guarantees (the single-use UPDATE...WHERE claim,
concurrent-safety under real threads) that aren't worth faking with a
mock session.

Run with: ai\\.venv\\Scripts\\python.exe -m pytest tests/test_password_reset.py
"""

import hashlib
import logging
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.api import auth as auth_module
from backend.app.core.errors import configure_exception_handlers
from backend.app.db import PasswordResetToken, User, engine, get_session
from backend.app.services.email_service import EmailService
from backend.app.services.password_reset_service import cleanup_expired_password_reset_tokens, issue_reset_token
from backend.app.services.rate_limiter import RateLimiter


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


class FakeEmailService(EmailService):
    """Captures every "sent" email instead of printing it, so tests can
    recover the raw reset token from the URL the real endpoint generated —
    exactly what a real integration test would do against a captured
    outbound email, not a shortcut around the real flow. The real
    ConsoleEmailService is exercised directly in
    test_console_email_service_prints_the_reset_link below."""

    def __init__(self):
        self.sent = []

    def send_password_reset_email(self, to_email, reset_url):
        self.sent.append((to_email, reset_url))


def _extract_token(reset_url: str) -> str:
    match = re.search(r"[?&]reset_token=([^&]+)", reset_url)
    assert match, f"no reset_token found in reset url: {reset_url}"
    return match.group(1)


def make_test_app(
    forgot_password_ip_rate_limit=1000,
    forgot_password_email_rate_limit=1000,
    email_service=None,
):
    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(auth_module.router)
    # Generous by default (matches test_auth_api.py's make_test_app
    # convention) — tests that specifically verify forgot-password rate
    # limiting pass a small value for the relevant parameter instead.
    app.state.auth_login_ip_rate_limiter = RateLimiter(max_requests=1000, window_seconds=900)
    app.state.auth_login_account_rate_limiter = RateLimiter(max_requests=1000, window_seconds=900)
    app.state.auth_signup_rate_limiter = RateLimiter(max_requests=1000, window_seconds=3600)
    app.state.auth_forgot_password_ip_rate_limiter = RateLimiter(
        max_requests=forgot_password_ip_rate_limit, window_seconds=3600
    )
    app.state.auth_forgot_password_email_rate_limiter = RateLimiter(
        max_requests=forgot_password_email_rate_limit, window_seconds=3600
    )
    app.state.email_service = email_service if email_service is not None else FakeEmailService()
    return app


@pytest.fixture
def email_service():
    return FakeEmailService()


@pytest.fixture
def client(email_service):
    with TestClient(make_test_app(email_service=email_service)) as c:
        yield c


@pytest.fixture
def test_email():
    """A unique email per test, cleaned up from the shared local dev
    database afterward — same fixture shape as test_auth_api.py's."""
    email = f"test-{uuid.uuid4().hex}@example.com"
    yield email
    with get_session() as session:
        user = session.query(User).filter(User.email == email).first()
        if user is not None:
            session.delete(user)


def signup(client, email, password="correct-horse-battery"):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


def forgot_password(client, email):
    return client.post("/api/auth/forgot-password", json={"email": email})


def reset_password(client, token, new_password):
    return client.post("/api/auth/reset-password", json={"token": token, "new_password": new_password})


def login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# --- forgot-password: enumeration-safety & basic behavior --------------------


def test_forgot_password_known_email_sends_email_and_returns_generic_message(client, test_email, email_service):
    signup(client, test_email)

    resp = forgot_password(client, test_email)

    assert resp.status_code == 200
    assert resp.json() == {"message": resp.json()["message"]}
    assert "If an account exists" in resp.json()["message"]
    assert len(email_service.sent) == 1
    assert email_service.sent[0][0] == test_email


def test_forgot_password_unknown_email_returns_200_and_sends_nothing(client, email_service):
    resp = forgot_password(client, f"nobody-here-{uuid.uuid4().hex}@example.com")

    assert resp.status_code == 200
    assert len(email_service.sent) == 0  # no account, no email sent


def test_forgot_password_unknown_and_known_email_return_the_identical_response_body(
    client, test_email, email_service
):
    signup(client, test_email)

    known = forgot_password(client, test_email)
    unknown = forgot_password(client, f"never-registered-{uuid.uuid4().hex}@example.com")

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_forgot_password_does_not_create_an_account(client):
    email = f"never-signed-up-{uuid.uuid4().hex}@example.com"

    forgot_password(client, email)

    with get_session() as session:
        assert session.query(User).filter(User.email == email).first() is None


def test_forgot_password_is_case_insensitive_like_signup_and_login(client, test_email, email_service):
    signup(client, test_email)

    resp = forgot_password(client, test_email.upper())

    assert resp.status_code == 200
    assert len(email_service.sent) == 1


def test_forgot_password_malformed_email_rejected(client):
    assert client.post("/api/auth/forgot-password", json={"email": "not-an-email"}).status_code == 422


def test_forgot_password_missing_email_rejected(client):
    assert client.post("/api/auth/forgot-password", json={}).status_code == 422


# --- forgot-password: rate limiting ------------------------------------------


def test_forgot_password_allows_requests_up_to_the_ip_limit(test_email):
    with TestClient(make_test_app(forgot_password_ip_rate_limit=2)) as client:
        signup(client, test_email)
        for _ in range(2):
            assert forgot_password(client, test_email).status_code == 200


def test_forgot_password_rate_limited_by_ip(test_email):
    with TestClient(make_test_app(forgot_password_ip_rate_limit=2)) as client:
        signup(client, test_email)
        for _ in range(2):
            forgot_password(client, test_email)

        limited = forgot_password(client, test_email)
        assert limited.status_code == 429
        assert "Retry-After" in limited.headers


def test_forgot_password_rate_limited_by_email():
    with TestClient(make_test_app(forgot_password_email_rate_limit=2)) as client:
        email = f"rate-limit-target-{uuid.uuid4().hex}@example.com"
        for _ in range(2):
            forgot_password(client, email)

        limited = forgot_password(client, email)
        assert limited.status_code == 429


def test_forgot_password_rate_limit_response_identical_for_real_and_fake_email():
    """Enumeration-safety extends to the rate-limit path itself: once
    rate-limited, the response must be indistinguishable whether the
    targeted email is real or made up — same principle
    test_auth_api.py::test_login_rate_limit_response_does_not_reveal_account_existence
    already establishes for login."""
    with TestClient(make_test_app(forgot_password_email_rate_limit=1)) as client:
        real_email = f"real-target-{uuid.uuid4().hex}@example.com"
        signup(client, real_email)
        forgot_password(client, real_email)
        real_limited = forgot_password(client, real_email)

        fake_email = f"fake-target-{uuid.uuid4().hex}@example.com"
        forgot_password(client, fake_email)
        fake_limited = forgot_password(client, fake_email)

    assert real_limited.status_code == fake_limited.status_code == 429
    real_detail = re.sub(r"\d+", "N", real_limited.json()["detail"])
    fake_detail = re.sub(r"\d+", "N", fake_limited.json()["detail"])
    assert real_detail == fake_detail

    with get_session() as session:
        session.query(User).filter(User.email == real_email).delete(synchronize_session=False)


def test_forgot_password_rate_limiting_does_not_affect_login_or_signup(test_email):
    with TestClient(make_test_app(forgot_password_ip_rate_limit=1)) as client:
        signup(client, test_email, password="right-password")
        forgot_password(client, test_email)
        assert forgot_password(client, test_email).status_code == 429  # forgot-password now rate-limited

        # Unrelated routes, same client/IP, same process: unaffected.
        assert login(client, test_email, "right-password").status_code == 200


# --- reset-password: token validity ------------------------------------------


def test_reset_password_with_valid_token_changes_password(client, test_email, email_service):
    signup(client, test_email, password="old-password-123")
    forgot_password(client, test_email)
    token = _extract_token(email_service.sent[0][1])

    resp = reset_password(client, token, "new-password-456")

    assert resp.status_code == 200, resp.text
    assert login(client, test_email, "new-password-456").status_code == 200
    assert login(client, test_email, "old-password-123").status_code == 401


def test_reset_password_invalid_token_rejected(client):
    resp = reset_password(client, "totally-made-up-token", "new-password-456")
    assert resp.status_code == 400
    assert resp.json()["detail"]


def test_reset_password_expired_token_rejected(client, test_email):
    signup(client, test_email, password="old-password-123")
    with get_session() as session:
        user = session.query(User).filter(User.email == test_email).first()
        raw_token = issue_reset_token(session, user)
        session.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).update(
            {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}
        )

    resp = reset_password(client, raw_token, "new-password-456")

    assert resp.status_code == 400
    # A rejected attempt must be a no-op — the old password still works.
    assert login(client, test_email, "old-password-123").status_code == 200


def test_reset_password_already_used_token_rejected(client, test_email, email_service):
    signup(client, test_email, password="old-password-123")
    forgot_password(client, test_email)
    token = _extract_token(email_service.sent[0][1])

    first = reset_password(client, token, "new-password-456")
    assert first.status_code == 200

    second = reset_password(client, token, "yet-another-password-789")
    assert second.status_code == 400

    # The first reset's password is the one that stuck — reuse was a no-op.
    assert login(client, test_email, "new-password-456").status_code == 200
    assert login(client, test_email, "yet-another-password-789").status_code == 401


def test_invalid_expired_and_used_tokens_share_the_identical_error_message(client, test_email, email_service):
    signup(client, test_email, password="old-password-123")

    forgot_password(client, test_email)
    used_token = _extract_token(email_service.sent[-1][1])
    reset_password(client, used_token, "new-password-456")
    used_resp = reset_password(client, used_token, "another-password-000")

    invalid_resp = reset_password(client, "totally-made-up-token", "another-password-000")

    assert used_resp.status_code == invalid_resp.status_code == 400
    assert used_resp.json()["detail"] == invalid_resp.json()["detail"]


def test_reset_password_concurrent_attempts_with_same_token_only_one_succeeds(client, test_email, email_service):
    """Same race-safety proof style as
    test_auth_api.py::test_concurrent_duplicate_requests_against_real_database_create_only_one_job
    — real threads racing the exact same token against the real database,
    not just reasoning about the UPDATE...WHERE claim in isolation."""
    signup(client, test_email, password="old-password-123")
    forgot_password(client, test_email)
    token = _extract_token(email_service.sent[0][1])

    barrier = threading.Barrier(2)
    results = [None, None]

    def attempt(i, password):
        barrier.wait()
        results[i] = reset_password(client, token, password)

    threads = [
        threading.Thread(target=attempt, args=(0, "first-new-password-1")),
        threading.Thread(target=attempt, args=(1, "second-new-password-2")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400]  # exactly one wins, the other sees an already-used token

    with get_session() as session:
        user = session.query(User).filter(User.email == test_email).first()
        used_count = (
            session.query(PasswordResetToken)
            .filter(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.isnot(None))
            .count()
        )
    assert used_count == 1  # exactly one row claimed, not zero and not two


def test_new_forgot_password_request_invalidates_earlier_outstanding_token(client, test_email, email_service):
    signup(client, test_email, password="old-password-123")
    forgot_password(client, test_email)
    first_token = _extract_token(email_service.sent[0][1])

    forgot_password(client, test_email)
    second_token = _extract_token(email_service.sent[1][1])

    assert first_token != second_token
    assert reset_password(client, first_token, "new-password-456").status_code == 400  # superseded
    assert reset_password(client, second_token, "new-password-456").status_code == 200  # still valid


# --- reset-password: malformed input / password policy ------------------------


def test_reset_password_missing_fields_rejected(client):
    assert client.post("/api/auth/reset-password", json={}).status_code == 422
    assert client.post("/api/auth/reset-password", json={"token": "x"}).status_code == 422
    assert client.post("/api/auth/reset-password", json={"new_password": "longenoughpassword"}).status_code == 422


def test_reset_password_empty_token_rejected(client):
    assert reset_password(client, "", "long-enough-password").status_code == 422


def test_reset_password_enforces_existing_password_policy(client, test_email, email_service):
    """Same min_length=8 policy as SignupRequest.password — reused, not
    reimplemented (see models/schemas.py's ResetPasswordRequest)."""
    signup(client, test_email, password="old-password-123")
    forgot_password(client, test_email)
    token = _extract_token(email_service.sent[0][1])

    too_short = reset_password(client, token, "short1")
    assert too_short.status_code == 422

    # A validation failure must never consume the token — it's still
    # usable afterward.
    ok = reset_password(client, token, "long-enough-password")
    assert ok.status_code == 200


# --- no leakage ---------------------------------------------------------------


def test_reset_token_never_appears_in_forgot_password_response(client, test_email, email_service):
    signup(client, test_email)
    resp = forgot_password(client, test_email)
    token = _extract_token(email_service.sent[0][1])

    assert token not in resp.text


def test_reset_token_never_appears_in_reset_password_error_response(client):
    fake_token = "a-fake-but-realistic-looking-token-value"
    resp = reset_password(client, fake_token, "long-enough-password")

    assert fake_token not in resp.text


def test_only_a_hash_of_the_token_is_stored_in_the_database(client, test_email, email_service):
    signup(client, test_email)
    forgot_password(client, test_email)
    token = _extract_token(email_service.sent[0][1])

    with get_session() as session:
        user = session.query(User).filter(User.email == test_email).first()
        stored = session.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).first()

    assert stored.token_hash != token
    assert stored.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_no_raw_token_reaches_application_logs(client, test_email, email_service, caplog):
    with caplog.at_level(logging.DEBUG):
        signup(client, test_email)
        forgot_password(client, test_email)
        token = _extract_token(email_service.sent[0][1])
        reset_password(client, token, "new-password-456")

    assert token not in caplog.text


def test_console_email_service_never_logs_the_raw_token(caplog):
    """The real (dev) EmailService implementation, not the test double —
    confirms ConsoleEmailService.send_password_reset_email prints (does
    not log) the link, and its own logger.info call carries no PII/token
    (see services/email_service.py's module docstring for why)."""
    from backend.app.services.email_service import ConsoleEmailService

    service = ConsoleEmailService()
    fake_url = f"http://localhost:5173/?reset_token={uuid.uuid4().hex}"

    with caplog.at_level(logging.DEBUG):
        service.send_password_reset_email("someone@example.com", fake_url)

    assert fake_url not in caplog.text
    assert "someone@example.com" not in caplog.text


# --- cleanup sweep (backend/scripts/cleanup_expired_results.py's third job) ---


def test_cleanup_deletes_expired_and_used_tokens_but_preserves_valid_ones(test_email):
    signup_email = test_email
    with get_session() as session:
        user = User(email=signup_email, password_hash="irrelevant-for-this-test")
        session.add(user)
        session.flush()
        user_id = user.id

        expired = PasswordResetToken(
            user_id=user_id,
            token_hash="expired-token-hash-" + uuid.uuid4().hex,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        used = PasswordResetToken(
            user_id=user_id,
            token_hash="used-token-hash-" + uuid.uuid4().hex,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            used_at=datetime.now(timezone.utc),
        )
        valid = PasswordResetToken(
            user_id=user_id,
            token_hash="valid-token-hash-" + uuid.uuid4().hex,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        session.add_all([expired, used, valid])
        session.flush()
        valid_hash = valid.token_hash

        cleanup_expired_password_reset_tokens(session)

        remaining = session.query(PasswordResetToken).filter(PasswordResetToken.user_id == user_id).all()

    assert [t.token_hash for t in remaining] == [valid_hash]


def test_cleanup_is_idempotent(test_email):
    with get_session() as session:
        user = User(email=test_email, password_hash="irrelevant-for-this-test")
        session.add(user)
        session.flush()
        session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash="expired-token-hash-" + uuid.uuid4().hex,
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
        )
        session.flush()

        first = cleanup_expired_password_reset_tokens(session)
        second = cleanup_expired_password_reset_tokens(session)  # nothing left to delete -- must not error

    assert first.deleted >= 1
    assert second.deleted == 0


# --- unaffected existing behavior ---------------------------------------------


def test_signup_and_login_unaffected_by_password_reset_feature(client, test_email):
    assert signup(client, test_email, password="right-password").status_code == 201
    assert login(client, test_email, "right-password").status_code == 200
    assert login(client, test_email, "wrong-password").status_code == 401
