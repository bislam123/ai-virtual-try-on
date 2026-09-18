"""add user is_admin and is_active for admin operations tooling

Revision ID: e94aa22bee59
Revises: 1b66823866e3
Create Date: 2026-09-18 15:09:32.535416

Admin/Operations milestone: two small, independent boolean flags on
`users`, not a roles table or a new admin_users table -- this app has
exactly one privilege tier above "normal user" today, so a boolean is the
"explicit server-side representation" the milestone brief calls for,
without inventing a roles system nothing else here needs yet.

`is_admin` (default false): gates every /api/admin/* route
(auth/dependencies.py's get_current_admin_user). No signup/login request
can ever set this -- SignupRequest has no such field (models/schemas.py)
and never will; the only way an account becomes admin is a direct,
out-of-band database UPDATE by whoever already has DB access, the same
trust boundary this project already relies on for editing `plans` rows
(see docs/DEVELOPMENT.md's Milestone 11). See backend/scripts/promote_admin.py
for a safer, documented convenience wrapper around that same UPDATE, and
docs/ARCHITECTURE.md's Admin/Operations section for the full bootstrap
procedure. No default admin account is created by this migration or by
any code -- every row gets is_admin=false, with no exception.

`is_active` (default true): lets an admin disable an account without
deleting it (Admin/Operations milestone, section 2). Checked alongside
the existing auth_version comparison in get_current_user_optional, so
disabling an account revokes its live sessions immediately, not just
future logins -- the same fail-closed pattern already established there,
not a second, divergent mechanism.

Both use server_default (not just the ORM model's Python-side default)
for the same reason auth_version's migration does: a nullable=False add
against a possibly non-empty `users` table needs a value for every
existing row up front, and keeping the server-side default permanently
means any row ever inserted outside the ORM still gets a sane, non-NULL
value.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e94aa22bee59'
down_revision: Union[str, Sequence[str], None] = '1b66823866e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'is_active')
    op.drop_column('users', 'is_admin')
