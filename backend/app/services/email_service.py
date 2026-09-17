"""Password-reset email delivery abstraction.

`ConsoleEmailService` is the only implementation today: instead of a real
send, it prints the message to stdout — exactly how Django's "console"
email backend works for local development. It exists purely so a developer
testing the forgot-password flow locally can read the reset link straight
out of the terminal running the server.

This is a real, load-bearing limitation, not an oversight: sending actual
email needs a transactional-email provider or SMTP relay, which is
genuinely new infrastructure this project doesn't have (no SMTP server, no
provider account, no credentials) — exactly the "unrelated infrastructure"
this milestone was scoped to avoid adding speculatively. A production
deployment swaps in a real implementation of this same interface (SMTP via
stdlib smtplib, or a provider's HTTP API) the same way providers/selfhosted.py's
VirtualTryOnProvider gets swapped for a different backend — api/auth.py's
route handler never has to change.

Deliberately prints rather than going through `logging`: api/auth.py's
forgot-password endpoint must never put the raw reset token/URL into the
application's structured logs (a log line here is exactly that channel).
Printing directly to the console this dev server happens to be running in
is not the same exposure — it's the intended, temporary stand-in for an
inbox only the developer running that server can see.
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class EmailService(ABC):
    @abstractmethod
    def send_password_reset_email(self, to_email: str, reset_url: str) -> None: ...


class ConsoleEmailService(EmailService):
    def send_password_reset_email(self, to_email: str, reset_url: str) -> None:
        print(
            "\n----- Password reset email (dev console backend — no real email is sent) -----\n"
            f"To: {to_email}\n"
            "Subject: Reset your AI Try-On password\n\n"
            f"Use this link to reset your password: {reset_url}\n"
            f"This link expires soon and can only be used once.\n"
            "If you didn't request this, you can safely ignore this email.\n"
            "--------------------------------------------------------------------------------\n"
        )
        # No recipient address, token, or URL here — see module docstring.
        logger.info("Password reset email dispatched via console backend.")
