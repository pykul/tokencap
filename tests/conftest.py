"""Shared test fixtures for tokencap."""

from __future__ import annotations

from pathlib import Path

import pytest

from tokencap.backends.sqlite import SQLiteBackend
from tokencap.core.types import BudgetKey


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    """Return a temporary SQLite database path."""
    return str(tmp_path / "test_tokencap.db")


@pytest.fixture
def sqlite_backend(tmp_db: str) -> SQLiteBackend:
    """Create a SQLiteBackend using a temporary database."""
    backend = SQLiteBackend(path=tmp_db)
    yield backend  # type: ignore[misc]  # mypy cannot infer return type of pytest yield fixtures
    backend.close()


@pytest.fixture
def sample_key() -> BudgetKey:
    """Return a sample BudgetKey for tests."""
    return BudgetKey(dimension="session", identifier="test-123")
