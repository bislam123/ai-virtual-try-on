#!/usr/bin/env python3
"""Promotes an existing account to admin -- the documented, safe way to
bootstrap the first (or any later) admin account.

Deliberately does NOT create an account, set a password, or accept any
credential -- it only flips `users.is_admin` to true for an email that
has ALREADY signed up through the normal POST /api/auth/signup flow.
There is no other way to become an admin anywhere in this codebase: no
request schema anywhere accepts an is_admin field (see models/schemas.py),
and no endpoint sets it. This script is a thin, safer convenience wrapper
around the one thing that actually grants admin -- a direct database
UPDATE -- for whoever already has the database access needed to run it
(the exact same trust boundary this project already relies on for editing
`plans` rows by hand, see docs/DEVELOPMENT.md's Milestone 11).

No default admin account is created by this script, by the migration
that adds is_admin, or by anything else in this codebase. Every account
starts, and stays, non-admin until an operator runs this deliberately.

Usage (from the repo root, same venv as the rest of the backend):
    ai\\.venv\\Scripts\\python.exe backend\\scripts\\promote_admin.py you@example.com

Exit code 0 on success (including "already an admin, nothing to do"), 1
if no account exists for that email yet.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.db import User, get_session  # noqa: E402


def promote_to_admin(email: str) -> int:
    normalized = email.strip().lower()
    with get_session() as session:
        user = session.query(User).filter(User.email == normalized).first()
        if user is None:
            print(
                f"No account found for {normalized!r}. The account must already exist -- "
                "sign up through the normal app / POST /api/auth/signup flow first, then run this again.",
                file=sys.stderr,
            )
            return 1
        if user.is_admin:
            print(f"{normalized} is already an admin. Nothing to do.")
            return 0
        user.is_admin = True
        print(f"{normalized} (user id {user.id}) is now an admin.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote an existing account to admin.")
    parser.add_argument("email", help="Email of an existing, already-signed-up account.")
    args = parser.parse_args()
    return promote_to_admin(args.email)


if __name__ == "__main__":
    sys.exit(main())
