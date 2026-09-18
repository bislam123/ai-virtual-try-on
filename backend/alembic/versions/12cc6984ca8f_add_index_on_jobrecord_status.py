"""add index on jobrecord status

Revision ID: 12cc6984ca8f
Revises: e1f5538acd61
Create Date: 2026-09-18 17:32:05.152809

Production-readiness follow-up: `jobs.status` (db/models.py's JobRecord)
had no index despite being filtered on the hot path of every single
try-on submission (`CapacityService.get_status`'s
`WHERE status IN ('pending','processing')`, run before a job row is even
created -- see api/tryon.py) and by both background sweeps
(`job_recovery.py`'s `WHERE status='processing'`,
`cleanup_expired_results.py`'s `WHERE status='completed'`). Nothing in
this application ever deletes a `JobRecord` row (only its result/temp
*files* are cleaned up), so this table only ever grows -- an uncovered
filter here becomes a progressively slower full scan over the
application's lifetime, not a one-time cost.

**Uses `CREATE INDEX CONCURRENTLY`, not a plain `CREATE INDEX`**, and
therefore must run outside this migration's normal transaction --
`op.get_context().autocommit_block()` lifts both operations below into
their own autocommit connection, which is Alembic's own documented
mechanism for this exact situation (see Alembic's "Building an Index
CONCURRENTLY" cookbook recipe). Concurrent index creation does not take
the ACCESS EXCLUSIVE lock a plain `CREATE INDEX` would, so writes to
`jobs` are never blocked while this runs -- worth it here specifically
because, unlike most of this project's earlier migrations, this one may
run against a table that already holds real, growing production data by
the time it's applied. `DROP INDEX CONCURRENTLY` in `downgrade()` for the
same reason, symmetrically.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '12cc6984ca8f'
down_revision: Union[str, Sequence[str], None] = 'e1f5538acd61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.get_context().autocommit_block():
        op.create_index(
            op.f('ix_jobs_status'), 'jobs', ['status'], unique=False, postgresql_concurrently=True
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(op.f('ix_jobs_status'), table_name='jobs', postgresql_concurrently=True)
