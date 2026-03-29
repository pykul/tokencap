"""Tests for tokencap.interceptor.base and wrapper classes."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_action, make_dimension_policy, make_policy, make_threshold
from tokencap.core.enums import ActionKind
from tokencap.core.exceptions import BudgetExceededError
from tokencap.core.guard import Guard
from tokencap.core.policy import Policy
from tokencap.core.types import BudgetKey, BudgetState, CheckResult, TokenUsage
from tokencap.interceptor.base import (
    GuardedStream,
    _evaluate_thresholds,
    call,
    call_async,
    call_stream,
)

# ---------------------------------------------------------------------------
# call() tests
# ---------------------------------------------------------------------------


class TestCall:
    """Tests for the sync call() function."""

    def test_call_allowed(
        self, stub_guard: Guard, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """Backend returns allowed=True: real_fn called, response returned."""
        real_fn = MagicMock(return_value="response")
        result = call(real_fn, {"model": "test"}, stub_guard, mock_provider)
        assert result == "response"
        real_fn.assert_called_once()

    def test_call_blocked_by_check(
        self, stub_guard: Guard, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """Backend returns allowed=False: BudgetExceededError, real_fn never called."""
        mock_backend.check_and_increment.return_value = CheckResult(
            allowed=False,
            states={"session": BudgetState(
                key=BudgetKey("session", "test-id"),
                limit=100, used=100, remaining=0, pct_used=1.0,
            )},
            violated=["session"],
        )
        real_fn = MagicMock()
        with pytest.raises(BudgetExceededError):
            call(real_fn, {"model": "test"}, stub_guard, mock_provider)
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
        call(real_fn, {"model": "test"}, stub_guard, mock_provider)
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
        call(real_fn, {"model": "test"}, stub_guard, mock_provider)
        mock_backend.force_increment.assert_not_called()

    def test_call_kwargs_not_mutated(
        self, stub_guard: Guard, mock_provider: MagicMock
    ) -> None:
        """Original kwargs dict is unchanged after call."""
        original = {"model": "test", "messages": []}
        original_copy = dict(original)
        real_fn = MagicMock(return_value="response")
        call(real_fn, original, stub_guard, mock_provider)
        assert original == original_copy

    def test_call_backend_error_wrapped(
        self, stub_guard: Guard, mock_backend: MagicMock, mock_provider: MagicMock
    ) -> None:
        """Backend RuntimeError is wrapped as BackendError."""
        from tokencap.core.exceptions import BackendError

        mock_backend.check_and_increment.side_effect = RuntimeError("disk full")
        real_fn = MagicMock()
        with pytest.raises(BackendError, match="check_and_increment failed"):
            call(real_fn, {"model": "test"}, stub_guard, mock_provider)
        real_fn.assert_not_called()

    def test_call_stream_blocks_when_budget_exceeded(
        self,
        stub_guard: Guard,
        mock_backend: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """call_stream() raises BudgetExceededError before the provider is called."""
        key = BudgetKey("session", "test-id")
        state = BudgetState(key=key, limit=1, used=1, remaining=0, pct_used=1.0)
        mock_backend.check_and_increment.return_value = CheckResult(
            allowed=False, states={"session": state}, violated=["session"]
        )
        real_fn = MagicMock()
        with pytest.raises(BudgetExceededError):
            call_stream(real_fn, {"model": "test"}, stub_guard, mock_provider)
        real_fn.assert_not_called()


# ---------------------------------------------------------------------------
# call_async() tests
# ---------------------------------------------------------------------------


class TestCallAsync:
    """Tests for the async call path."""

    @pytest.mark.asyncio
    async def test_call_async_tracks_tokens(
        self,
        stub_guard: Guard,
        mock_backend: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """call_async() calls force_increment with the usage delta."""
        mock_provider.extract_usage.return_value = TokenUsage(
            input_tokens=80, output_tokens=40,
        )
        mock_provider.estimate_tokens.return_value = 50

        async def fake_fn(**kwargs: object) -> str:
            return "response"

        response = await call_async(
            fake_fn, {"model": "test"}, stub_guard, mock_provider,
        )
        assert response == "response"
        # actual.total=120, estimated=50, delta=70
        mock_backend.force_increment.assert_called_once()
        delta = mock_backend.force_increment.call_args[0][1]
        assert delta == 70

    @pytest.mark.asyncio
    async def test_call_async_blocks_when_budget_exceeded(
        self,
        stub_guard: Guard,
        mock_backend: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """call_async() raises BudgetExceededError before awaiting the provider."""
        key = BudgetKey("session", "test-id")
        state = BudgetState(key=key, limit=1, used=1, remaining=0, pct_used=1.0)
        mock_backend.check_and_increment.return_value = CheckResult(
            allowed=False, states={"session": state}, violated=["session"]
        )
        called = False

        async def fake_fn(**kwargs: object) -> str:
            nonlocal called
            called = True
            return "should not reach"

        with pytest.raises(BudgetExceededError):
            await call_async(
                fake_fn, {"model": "test"}, stub_guard, mock_provider,
            )
        assert not called

    @pytest.mark.asyncio
    async def test_call_async_evaluates_thresholds(
        self,
        mock_backend: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """call_async() fires WARN callback when threshold is crossed."""
        warned: list[object] = []

        def on_warn(status: object) -> None:
            warned.append(status)

        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=100,
                thresholds=[make_threshold(at_pct=0.5, actions=[
                    make_action(kind=ActionKind.WARN, callback=on_warn),
                ])],
            ),
        })
        guard = Guard(
            policy=policy,
            identifiers={"session": "test-id"},
            backend=mock_backend,
            quiet=True,
        )
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=100, used=60, remaining=40, pct_used=0.6,
        )
        mock_backend.check_and_increment.return_value = CheckResult(
            allowed=True, states={"session": state}, violated=[],
        )
        mock_provider.extract_usage.return_value = TokenUsage(
            input_tokens=30, output_tokens=30,
        )
        mock_provider.estimate_tokens.return_value = 50

        async def fake_fn(**kwargs: object) -> str:
            return "ok"

        await call_async(fake_fn, {"model": "test"}, guard, mock_provider)
        assert len(warned) == 1

    @pytest.mark.asyncio
    async def test_call_async_wraps_backend_error(
        self,
        stub_guard: Guard,
        mock_backend: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """Backend RuntimeError is wrapped as BackendError in async path."""
        from tokencap.core.exceptions import BackendError

        mock_backend.check_and_increment.side_effect = RuntimeError("boom")

        async def fake_fn(**kwargs: object) -> str:
            return "should not reach"

        with pytest.raises(BackendError, match="check_and_increment failed"):
            await call_async(
                fake_fn, {"model": "test"}, stub_guard, mock_provider,
            )

    @pytest.mark.asyncio
    async def test_call_async_reconciles_after_call(
        self,
        stub_guard: Guard,
        mock_backend: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """call_async() reconciles actual vs estimated usage."""
        mock_provider.estimate_tokens.return_value = 200
        mock_provider.extract_usage.return_value = TokenUsage(
            input_tokens=100, output_tokens=50,
        )

        async def fake_fn(**kwargs: object) -> str:
            return "ok"

        await call_async(
            fake_fn, {"model": "test"}, stub_guard, mock_provider,
        )
        # actual.total=150, estimated=200, delta=-50 => no force_increment
        mock_backend.force_increment.assert_not_called()


# ---------------------------------------------------------------------------
# _evaluate_thresholds() tests
# ---------------------------------------------------------------------------


class TestEvaluateThresholds:
    """Tests for _evaluate_thresholds."""

    def _make_guard(
        self,
        policy: Policy,
        mock_backend: MagicMock,
    ) -> Guard:
        """Create a Guard with the given policy."""
        return Guard(
            policy=policy,
            identifiers={"session": "test-id"},
            backend=mock_backend,
            quiet=True,
        )

    def test_warn_fires_callback(
        self, mock_backend: MagicMock
    ) -> None:
        """WARN threshold crossed: callback invoked."""
        callback = MagicMock()
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=0.5, actions=[
                make_action(kind=ActionKind.WARN, callback=callback),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=600, remaining=400, pct_used=0.6        )
        _evaluate_thresholds(guard, [key], {"session": state}, {})
        callback.assert_called_once()

    def test_warn_fire_once(
        self, mock_backend: MagicMock
    ) -> None:
        """Same WARN threshold crossed twice: callback fires only once."""
        callback = MagicMock()
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=0.5, actions=[
                make_action(kind=ActionKind.WARN, callback=callback),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=600, remaining=400, pct_used=0.6        )
        _evaluate_thresholds(guard, [key], {"session": state}, {})
        mock_backend.is_threshold_fired.return_value = True
        _evaluate_thresholds(guard, [key], {"session": state}, {})
        callback.assert_called_once()

    def test_block_raises(
        self, mock_backend: MagicMock
    ) -> None:
        """BLOCK threshold crossed: BudgetExceededError raised."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=1.0, actions=[
                make_action(kind=ActionKind.BLOCK),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=1000, remaining=0, pct_used=1.0        )
        with pytest.raises(BudgetExceededError) as exc_info:
            _evaluate_thresholds(guard, [key], {"session": state}, {})
        assert "session" in exc_info.value.check_result.violated

    def test_block_refires_every_call(
        self, mock_backend: MagicMock
    ) -> None:
        """BLOCK threshold raises on every call, not just the first (D-037)."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=1.0, actions=[
                make_action(kind=ActionKind.BLOCK),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=1000, remaining=0, pct_used=1.0        )
        with pytest.raises(BudgetExceededError):
            _evaluate_thresholds(guard, [key], {"session": state}, {})
        with pytest.raises(BudgetExceededError):
            _evaluate_thresholds(guard, [key], {"session": state}, {})
        mock_backend.is_threshold_fired.assert_not_called()
        mock_backend.mark_threshold_fired.assert_not_called()

    def test_block_fires_warn_first(
        self, mock_backend: MagicMock
    ) -> None:
        """Threshold with WARN + BLOCK: callback fires, then raises."""
        callback = MagicMock()
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=1.0, actions=[
                make_action(kind=ActionKind.WARN, callback=callback),
                make_action(kind=ActionKind.BLOCK),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=1000, remaining=0, pct_used=1.0        )
        with pytest.raises(BudgetExceededError):
            _evaluate_thresholds(guard, [key], {"session": state}, {})
        callback.assert_called_once()

    def test_degrade_swaps_model(
        self, mock_backend: MagicMock
    ) -> None:
        """DEGRADE: call_kwargs has new model, original kwargs unchanged."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=0.5, actions=[
                make_action(kind=ActionKind.DEGRADE, degrade_to="cheap-model"),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=600, remaining=400, pct_used=0.6        )
        original = {"model": "expensive-model"}
        result = _evaluate_thresholds(guard, [key], {"session": state}, original)
        assert result["model"] == "cheap-model"
        assert original["model"] == "expensive-model"

    def test_degrade_skipped_with_block(
        self, mock_backend: MagicMock
    ) -> None:
        """Threshold with BLOCK + DEGRADE: raises, no model swap."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=1.0, actions=[
                make_action(kind=ActionKind.DEGRADE, degrade_to="cheap-model"),
                make_action(kind=ActionKind.BLOCK),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=1000, remaining=0, pct_used=1.0        )
        with pytest.raises(BudgetExceededError):
            _evaluate_thresholds(guard, [key], {"session": state}, {"model": "x"})

    def test_webhook_fires_in_thread(
        self, mock_backend: MagicMock
    ) -> None:
        """WEBHOOK: thread started."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=0.5, actions=[
                make_action(kind=ActionKind.WEBHOOK, webhook_url="http://example.com/hook"),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=600, remaining=400, pct_used=0.6        )
        with patch.object(threading.Thread, "start") as mock_start:
            _evaluate_thresholds(guard, [key], {"session": state}, {})
            mock_start.assert_called_once()

    def test_warn_callback_receives_status_response(
        self, mock_backend: MagicMock
    ) -> None:
        """WARN callback receives a StatusResponse with correct fields."""
        from tokencap.status.api import StatusResponse

        captured: list[object] = []

        def on_warn(status: object) -> None:
            captured.append(status)

        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=0.5, actions=[
                make_action(kind=ActionKind.WARN, callback=on_warn),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=600, remaining=400, pct_used=0.6,
        )
        _evaluate_thresholds(guard, [key], {"session": state}, {})
        assert len(captured) == 1
        status = captured[0]
        assert isinstance(status, StatusResponse)
        assert status.timestamp
        assert isinstance(status.dimensions, dict)
        assert isinstance(status.active_policy, str)
        assert status.active_policy

    def test_webhook_invalid_scheme_skipped(
        self, mock_backend: MagicMock
    ) -> None:
        """WEBHOOK: file:// URL logs WARNING and does not call urlopen."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[make_threshold(at_pct=0.5, actions=[
                make_action(kind=ActionKind.WEBHOOK, webhook_url="file:///etc/passwd"),
            ])],
        )})
        guard = self._make_guard(policy, mock_backend)
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=600, remaining=400, pct_used=0.6,
        )
        with patch.object(threading.Thread, "start") as mock_start:
            _evaluate_thresholds(guard, [key], {"session": state}, {})
            mock_start.assert_not_called()


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
            provider=mock_provider,
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
            provider=mock_provider,
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

    def test_messages_property_returns_guarded(
        self, stub_guard: Guard, mock_provider: MagicMock
    ) -> None:
        """The .messages property returns a GuardedMessages, not raw SDK."""
        from tokencap.interceptor.anthropic import GuardedAnthropic, GuardedMessages

        mock_client = MagicMock()
        mock_client.__class__ = type("Anthropic", (), {})
        guarded = GuardedAnthropic(mock_client, stub_guard, mock_provider)
        assert isinstance(guarded.messages, GuardedMessages)

    def test_getattr_passthrough(
        self, stub_guard: Guard, mock_provider: MagicMock
    ) -> None:
        """Attributes not intercepted delegate to the real client."""
        from tokencap.interceptor.anthropic import GuardedAnthropic

        mock_client = MagicMock()
        mock_client.__class__ = type("Anthropic", (), {})
        mock_client.api_key = "sk-test"
        guarded = GuardedAnthropic(mock_client, stub_guard, mock_provider)
        assert guarded.api_key == "sk-test"

    def test_with_options_returns_guarded(
        self, stub_guard: Guard, mock_provider: MagicMock
    ) -> None:
        """with_options() returns a new GuardedAnthropic."""
        from tokencap.interceptor.anthropic import GuardedAnthropic

        mock_client = MagicMock()
        mock_client.__class__ = type("Anthropic", (), {})
        mock_client.with_options.return_value = mock_client
        guarded = GuardedAnthropic(mock_client, stub_guard, mock_provider)
        result = guarded.with_options(timeout=30)
        assert isinstance(result, GuardedAnthropic)

    def test_get_status(
        self, stub_guard: Guard, mock_provider: MagicMock
    ) -> None:
        """get_status() delegates to guard.get_status()."""
        from tokencap.interceptor.anthropic import GuardedAnthropic
        from tokencap.status.api import StatusResponse

        mock_client = MagicMock()
        mock_client.__class__ = type("Anthropic", (), {})
        guarded = GuardedAnthropic(mock_client, stub_guard, mock_provider)
        status = guarded.get_status()
        assert isinstance(status, StatusResponse)


class TestGuardedOpenAI:
    """Tests for GuardedOpenAI (mocked openai SDK)."""

    def test_chat_property_returns_guarded(
        self, stub_guard: Guard, mock_provider: MagicMock
    ) -> None:
        """The .chat property returns a GuardedChat."""
        from tokencap.interceptor.openai import GuardedChat, GuardedOpenAI

        mock_client = MagicMock()
        mock_client.__class__ = type("OpenAI", (), {})
        guarded = GuardedOpenAI(mock_client, stub_guard, mock_provider)
        assert isinstance(guarded.chat, GuardedChat)

    def test_completions_property(
        self, stub_guard: Guard, mock_provider: MagicMock
    ) -> None:
        """The .chat.completions property returns a GuardedCompletions."""
        from tokencap.interceptor.openai import GuardedCompletions, GuardedOpenAI

        mock_client = MagicMock()
        mock_client.__class__ = type("OpenAI", (), {})
        guarded = GuardedOpenAI(mock_client, stub_guard, mock_provider)
        assert isinstance(guarded.chat.completions, GuardedCompletions)

    def test_stream_injects_options(
        self, stub_guard: Guard, mock_provider: MagicMock
    ) -> None:
        """stream=True injects stream_options in a copy, not the original."""
        from tokencap.interceptor.openai import GuardedCompletions

        mock_completions = MagicMock()
        mock_completions.__class__ = type("Completions", (), {})
        gc = GuardedCompletions(
            mock_completions, stub_guard, mock_provider, is_async=False
        )
        original_kwargs = {"model": "gpt-4o", "stream": True, "messages": []}
        original_copy = dict(original_kwargs)
        result = gc.create(**original_kwargs)
        assert original_kwargs == original_copy
        assert isinstance(result, GuardedStream)

    def test_get_status(
        self, stub_guard: Guard, mock_provider: MagicMock
    ) -> None:
        """get_status() delegates to guard.get_status()."""
        from tokencap.interceptor.openai import GuardedOpenAI
        from tokencap.status.api import StatusResponse

        mock_client = MagicMock()
        mock_client.__class__ = type("OpenAI", (), {})
        guarded = GuardedOpenAI(mock_client, stub_guard, mock_provider)
        status = guarded.get_status()
        assert isinstance(status, StatusResponse)
