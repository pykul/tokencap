"""Live tests for tokencap.patch() and tokencap.unpatch().

When API keys are set: uses patch() and makes real API calls through
patched constructors.
When absent: constructs mock response objects matching the real SDK shape
and runs the full tokencap code path via wrap(). Never skips.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import anthropic
import openai

import tokencap
from tokencap.interceptor.anthropic import GuardedAnthropic


def teardown_function() -> None:
    """Ensure unpatch and teardown after each test."""
    tokencap.unpatch()
    tokencap.teardown()


def test_patch_anthropic_live() -> None:
    """Full patch path for Anthropic. Never skips."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if api_key:
        # Real path: patch() intercepts the constructor
        tokencap.patch(limit=10_000, quiet=True)
        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )
    else:
        # Mock path: construct a response matching the real SDK shape.
        # Use wrap() directly because patch() replaces the Anthropic class
        # with a factory, which breaks MagicMock(spec=...).
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "Hello"
        mock_response.content[0].type = "text"
        mock_response.model = "claude-haiku-4-5"
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 15
        mock_response.usage.output_tokens = 3
        del mock_response.parse  # not a raw response wrapper

        mock_client = MagicMock(spec=anthropic.Anthropic)
        mock_client.messages.create.return_value = mock_response

        client = tokencap.wrap(mock_client, limit=10_000, quiet=True)
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )

    status = tokencap.get_status()
    assert status.dimensions["session"].used > 0


def test_patch_openai_live() -> None:
    """Full patch path for OpenAI. Never skips."""
    api_key = os.environ.get("OPENAI_API_KEY")

    if api_key:
        # Real path: patch() intercepts the constructor
        tokencap.patch(limit=10_000, quiet=True)
        client = openai.OpenAI(api_key=api_key)
        client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )
    else:
        # Mock path: construct a response matching the real SDK shape.
        # Use wrap() directly because patch() replaces the OpenAI class
        # with a factory, which breaks MagicMock(spec=...).
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello"
        mock_response.choices[0].message.role = "assistant"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "gpt-4o-mini"
        mock_response.usage.prompt_tokens = 12
        mock_response.usage.completion_tokens = 2
        del mock_response.parse  # not a raw response wrapper

        mock_client = MagicMock(spec=openai.OpenAI)
        mock_client.chat.completions.create.return_value = mock_response

        client = tokencap.wrap(mock_client, limit=10_000, quiet=True)
        client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )

    status = tokencap.get_status()
    assert status.dimensions["session"].used > 0


def test_patch_unpatch_restores_sdk() -> None:
    """patch() wraps constructors, unpatch() restores them. Never skips."""
    tokencap.patch(limit=10_000, quiet=True)

    # After patch, new Anthropic instances should be GuardedAnthropic
    client = anthropic.Anthropic(api_key="sk-fake")
    assert isinstance(client, GuardedAnthropic)

    tokencap.unpatch()

    # After unpatch, new Anthropic instances should be real Anthropic
    real_client = anthropic.Anthropic(api_key="sk-fake")
    assert not isinstance(real_client, GuardedAnthropic)
