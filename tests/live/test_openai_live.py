"""Live tests for OpenAI via the tokencap drop-in API.

When OPENAI_API_KEY is set: makes a real API call.
When absent: constructs a mock response matching the real SDK shape
and runs the full tokencap code path against it. Never skips.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import openai

import tokencap


def teardown_function() -> None:
    """Reset global Guard after each test."""
    tokencap.teardown()


def test_openai_live_or_mock() -> None:
    """Full drop-in API path for OpenAI. Never skips."""
    api_key = os.environ.get("OPENAI_API_KEY")

    if api_key:
        # Real API call
        client = tokencap.wrap(
            openai.OpenAI(api_key=api_key), limit=100_000, quiet=True
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=10,
            messages=[{"role": "user", "content": "Say hello in one word."}],
        )
        assert len(response.choices) > 0
        assert response.choices[0].message.content
    else:
        # Mock path: construct a response matching the real SDK shape
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello"
        mock_response.choices[0].message.role = "assistant"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "gpt-4o-mini"
        mock_response.usage.prompt_tokens = 12
        mock_response.usage.completion_tokens = 2
        del mock_response.parse  # not a raw response wrapper

        # Create a mock client that returns our response
        mock_client = MagicMock(spec=openai.OpenAI)
        mock_client.chat.completions.create.return_value = mock_response

        client = tokencap.wrap(mock_client, limit=100_000, quiet=True)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=10,
            messages=[{"role": "user", "content": "Say hello in one word."}],
        )
        assert response.choices[0].message.content == "Hello"

    # Common assertions for both paths
    status = tokencap.get_status()
    assert status.dimensions["session"].used > 0
