"""add user auth_version for JWT revocation

Revision ID: 1b66823866e3
Revises: d2aad5003996
Create Date: 2026-09-17 20:46:47.024382

Lightweight server-side JWT revocation (session-hardening milestone,
2026-09-17): adds a single per-user version counter rather than a token
blocklist/session table. Every access token embeds the auth_version it was
issued under (auth/security.py's create_access_token); a request is
authenticated only if that embedded version still matches this column's
*current* value (auth/dependencies.py's get_current_user_optional).
Bumping the column therefore revokes every previously issued token for
that user in one write, checked against data a request already has to
load to authenticate at all -- no new table, no per-token bookkeeping, no
extra query. See api/auth.py's reset_password for the one place this is
bumped today (closing the "JWT stays valid after a password reset"
limitation from the password-reset milestone).

`nullable=False` against an existing, possibly non-empty `users` table
needs a value for every current row up front, hence `server_default='1'`
below -- Postgres backfills every existing row with 1 as part of the same
ALTER TABLE, and 1 is also exactly what a freshly created row gets from
the ORM model's own `default=1` (db/models.py's User.auth_version) once
this migration is in place. The server-side default is kept permanently
(not dropped after backfill, unlike some two-step "add with default, then
drop the default" migrations) -- there's no ongoing cost to a integer
default staying on the column, and it means any row ever inserted outside
the ORM (raw SQL, another tool) still gets a sane, non-NULL value rather
than depending on Python-side code to have set it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1b66823866e3'
down_revision: Union[str, Sequence[str], None] = 'd2aad5003996'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('auth_version', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'auth_version')
