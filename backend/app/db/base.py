"""SQLAlchemy engine/session setup.

One engine for the process, one session per request/background job — the
usual sync SQLAlchemy pattern. Nothing above this module should import
`sqlalchemy` directly; go through the session helpers here instead, so the
ORM stays swappable in principle (mirrors every other abstraction in this
codebase — see docs/ARCHITECTURE.md).
"""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# A fixed naming convention so every index/constraint gets a predictable
# name. Without this, Alembic can't reliably autogenerate a migration that
# drops or alters a constraint (it has no name to target) — see the
# "unnamed constraint" warning this fixed, in the Milestone 6 commit.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@contextmanager
def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
