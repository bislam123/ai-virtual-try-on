"""Password-reset email delivery abstraction.

Two implementations:

`ConsoleEmailService` -- the dev/test stand-in. Instead of a real send, it
prints the message to stdout, exactly how Django's "console" email backend
works. Exists purely so a developer testing the forgot-password flow
locally can read the reset link straight out of the terminal running the
server. Selected whenever `AITRYON_EMAIL_PROVIDER` is left at its default,
"console" (see app/config.py) -- and `Settings.check_production_email_provider`
refuses to start with `AITRYON_ENVIRONMENT=production` while it's still
selected, so this can never silently reach production.

`SmtpEmailService` -- real delivery via Python's stdlib `smtplib`, not a
vendor-specific SDK. Every major transactional-email provider (SendGrid,
Mailgun, Amazon SES, Postmark, ...) also exposes a plain SMTP relay
(typically with an API key as the SMTP password), so one stdlib-only
implementation covers all of them without adding a new dependency or
coupling this codebase to one vendor's API shape -- the same "swap the
implementation behind a stable interface" pattern
`providers/selfhosted.py`'s `VirtualTryOnProvider` already establishes for
the AI model. Selected via `AITRYON_EMAIL_PROVIDER=smtp` plus the
accompanying `AITRYON_SMTP_*`/`AITRYON_EMAIL_FROM_ADDRESS` settings (see
app/config.py and docs/ENVIRONMENT.md). `app/main.py`'s `lifespan()` is the
only place that decides which one gets constructed -- `api/auth.py`'s
route handler never has to change either way.

Deliberately prints (Console) / logs only an exception's type (SMTP)
rather than ever logging the raw reset token, URL, or recipient address:
`api/auth.py`'s forgot-password endpoint must never put the raw reset
token/URL into the application's structured logs (a log line here is
exactly that channel), and the SMTP path must never let a server's error
response (which can echo back request detail) leak into a log line either.
"""

import html
import logging
import smtplib
import ssl
from abc import ABC, abstractmethod
from email.message import EmailMessage
from email.utils import formataddr

from ..config import settings

logger = logging.getLogger(__name__)

PASSWORD_RESET_SUBJECT = "Reset your AI Try-On password"


class EmailService(ABC):
    @abstractmethod
    def send_password_reset_email(self, to_email: str, reset_url: str) -> None: ...


class EmailDeliveryError(Exception):
    """Raised by a real (non-Console) EmailService implementation when
    sending genuinely fails -- an SMTP connection/authentication/send
    error, or a provider's relay rejecting the message. Caught at the
    route layer (api/auth.py's forgot_password) and never allowed to
    become a distinct response, expose provider detail, or change
    forgot-password's response shape -- see that function's own
    docstring for why the generic response must stay identical whether
    or not delivery actually succeeded. The message on this exception is
    always the static, safe text below -- never provider/exception
    detail -- so it's always safe to have reached this far even before
    the route's own handling.
    """


def _password_reset_plain_text(reset_url: str) -> str:
    expire_minutes = settings.password_reset_token_expire_minutes
    return (
        "AI Try-On\n\n"
        "We received a request to reset the password for your AI Try-On account.\n\n"
        f"Reset your password: {reset_url}\n\n"
        f"This link will expire in {expire_minutes} minutes and can only be used once.\n\n"
        "If you didn't request this, you can safely ignore this email -- your password will not be changed.\n"
    )


def _password_reset_html(reset_url: str) -> str:
    expire_minutes = settings.password_reset_token_expire_minutes
    safe_url = html.escape(reset_url)
    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "  <body style=\"font-family: -apple-system, Segoe UI, Roboto, sans-serif; color: #1e293b; "
        'max-width: 480px; margin: 0 auto; padding: 24px;">\n'
        '    <h1 style="font-size: 18px; margin-bottom: 16px;">AI Try-On</h1>\n'
        "    <p>We received a request to reset the password for your AI Try-On account.</p>\n"
        "    <p>\n"
        f'      <a href="{safe_url}" style="display: inline-block; background: #4f46e5; color: #ffffff; '
        'padding: 10px 20px; border-radius: 8px; text-decoration: none; font-weight: 600;">\n'
        "        Reset your password\n"
        "      </a>\n"
        "    </p>\n"
        '    <p style="font-size: 13px; color: #64748b;">Or copy and paste this link into your browser:'
        f"<br>{safe_url}</p>\n"
        f'    <p style="font-size: 13px; color: #64748b;">This link will expire in {expire_minutes} minutes '
        "and can only be used once.</p>\n"
        '    <p style="font-size: 13px; color: #64748b;">If you didn\'t request this, you can safely ignore '
        "this email -- your password will not be changed.</p>\n"
        "  </body>\n"
        "</html>\n"
    )


class ConsoleEmailService(EmailService):
    def send_password_reset_email(self, to_email: str, reset_url: str) -> None:
        print(
            "\n----- Password reset email (dev console backend — no real email is sent) -----\n"
            f"To: {to_email}\n"
            f"Subject: {PASSWORD_RESET_SUBJECT}\n\n"
            f"{_password_reset_plain_text(reset_url)}"
            "--------------------------------------------------------------------------------\n"
        )
        # No recipient address, token, or URL here — see module docstring.
        logger.info("Password reset email dispatched via console backend.")


class SmtpEmailService(EmailService):
    """Production email delivery via stdlib smtplib. See module docstring
    for why SMTP specifically, not a vendor SDK.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool,
        from_address: str,
        from_name: str,
    ):
        # Validated here too (not just Settings.check_production_email_provider,
        # which only runs when AITRYON_ENVIRONMENT=production) -- this
        # class must never be usable in a half-configured state
        # regardless of environment, since it's the thing that would
        # otherwise fail confusingly at first send instead of at
        # construction.
        if not host:
            raise ValueError("SmtpEmailService requires a non-empty host (AITRYON_SMTP_HOST).")
        if not from_address:
            raise ValueError("SmtpEmailService requires a non-empty from_address (AITRYON_EMAIL_FROM_ADDRESS).")
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._from_address = from_address
        self._from_name = from_name

    def send_password_reset_email(self, to_email: str, reset_url: str) -> None:
        message = EmailMessage()
        message["Subject"] = PASSWORD_RESET_SUBJECT
        message["From"] = formataddr((self._from_name, self._from_address))
        message["To"] = to_email
        message.set_content(_password_reset_plain_text(reset_url))
        message.add_alternative(_password_reset_html(reset_url), subtype="html")

        try:
            with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
                if self._use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                if self._username:
                    smtp.login(self._username, self._password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            # Exception TYPE only, never str(exc) -- an SMTP server's own
            # error response can echo back request/auth detail, and this
            # must never risk leaking smtp_password, the recipient
            # address, or message content into a log line.
            logger.error("Password reset email delivery failed via SMTP host=%s: %s", self._host, type(exc).__name__)
            raise EmailDeliveryError("Failed to send password reset email.") from exc
