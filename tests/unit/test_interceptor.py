"""Tests for tokencap.interceptor.base and wrapper classes."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_action, make_dimension_policy, make_policy, make_threshold
from tokencap.core.exceptions import BudgetExceededError
from tokencap.core.guard import Guard
from tokencap.core.types import BudgetKey, BudgetState, CheckResult, TokenUsage
from tokencap.interceptor.base import (
    GuardedStream,
    _evaluate_thresholds,
    call,
)

# ---------------------------------------------------------------------------
# call() tests
# ---------------------------------------------------------------------------


class TestCall:
    """Tests for the sync call() function."""

    def test_call_allowed(
        self, stub_guard: Guard, mock_backend: MagicMock
    ) -> None:
        """Backend returns allowed=True: real_fn called, response returned."""
        real_fn = MagicMock(return_value="response")
        result = call(real_fn, {"model": "test"}, stub_guard)
        assert result == "response"
        real_fn.assert_called_once()

    def test_call_blocked_by_check(
        self, stub_guard: Guard, mock_backend: MagicMock
    ) -> None:
        """Backend returns allowed=False: BudgetExceededError, real_fn never called."""
        mock_backend.check_and_increment.return_value = CheckResult(
            allowed=False,
            states={"session": BudgetState(
                key=BudgetKey("session", "test-id"),
                limit=100, used=100, remaining=0, pct_used=1.0, cost_usd=0.0,
            )},
            violated=["session"],
        )
        real_fn = MagicMock()
        with pytest.raises(BudgetExceededError):
            call(real_fn, {"model": "test"}, stub_guard)
        real_fn.assert_not_called()

    def test_call_reconciliation(
        self, stub_guard: Guard, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """When actual > estimated, force_increment is called with the delta."""
        mock_provider.estimate_tokens.return_value = 50
        mock_provider.extract_usage.return_value = TokenUsage(
            input_tokens=60, output_tokens=60
        )
        real_fn = MagicMock(return_value="response")
        call(real_fn, {"model": "test"}, stub_guard)
        # delta = 120 - 50 = 70
        mock_backend.force_increment.assert_called_once()
        args = mock_backend.force_increment.call_args
        assert args[0][1] == 70

    def test_call_no_reconciliation(
        self, stub_guard: Guard, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """When actual <= estimated, force_increment is NOT called."""
        mock_provider.estimate_tokens.return_value = 200
        mock_provider.extract_usage.return_value = TokenUsage(
            input_tokens=50, output_tokens=50
        )
        real_fn = MagicMock(return_value="response")
        call(real_fn, {"model": "test"}, stub_guard)
        mock_backend.force_increment.assert_not_called()

    def test_call_kwargs_not_mutated(self, stub_guard: Guard) -> None:
        """Original kwargs dict is unchanged after call."""
        original = {"model": "test", "messages": []}
        original_copy = dict(original)
        real_fn = MagicMock(return_value="response")
        call(real_fn, original, stub_guard)
        assert original == original_copy


# ---------------------------------------------------------------------------
# _evaluate_thresholds() tests
# ---------------------------------------------------------------------------


class TestEvaluateThresholds:
    """Tests for _evaluate_thresholds."""

    def _make_guard(
        self,
        policy: MagicMock | object,
        mock_backend: MagicMock,
        mock_provider: MagicMock,
    ) -> Guard:
        """Create a Guard with the given policy."""
        return Guard(
            policy=policy,  # type: ignore[arg-type]
            backend=mock_backend,
            provider=mock_provider,
            identifiers={"session": "test-id"},
        )

    def test_warn_fires_callback(
        self, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """WARN threshold crossed: callback invoked."""
        callback = MagicMock()
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=0.5, actions=[
                make_action(kind="WARN", callback=callback),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend, mock_provider)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=600, remaining=400, pct_used=0.6, cost_usd=0.0
        )
        _evaluate_thresholds(guard, [key], {"session": state}, {})
        callback.assert_called_once()

    def test_warn_fire_once(
        self, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """Same WARN threshold crossed twice: callback fires only once."""
        callback = MagicMock()
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=0.5, actions=[
                make_action(kind="WARN", callback=callback),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend, mock_provider)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=600, remaining=400, pct_used=0.6, cost_usd=0.0
        )
        _evaluate_thresholds(guard, [key], {"session": state}, {})
        mock_backend.is_threshold_fired.return_value = True
        _evaluate_thresholds(guard, [key], {"session": state}, {})
        callback.assert_called_once()

    def test_block_raises(
        self, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """BLOCK threshold crossed: BudgetExceededError raised."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=1.0, actions=[
                make_action(kind="BLOCK"),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend, mock_provider)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=1000, remaining=0, pct_used=1.0, cost_usd=0.0
        )
        with pytest.raises(BudgetExceededError) as exc_info:
            _evaluate_thresholds(guard, [key], {"session": state}, {})
        assert "session" in exc_info.value.check_result.violated

    def test_block_refires_every_call(
        self, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """BLOCK threshold raises on every call, not just the first (D-037)."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=1.0, actions=[
                make_action(kind="BLOCK"),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend, mock_provider)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=1000, remaining=0, pct_used=1.0, cost_usd=0.0
        )
        with pytest.raises(BudgetExceededError):
            _evaluate_thresholds(guard, [key], {"session": state}, {})
        with pytest.raises(BudgetExceededError):
            _evaluate_thresholds(guard, [key], {"session": state}, {})
        mock_backend.is_threshold_fired.assert_not_called()
        mock_backend.mark_threshold_fired.assert_not_called()

    def test_block_fires_warn_first(
        self, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """Threshold with WARN + BLOCK: callback fires, then raises."""
        callback = MagicMock()
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=1.0, actions=[
                make_action(kind="WARN", callback=callback),
                make_action(kind="BLOCK"),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend, mock_provider)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=1000, remaining=0, pct_used=1.0, cost_usd=0.0
        )
        with pytest.raises(BudgetExceededError):
            _evaluate_thresholds(guard, [key], {"session": state}, {})
        callback.assert_called_once()

    def test_degrade_swaps_model(
        self, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """DEGRADE: call_kwargs has new model, original kwargs unchanged."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=0.5, actions=[
                make_action(kind="DEGRADE", degrade_to="cheap-model"),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend, mock_provider)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=600, remaining=400, pct_used=0.6, cost_usd=0.0
        )
        original = {"model": "expensive-model"}
        result = _evaluate_thresholds(guard, [key], {"session": state}, original)
        assert result["model"] == "cheap-model"
        assert original["model"] == "expensive-model"

    def test_degrade_skipped_with_block(
        self, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """Threshold with BLOCK + DEGRADE: raises, no model swap."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=1.0, actions=[
                make_action(kind="DEGRADE", degrade_to="cheap-model"),
                make_action(kind="BLOCK"),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend, mock_provider)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=1000, remaining=0, pct_used=1.0, cost_usd=0.0
        )
        with pytest.raises(BudgetExceededError):
            _evaluate_thresholds(guard, [key], {"session": state}, {"model": "x"})

    def test_webhook_fires_in_thread(
        self, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """WEBHOOK: thread started."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=0.5, actions=[
                make_action(kind="WEBHOOK", webhook_url="http://example.com/hook"),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend, mock_provider)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=600, remaining=400, pct_used=0.6, cost_usd=0.0
        )
        with patch.object(threading.Thread, "start") as mock_start:
            _evaluate_thresholds(guard, [key], {"session": state}, {})
            mock_start.assert_called_once()


