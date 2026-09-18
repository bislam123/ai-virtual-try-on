"""Tests for GET /health (backend/app/main.py) -- added during the
production-deployment-preparation milestone alongside a real database
reachability check (a genuine `SELECT 1`, not just "the process is
running").

Wires the real `health` route handler onto a fresh, lifespan-free FastAPI
test app rather than importing the real `app` object from main.py -- same
reasoning as test_security_headers.py's own docstring: the real app's
lifespan() loads the actual ~2GB AI model, far too slow/heavy for a unit
test, and unnecessary here since importing the module (as opposed to
starting it) never triggers lifespan() at all. This exercises the actual
production `health` function, not a duplicate of its logic.
"""

from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

import backend.app.main as main_module
from backend.app.db import engine
from backend.app.main import health


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_api_route("/health", health, methods=["GET"])
    return app


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


@pytest.mark.skipif(
    not _db_reachable(), reason="Postgres isn't reachable at settings.database_url — see docs/DEVELOPMENT.md"
)
def test_health_reports_ok_when_database_is_reachable():
    client = TestClient(_make_app())

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_reports_503_when_database_is_unreachable(monkeypatch):
    """No real Postgres outage needed -- monkeypatches main.py's own
    get_session reference (not the shared engine/SessionLocal, which
    other tests may depend on) so only this test's /health call sees a
    broken database."""

    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    @contextmanager
    def _broken_get_session():
        yield _BrokenSession()

    monkeypatch.setattr(main_module, "get_session", _broken_get_session)
    client = TestClient(_make_app())

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json() == {"status": "error", "detail": "Database unreachable."}
