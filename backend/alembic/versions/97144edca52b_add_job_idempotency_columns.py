"""add job idempotency columns

Revision ID: 97144edca52b
Revises: 34aed8f22dbb
Create Date: 2026-09-18 10:00:00.000000

Adds idempotency protection to POST /api/try-on (double taps, network
retries, browser retry behavior, mobile connection instability all risk
creating a second, expensive AI job for what the client considers one
request).

A migration is genuinely required here, not avoidable by reusing existing
columns: nothing in the current schema records a client-supplied key at
all, and the required guarantee -- "at most one active job per (scope,
key), even under truly concurrent requests" -- can only be enforced
atomically by the database itself, not by an in-memory dict (which doesn't
survive a restart and doesn't coordinate across multiple worker
processes). Kept minimal on purpose: three nullable columns on the
existing `jobs` table (no new table -- a job's idempotency identity is
inherent to that job, not a separate resource) plus one partial unique
index.

The index is unique on (idempotency_scope, idempotency_key) but only
`WHERE idempotency_key IS NOT NULL AND status <> 'failed'`:
- NULL is excluded so old-style jobs with no Idempotency-Key header (the
  overwhelming majority, and *all* jobs created before this migration)
  never collide with each other -- Postgres unique indexes already treat
  NULL as distinct from every other NULL by default, but the explicit
  predicate documents that this is intentional, not incidental.
- `status <> 'failed'` is excluded so a genuinely failed job frees up its
  (scope, key) pair for a fresh retry automatically: the moment
  update_status() flips a row to 'failed', Postgres drops that row from
  this partial index on the same UPDATE, with no separate cleanup step
  needed. See services/job_store.py's create_idempotent for the
  application-level side of this.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '97144edca52b'
down_revision: Union[str, Sequence[str], None] = '34aed8f22dbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('jobs', sa.Column('idempotency_scope', sa.String(length=64), nullable=True))
    op.add_column('jobs', sa.Column('idempotency_key', sa.String(length=255), nullable=True))
    op.add_column('jobs', sa.Column('idempotency_fingerprint', sa.String(length=64), nullable=True))
    op.create_index(
        'ix_jobs_idempotency_active',
        'jobs',
        ['idempotency_scope', 'idempotency_key'],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL AND status <> 'failed'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_jobs_idempotency_active', table_name='jobs')
    op.drop_column('jobs', 'idempotency_fingerprint')
    op.drop_column('jobs', 'idempotency_key')
    op.drop_column('jobs', 'idempotency_scope')
