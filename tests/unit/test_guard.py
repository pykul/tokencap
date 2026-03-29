"""Tests for tokencap.core.guard.Guard."""

from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock

from tests.conftest import make_action, make_dimension_policy, make_policy, make_threshold
from tokencap.core.enums import ActionKind
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
                thresholds=[make_threshold(
                    at_pct=1.0, actions=[make_action(kind=ActionKind.BLOCK)],
                )],
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


class TestWrapLimitEquivalence:
    """Verify wrap(limit=N) and init(policy=...)+wrap() produce identical state."""

    def test_wrap_limit_identical_to_init_wrap(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """wrap(client, limit=N) produces same Guard state as init(policy)+wrap()."""
        import anthropic

        import tokencap

        # Path 1: wrap(client, limit=N)
        tokencap.wrap(
            anthropic.Anthropic(api_key="sk-fake"), limit=50_000, quiet=True
        )
        guard_a = tokencap._guard
        assert guard_a is not None
        policy_a = guard_a.policy
        ident_a = guard_a.identifiers
        tokencap.teardown()

        # Path 2: init(policy=...) + wrap(client)
        tokencap.init(
            policy=tokencap.Policy(
                dimensions={
                    "session": tokencap.DimensionPolicy(
                        limit=50_000,
                        thresholds=[
                            tokencap.Threshold(
                                at_pct=1.0,
                                actions=[tokencap.Action(kind=tokencap.ActionKind.BLOCK)],
                            ),
                        ],
                    ),
                }
            ),
            quiet=True,
        )
        tokencap.wrap(anthropic.Anthropic(api_key="sk-fake"))
        guard_b = tokencap._guard
        assert guard_b is not None
        policy_b = guard_b.policy
        ident_b = guard_b.identifiers
        tokencap.teardown()

        # Compare structure
        assert set(policy_a.dimensions.keys()) == set(policy_b.dimensions.keys())
        for dim in policy_a.dimensions:
            dp_a = policy_a.dimensions[dim]
            dp_b = policy_b.dimensions[dim]
            assert dp_a.limit == dp_b.limit
            assert len(dp_a.thresholds) == len(dp_b.thresholds)
            for t_a, t_b in zip(dp_a.thresholds, dp_b.thresholds):
                assert t_a.at_pct == t_b.at_pct
                assert [a.kind for a in t_a.actions] == [a.kind for a in t_b.actions]
        assert set(ident_a.keys()) == set(ident_b.keys())
        # UUIDs differ but both have "session" dimension
        assert "session" in ident_a
        assert "session" in ident_b
