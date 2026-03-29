"""Integration tests for Anthropic call path.

Uses pytest-httpx to intercept HTTP calls. Real anthropic SDK code runs,
real tokencap code runs, fake HTTP responses are returned.
No credentials required.
"""

from __future__ import annotations

import anthropic
import pytest

from tests.conftest import (
    anthropic_response,
    make_action,
    make_dimension_policy,
    make_policy,
    make_threshold,
)
from tokencap.backends.sqlite import SQLiteBackend
from tokencap.core.enums import ActionKind
from tokencap.core.exceptions import BudgetExceededError
from tokencap.core.guard import Guard
from tokencap.core.types import BudgetKey
from tokencap.interceptor.anthropic import GuardedAnthropic
from tokencap.providers.anthropic import AnthropicProvider


class TestAnthropicIntegration:
    """Full Anthropic call path with HTTP mocking."""

    def test_full_anthropic_call(
        self, tmp_path: object, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Create message: tokens tracked, response returned."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=25, output_tokens=10, content="Hello!"),
        )
        backend = SQLiteBackend(path=str(tmp_path) + "/test.db")  # type: ignore[arg-type]
        policy = make_policy(dimensions={
            "session": make_dimension_policy(limit=100000),
        })
        provider = AnthropicProvider()
        guard = Guard(
            policy=policy, identifiers={"session": "integration-test"},
            backend=backend, quiet=True,
        )
        client = anthropic.Anthropic(api_key="sk-fake-key")
        guarded = GuardedAnthropic(client, guard, provider)

        response = guarded.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert response.content[0].text == "Hello!"

        key = BudgetKey("session", "integration-test")
        states = backend.get_states([key])
        assert states["session"].used > 0
        backend.close()

    def test_anthropic_block_at_limit(
        self, tmp_path: object, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Budget exceeded: BudgetExceededError before HTTP call."""
        backend = SQLiteBackend(path=str(tmp_path) + "/test.db")  # type: ignore[arg-type]
        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=1,
                thresholds=[make_threshold(
                    at_pct=1.0, actions=[make_action(kind=ActionKind.BLOCK)],
                )],
            ),
        })
        provider = AnthropicProvider()
        guard = Guard(
            policy=policy, identifiers={"session": "block-test"},
            backend=backend, quiet=True,
        )
        client = anthropic.Anthropic(api_key="sk-fake-key")
        guarded = GuardedAnthropic(client, guard, provider)

        with pytest.raises(BudgetExceededError):
            guarded.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=100,
                messages=[{"role": "user", "content": "Hi"}],
            )
        backend.close()

    def test_anthropic_reconciliation(
        self, tmp_path: object, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Actual differs from estimate: force_increment reconciles."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=25, output_tokens=10),
        )
        backend = SQLiteBackend(path=str(tmp_path) + "/test.db")  # type: ignore[arg-type]
        policy = make_policy(dimensions={
            "session": make_dimension_policy(limit=100000),
        })
        provider = AnthropicProvider()
        guard = Guard(
            policy=policy, identifiers={"session": "reconcile-test"},
            backend=backend, quiet=True,
        )
        client = anthropic.Anthropic(api_key="sk-fake-key")
        guarded = GuardedAnthropic(client, guard, provider)

        guarded.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        key = BudgetKey("session", "reconcile-test")
        states = backend.get_states([key])
        assert states["session"].used == 35  # 25 input + 10 output
        backend.close()


class TestAnthropicAsyncIntegration:
    """Async integration tests for Anthropic via tokencap drop-in API."""

    @pytest.mark.asyncio
    async def test_async_wrap_tracks_tokens(
        self,
        tmp_path: object,
        httpx_mock: object,  # type: ignore[type-arg]
    ) -> None:
        """wrap(AsyncAnthropic) + await create() tracks tokens end-to-end."""
        import tokencap

        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=20, output_tokens=10),
        )
        try:
            client = tokencap.wrap(
                anthropic.AsyncAnthropic(api_key="sk-fake-key"),
                limit=10_000, quiet=True,
            )
            response = await client.messages.create(
                model="claude-sonnet-4-6", max_tokens=100,
                messages=[{"role": "user", "content": "Hi"}],
            )
            assert response.content[0].text
            status = client.get_status()
            assert status.dimensions["session"].used > 0
        finally:
            tokencap.teardown()

    @pytest.mark.asyncio
    async def test_async_wrap_blocks_when_exceeded(
        self,
        httpx_mock: object,  # type: ignore[type-arg]
    ) -> None:
        """wrap(AsyncAnthropic, limit=1) raises BudgetExceededError."""
        import tokencap

        try:
            client = tokencap.wrap(
                anthropic.AsyncAnthropic(api_key="sk-fake-key"),
                limit=1, quiet=True,
            )
            with pytest.raises(BudgetExceededError):
                await client.messages.create(
                    model="claude-sonnet-4-6", max_tokens=100,
                    messages=[{"role": "user", "content": "Hi"}],
                )
        finally:
            tokencap.teardown()
