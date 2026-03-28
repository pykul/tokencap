"""Tests for tokencap.core.guard.Guard."""

from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock

from tests.conftest import make_action, make_dimension_policy, make_policy, make_threshold
from tokencap.core.guard import Guard
from tokencap.status.api import StatusResponse


class TestGuardConstruction:
    """Tests for Guard.__init__."""

    def test_sets_limits_on_backend(self, mock_backend: MagicMock) -> None:
        """Guard calls backend.set_limit for each dimension."""
        policy = make_policy(dimensions={
            "a": make_dimension_policy(limit=1000),
            "b": make_dimension_policy(limit=2000),
        })
        Guard(policy=policy, identifiers={"a": "id-a", "b": "id-b"},
              backend=mock_backend, quiet=True)
        assert mock_backend.set_limit.call_count == 2

    def test_auto_uuid_for_missing_identifiers(self, mock_backend: MagicMock) -> None:
        """Dimensions without explicit identifiers get auto UUIDs."""
        policy = make_policy(dimensions={
            "session": make_dimension_policy(),
        })
        guard = Guard(policy=policy, backend=mock_backend, quiet=True)
        assert len(guard.identifiers["session"]) == 36  # UUID format

    def test_explicit_identifier_used(self, mock_backend: MagicMock) -> None:
        """Explicit identifiers are used as-is."""
        policy = make_policy(dimensions={"session": make_dimension_policy()})
        guard = Guard(policy=policy, identifiers={"session": "my-id"},
                      backend=mock_backend, quiet=True)
        assert guard.identifiers["session"] == "my-id"

    def test_startup_message_printed(self, mock_backend: MagicMock) -> None:
        """Guard prints startup message to stdout."""
        policy = make_policy(dimensions={"session": make_dimension_policy(limit=5000)})
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            Guard(policy=policy, identifiers={"session": "test-id"},
                  backend=mock_backend)
        finally:
            sys.stdout = old_stdout
        output = buf.getvalue()
        assert "[tokencap]" in output
        assert "session=test-id" in output

    def test_quiet_suppresses_message(self, mock_backend: MagicMock) -> None:
        """quiet=True suppresses stdout."""
        policy = make_policy(dimensions={"session": make_dimension_policy()})
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            Guard(policy=policy, backend=mock_backend, quiet=True)
        finally:
            sys.stdout = old_stdout
        assert buf.getvalue() == ""

    def test_default_backend_is_sqlite(self) -> None:
        """backend=None creates SQLiteBackend."""
        from tokencap.backends.sqlite import SQLiteBackend
        policy = make_policy(dimensions={"session": make_dimension_policy()})
        guard = Guard(policy=policy, quiet=True)
        assert isinstance(guard.backend, SQLiteBackend)
        guard.teardown()

    def test_startup_message_no_limit(self, mock_backend: MagicMock) -> None:
        """No thresholds shows '(no limit set)'."""
        policy = make_policy(dimensions={
            "session": make_dimension_policy(limit=0),
        })
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            Guard(policy=policy, identifiers={"session": "test-id"},
                  backend=mock_backend)
        finally:
            sys.stdout = old_stdout
        assert "(no limit set)" in buf.getvalue()

    def test_startup_message_with_limit(self, mock_backend: MagicMock) -> None:
        """Thresholds present shows 'limit=N tokens'."""
        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=5000,
                thresholds=[make_threshold(at_pct=1.0, actions=[make_action(kind="BLOCK")])],
            ),
        })
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            Guard(policy=policy, identifiers={"session": "test-id"},
                  backend=mock_backend)
        finally:
            sys.stdout = old_stdout
        assert "limit=5000 tokens" in buf.getvalue()


class TestGuardWrap:
    """Tests for wrap_anthropic and wrap_openai."""

    def test_wrap_anthropic_returns_guarded(self, mock_backend: MagicMock) -> None:
        """wrap_anthropic returns a GuardedAnthropic."""
        import anthropic

        from tokencap.interceptor.anthropic import GuardedAnthropic

        policy = make_policy(dimensions={"session": make_dimension_policy()})
        guard = Guard(policy=policy, backend=mock_backend, quiet=True)
        client = anthropic.Anthropic(api_key="sk-fake")
        wrapped = guard.wrap_anthropic(client)
        assert isinstance(wrapped, GuardedAnthropic)

    def test_wrap_openai_returns_guarded(self, mock_backend: MagicMock) -> None:
        """wrap_openai returns a GuardedOpenAI."""
        import openai

        from tokencap.interceptor.openai import GuardedOpenAI

        policy = make_policy(dimensions={"session": make_dimension_policy()})
        guard = Guard(policy=policy, backend=mock_backend, quiet=True)
        client = openai.OpenAI(api_key="sk-fake")
        wrapped = guard.wrap_openai(client)
        assert isinstance(wrapped, GuardedOpenAI)


class TestGuardStatus:
    """Tests for get_status."""

    def test_get_status_returns_status_response(self, mock_backend: MagicMock) -> None:
        """get_status returns a StatusResponse."""
        policy = make_policy(dimensions={"session": make_dimension_policy()})
        guard = Guard(policy=policy, identifiers={"session": "test-id"},
                      backend=mock_backend, quiet=True)
        status = guard.get_status()
        assert isinstance(status, StatusResponse)
        assert status.active_policy == "test"
        assert "session" in status.dimensions


class TestGuardTeardown:
    """Tests for teardown."""

    def test_teardown_closes_backend(self, mock_backend: MagicMock) -> None:
        """teardown calls close() on the backend."""
        policy = make_policy(dimensions={"session": make_dimension_policy()})
        guard = Guard(policy=policy, backend=mock_backend, quiet=True)
        guard.teardown()
        mock_backend.close.assert_called_once()
