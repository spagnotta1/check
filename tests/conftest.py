"""Shared fixtures: an isolated app + database per test."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import finance_sync.scheduler as scheduler_module
from app import create_app
from models import db


@pytest.fixture(autouse=True)
def _isolate_live_credentials(monkeypatch):
    """Tests must exercise sandbox mode by default regardless of what live
    credentials a developer happens to have in their real .env — app.py loads
    .env at import time, so those vars are already in os.environ otherwise.
    Tests that specifically want live-configured behavior re-set these via
    their own monkeypatch.setenv(...).
    """
    for var in ("PLAID_CLIENT_ID", "PLAID_SECRET", "COINBASE_CLIENT_ID", "COINBASE_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def app(tmp_path):
    """A fresh app bound to a temporary SQLite database."""
    # Each test gets its own scheduler bound to its own app.
    scheduler_module._scheduler = None

    application = create_app(test_config={
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'test.db'}",
        'SYNC_SYNCHRONOUS': True,   # manual-refresh API runs inline for determinism
        'SYNC_AUTO_ENABLED': False,
    })
    with application.app_context():
        yield application
        db.session.remove()
    scheduler_module._scheduler = None


@pytest.fixture()
def client(app):
    return app.test_client()
