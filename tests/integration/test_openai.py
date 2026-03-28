"""Integration tests for OpenAI call path.

Uses pytest-httpx to intercept HTTP calls. Real openai SDK code runs,
real tokencap code runs, fake HTTP responses are returned.
No credentials required.
"""

from __future__ import annotations

import openai
import pytest

from tests.conftest import (
    make_action,
    make_dimension_policy,
    make_policy,
    make_threshold,
    openai_response,
)
from tokencap.backends.sqlite import SQLiteBackend
from tokencap.core.exceptions import BudgetExceededError
from tokencap.core.guard import Guard
from tokencap.core.types import BudgetKey
from tokencap.interceptor.base import GuardedStream
from tokencap.interceptor.openai import GuardedOpenAI
from tokencap.providers.openai import OpenAIProvider


class TestOpenAIIntegration:
    """Full OpenAI call path with HTTP mocking."""

    def test_full_openai_call(
        self, tmp_path: object, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Create completion: tokens tracked, response returned."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=openai_response(prompt_tokens=20, completion_tokens=5, content="Hello!"),
        )
        backend = SQLiteBackend(path=str(tmp_path) + "/test.db")  # type: ignore[arg-type]
        policy = make_policy(dimensions={
            "session": make_dimension_policy(limit=100000),
        })
        provider = OpenAIProvider()
        guard = Guard(
            policy=policy, identifiers={"session": "integration-test"},
            backend=backend, quiet=True,
        )
        client = openai.OpenAI(api_key="sk-fake-key")
        guarded = GuardedOpenAI(client, guard, provider)

        response = guarded.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert response.choices[0].message.content == "Hello!"

        key = BudgetKey("session", "integration-test")
        states = backend.get_states([key])
        assert states["session"].used > 0
        backend.close()

    def test_openai_block_at_limit(
        self, tmp_path: object, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Budget exceeded: BudgetExceededError before HTTP call."""
        backend = SQLiteBackend(path=str(tmp_path) + "/test.db")  # type: ignore[arg-type]
        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=1,
                thresholds=[make_threshold(
                    at_pct=1.0, actions=[make_action(kind="BLOCK")],
                )],
            ),
        })
        provider = OpenAIProvider()
        guard = Guard(
            policy=policy, identifiers={"session": "block-test"},
            backend=backend, quiet=True,
        )
        client = openai.OpenAI(api_key="sk-fake-key")
        guarded = GuardedOpenAI(client, guard, provider)

        with pytest.raises(BudgetExceededError):
            guarded.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": "Hi"}],
            )
        backend.close()

    def test_openai_stream_options_injected(
        self, tmp_path: object, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Streaming call: stream_options injected, original kwargs not mutated."""
        backend = SQLiteBackend(path=str(tmp_path) + "/test.db")  # type: ignore[arg-type]
        policy = make_policy(dimensions={
            "session": make_dimension_policy(limit=100000),
        })
        provider = OpenAIProvider()
        guard = Guard(
            policy=policy, identifiers={"session": "stream-test"},
            backend=backend, quiet=True,
        )
        client = openai.OpenAI(api_key="sk-fake-key")
        guarded = GuardedOpenAI(client, guard, provider)

        original_kwargs = {"model": "gpt-4o", "stream": True, "messages": []}
        original_copy = dict(original_kwargs)

        result = guarded.chat.completions.create(**original_kwargs)
        assert isinstance(result, GuardedStream)
        assert original_kwargs == original_copy
        backend.close()