# ---------------------------------------------------------------------------
# GuardedStream tests
# ---------------------------------------------------------------------------


class TestGuardedStream:
    """Tests for GuardedStream."""

    def test_stream_normal_exit(
        self, stub_guard: Guard, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """Normal exit: usage extracted, delta reconciled."""
        mock_provider.estimate_tokens.return_value = 50
        mock_provider.extract_usage.return_value = TokenUsage(
            input_tokens=60, output_tokens=60
        )
        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__enter__ = MagicMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__exit__ = MagicMock(return_value=False)
        real_fn = MagicMock(return_value=mock_stream_ctx)

        key = BudgetKey("session", "test-id")
        gs = GuardedStream(
            real_fn=real_fn,
            call_kwargs={"model": "test"},
            estimated=50,
            keys=[key],
            original_model="test",
            guard=stub_guard,
        )
        with gs:
            pass
        mock_backend.force_increment.assert_called_once()

    def test_stream_early_exit(
        self, stub_guard: Guard, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """Early exit: no usage, WARNING logged, estimate stands."""
        mock_provider.extract_usage.return_value = TokenUsage(
            input_tokens=0, output_tokens=0
        )
        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__enter__ = MagicMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__exit__ = MagicMock(return_value=False)
        real_fn = MagicMock(return_value=mock_stream_ctx)

        key = BudgetKey("session", "test-id")
        gs = GuardedStream(
            real_fn=real_fn,
            call_kwargs={"model": "test"},
            estimated=100,
            keys=[key],
            original_model="test",
            guard=stub_guard,
        )
        with gs:
            pass
        mock_backend.force_increment.assert_not_called()
        mock_backend.get_states.assert_called()


# ---------------------------------------------------------------------------
# GuardedAnthropic / GuardedOpenAI wrapper tests
# ---------------------------------------------------------------------------


class TestGuardedAnthropic:
    """Tests for GuardedAnthropic (mocked anthropic SDK)."""

    def test_messages_property_returns_guarded(self, stub_guard: Guard) -> None:
        """The .messages property returns a GuardedMessages, not raw SDK."""
        from tokencap.interceptor.anthropic import GuardedAnthropic, GuardedMessages

        mock_client = MagicMock()
        mock_client.__class__ = type("Anthropic", (), {})
        guarded = GuardedAnthropic(mock_client, stub_guard)
        assert isinstance(guarded.messages, GuardedMessages)

    def test_getattr_passthrough(self, stub_guard: Guard) -> None:
        """Attributes not intercepted delegate to the real client."""
        from tokencap.interceptor.anthropic import GuardedAnthropic

        mock_client = MagicMock()
        mock_client.__class__ = type("Anthropic", (), {})
        mock_client.api_key = "sk-test"
        guarded = GuardedAnthropic(mock_client, stub_guard)
        assert guarded.api_key == "sk-test"

    def test_with_options_returns_guarded(self, stub_guard: Guard) -> None:
        """with_options() returns a new GuardedAnthropic."""
        from tokencap.interceptor.anthropic import GuardedAnthropic

        mock_client = MagicMock()
        mock_client.__class__ = type("Anthropic", (), {})
        mock_client.with_options.return_value = mock_client
        guarded = GuardedAnthropic(mock_client, stub_guard)
        result = guarded.with_options(timeout=30)
        assert isinstance(result, GuardedAnthropic)


class TestGuardedOpenAI:
    """Tests for GuardedOpenAI (mocked openai SDK)."""

    def test_chat_property_returns_guarded(self, stub_guard: Guard) -> None:
        """The .chat property returns a GuardedChat."""
        from tokencap.interceptor.openai import GuardedChat, GuardedOpenAI

        mock_client = MagicMock()
        mock_client.__class__ = type("OpenAI", (), {})
        guarded = GuardedOpenAI(mock_client, stub_guard)
        assert isinstance(guarded.chat, GuardedChat)

    def test_completions_property(self, stub_guard: Guard) -> None:
        """The .chat.completions property returns a GuardedCompletions."""
        from tokencap.interceptor.openai import GuardedCompletions, GuardedOpenAI

        mock_client = MagicMock()
        mock_client.__class__ = type("OpenAI", (), {})
        guarded = GuardedOpenAI(mock_client, stub_guard)
        assert isinstance(guarded.chat.completions, GuardedCompletions)

    def test_stream_injects_options(self, stub_guard: Guard) -> None:
        """stream=True injects stream_options in a copy, not the original."""
        from tokencap.interceptor.openai import GuardedCompletions

        mock_completions = MagicMock()
        mock_completions.__class__ = type("Completions", (), {})
        gc = GuardedCompletions(
            mock_completions, stub_guard, is_async=False
        )
        original_kwargs = {"model": "gpt-4o", "stream": True, "messages": []}
        original_copy = dict(original_kwargs)
        result = gc.create(**original_kwargs)
        assert original_kwargs == original_copy
        assert isinstance(result, GuardedStream)
