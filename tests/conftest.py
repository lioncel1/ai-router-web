"""Prevent Gateway integration tests from touching the live SQLite database.

The application-level Phase 3 chat wrapper writes Task records even when its
model caller is mocked. This automatically routes EVERY pytest test to its own
disposable database. unittest.setUp patches in existing repository tests still
work and restore back to this isolated path.
"""
import pytest

from database import connection


@pytest.fixture(autouse=True)
def use_disposable_database_for_tests(monkeypatch, tmp_path):
    monkeypatch.setattr(connection, "DB_FILE", tmp_path / "ai_router_test.db")
