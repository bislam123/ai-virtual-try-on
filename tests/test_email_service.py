"""Tests for backend/app/services/email_service.py -- ConsoleEmailService
(already covered for token-never-in-logs in test_password_reset.py; not
duplicated here) and the new SmtpEmailService/EmailDeliveryError.

No real network, no real SMTP server anywhere in this file -- smtplib.SMTP
itself is mocked (a fake/mock transport), per the milestone brief's own
instruction not to depend on a real external email provider.
"""

import logging
import smtplib

import pytest

from backend.app.services.email_service import (
    EmailDeliveryError,
    PASSWORD_RESET_SUBJECT,
    SmtpEmailService,
    _password_reset_html,
    _password_reset_plain_text,
)

RESET_URL = "https://app.example.com/?reset_token=abc123-fake-token-value"


def _make_service(**overrides):
    kwargs = dict(
        host="smtp.example.com",
        port=587,
        username="apikey",
        password="super-secret-smtp-password",
        use_tls=True,
        from_address="no-reply@example.com",
        from_name="AI Try-On",
    )
    kwargs.update(overrides)
    return SmtpEmailService(**kwargs)


# --- construction validation -----------------------------------------------------


def test_construction_rejects_empty_host():
    with pytest.raises(ValueError, match="host"):
        _make_service(host="")


def test_construction_rejects_empty_from_address():
    with pytest.raises(ValueError, match="from_address"):
        _make_service(from_address="")


def test_construction_succeeds_with_required_fields_present():
    _make_service()  # must not raise


# --- successful delivery via a fake/mock transport --------------------------------


class _FakeSmtp:
    """Stands in for smtplib.SMTP -- records every call instead of opening
    a real socket. Used as a context manager, same as the real class."""

    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.starttls_called = False
        self.login_args = None
        self.sent_message = None
        _FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self, context=None):
        self.starttls_called = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.sent_message = message


@pytest.fixture(autouse=True)
def _reset_fake_smtp():
    _FakeSmtp.instances = []
    yield
    _FakeSmtp.instances = []


def test_successful_send_connects_starttls_logs_in_and_sends(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    service = _make_service()

    service.send_password_reset_email("person@example.com", RESET_URL)

    assert len(_FakeSmtp.instances) == 1
    fake = _FakeSmtp.instances[0]
    assert fake.host == "smtp.example.com"
    assert fake.port == 587
    assert fake.starttls_called is True
    assert fake.login_args == ("apikey", "super-secret-smtp-password")
    assert fake.sent_message is not None
    assert fake.sent_message["To"] == "person@example.com"
    assert fake.sent_message["Subject"] == PASSWORD_RESET_SUBJECT
    assert "AI Try-On <no-reply@example.com>" == fake.sent_message["From"]


def test_send_without_username_skips_login(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    service = _make_service(username="", password="")

    service.send_password_reset_email("person@example.com", RESET_URL)

    assert _FakeSmtp.instances[0].login_args is None


def test_send_without_tls_skips_starttls(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    service = _make_service(use_tls=False)

    service.send_password_reset_email("person@example.com", RESET_URL)

    assert _FakeSmtp.instances[0].starttls_called is False


def test_message_contains_the_reset_url_in_both_plain_and_html_parts(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    service = _make_service()

    service.send_password_reset_email("person@example.com", RESET_URL)

    sent = _FakeSmtp.instances[0].sent_message
    assert RESET_URL in sent.get_body(preferencelist=("plain",)).get_content()
    assert RESET_URL in sent.get_body(preferencelist=("html",)).get_content()


# --- provider failure -------------------------------------------------------------


@pytest.mark.parametrize(
    "raised",
    [
        smtplib.SMTPConnectError(421, "Cannot connect"),
        smtplib.SMTPAuthenticationError(535, "Authentication failed"),
        smtplib.SMTPException("generic failure"),
        OSError("network unreachable"),
    ],
)
def test_smtp_failure_raises_email_delivery_error_not_the_original_exception(monkeypatch, raised):
    class _FailingSmtp(_FakeSmtp):
        def send_message(self, message):
            raise raised

    monkeypatch.setattr(smtplib, "SMTP", _FailingSmtp)
    service = _make_service()

    with pytest.raises(EmailDeliveryError):
        service.send_password_reset_email("person@example.com", RESET_URL)


def test_failure_log_never_contains_the_password_recipient_or_url(monkeypatch, caplog):
    class _FailingSmtp(_FakeSmtp):
        def send_message(self, message):
            raise smtplib.SMTPAuthenticationError(535, b"Authentication failed for super-secret-smtp-password")

    monkeypatch.setattr(smtplib, "SMTP", _FailingSmtp)
    service = _make_service()

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(EmailDeliveryError):
            service.send_password_reset_email("person@example.com", RESET_URL)

    assert "super-secret-smtp-password" not in caplog.text
    assert "person@example.com" not in caplog.text
    assert RESET_URL not in caplog.text
    assert "abc123-fake-token-value" not in caplog.text


def test_email_delivery_error_message_is_static_and_safe():
    """Never provider/exception detail -- see the exception's own
    docstring for why this must be true even before any route-level
    handling of it."""
    original = smtplib.SMTPAuthenticationError(535, b"leaked-server-detail")
    err = EmailDeliveryError("Failed to send password reset email.")
    err.__cause__ = original

    assert "leaked-server-detail" not in str(err)


# --- email content -----------------------------------------------------------------


def test_plain_text_body_contains_required_elements():
    body = _password_reset_plain_text(RESET_URL)

    assert "AI Try-On" in body  # application name
    assert RESET_URL in body  # the reset link
    assert "expire" in body.lower()  # expiration information
    assert "ignore" in body.lower()  # security warning for a non-requester


def test_html_body_contains_required_elements_and_escapes_the_url():
    body = _password_reset_html(RESET_URL)

    assert "AI Try-On" in body
    assert "expire" in body.lower()
    assert "ignore" in body.lower()
    # html.escape turns "&" into "&amp;" etc. -- confirm escaping actually
    # ran, using a URL with a query-string separator character.
    url_with_ampersand = "https://app.example.com/?a=1&reset_token=abc123"
    escaped = _password_reset_html(url_with_ampersand)
    assert "&amp;" in escaped
    assert "?a=1&reset_token" not in escaped  # the raw, unescaped form must not appear


def test_plain_text_body_never_contains_html_markup():
    body = _password_reset_plain_text(RESET_URL)
    assert "<a href" not in body
    assert "<html" not in body.lower()
