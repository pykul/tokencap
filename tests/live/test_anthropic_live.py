"""Live tests for Anthropic via the tokencap drop-in API.

When ANTHROPIC_API_KEY is set: makes a real API call.
When absent: constructs a mock response matching the real SDK shape
and runs the full tokencap code path against it. Never skips.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import anthropic

import tokencap


def teardown_function() -> None:
    """Reset global Guard after each test."""
    tokencap.teardown()


def test_anthropic_live_or_mock() -> None:
    """Full drop-in API path for Anthropic. Never skips."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if api_key:
        # Real API call
        client = tokencap.wrap(
            anthropic.Anthropic(api_key=api_key), limit=100_000, quiet=True
        )
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[{"role": "user", "content": "Say hello in one word."}],
        )
        assert len(response.content) > 0
        assert response.content[0].text
    else:
        # Mock path: construct a response matching the real SDK shape
        # and exercise the full tokencap code path
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "Hello"
        mock_response.content[0].type = "text"
        mock_response.model = "claude-haiku-4-5"
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 15
        mock_response.usage.output_tokens = 3
        del mock_response.parse  # not a raw response wrapper

        # Create a mock client that returns our response
        mock_client = MagicMock(spec=anthropic.Anthropic)
        mock_client.messages.create.return_value = mock_response

        client = tokencap.wrap(mock_client, limit=100_000, quiet=True)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[{"role": "user", "content": "Say hello in one word."}],
        )
        assert response.content[0].text == "Hello"

    # Common assertions for both paths
    status = tokencap.get_status()
    assert status.dimensions["session"].used > 0
