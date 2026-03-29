"""Live tests for tokencap.patch() and tokencap.unpatch().

When API keys are set: uses patch() and makes real API calls through
patched constructors.
When absent: uses patch() with httpx transport monkeypatching to return
mock responses. The patch() code path is always exercised. Never skips.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import anthropic
import httpx
import openai

import tokencap
from tokencap.interceptor.anthropic import GuardedAnthropic


def teardown_function() -> None:
    """Ensure unpatch and teardown after each test."""
    tokencap.unpatch()
    tokencap.teardown()


def _mock_anthropic_transport() -> MagicMock:
    """Build an httpx transport mock that returns an Anthropic-shaped response."""
    body = (
        b'{"id":"msg_test","type":"message","role":"assistant",'
        b'"content":[{"type":"text","text":"Hello"}],'
        b'"model":"claude-haiku-4-5","stop_reason":"end_turn",'
        b'"stop_sequence":null,'
        b'"usage":{"input_tokens":15,"output_tokens":3}}'
    )
    mock_transport = MagicMock()
    mock_transport.handle_request.return_value = httpx.Response(
        status_code=200,
        headers={"content-type": "application/json"},
        content=body,
    )
    return mock_transport


def _mock_openai_transport() -> MagicMock:
    """Build an httpx transport mock that returns an OpenAI-shaped response."""
    body = (
        b'{"id":"chatcmpl-test","object":"chat.completion","created":1700000000,'
        b'"model":"gpt-4o-mini","choices":[{"index":0,"message":'
        b'{"role":"assistant","content":"Hello"},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":12,"completion_tokens":2,"total_tokens":14}}'
    )
    mock_transport = MagicMock()
    mock_transport.handle_request.return_value = httpx.Response(
        status_code=200,
        headers={"content-type": "application/json"},
        content=body,
    )
    return mock_transport


def test_patch_anthropic_live() -> None:
    """Full patch path for Anthropic. Never skips.

    Both real and mock paths exercise patch() -> anthropic.Anthropic() ->
    messages.create() through the patched constructor. Only the HTTP layer
    differs.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    tokencap.patch(limit=10_000, quiet=True)

    if api_key:
        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )
    else:
        # Mock path: patch() is active, so anthropic.Anthropic() returns
        # a GuardedAnthropic. We monkeypatch the httpx transport layer on
        # the underlying real client to return a mock response without
        # hitting the network.
        client = anthropic.Anthropic(
            api_key="sk-ant-fake",
            http_client=httpx.Client(transport=_mock_anthropic_transport()),
        )
        assert isinstance(client, GuardedAnthropic)
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )

    status = tokencap.get_status()
    assert status.dimensions["session"].used > 0


def test_patch_openai_live() -> None:
    """Full patch path for OpenAI. Never skips.

    Both real and mock paths exercise patch() -> openai.OpenAI() ->
    chat.completions.create() through the patched constructor. Only the
    HTTP layer differs.
    """
    api_key = os.environ.get("OPENAI_API_KEY")

    tokencap.patch(limit=10_000, quiet=True)

    if api_key:
        client = openai.OpenAI(api_key=api_key)
        client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )
    else:
        # Mock path: patch() is active, so openai.OpenAI() returns a
        # GuardedOpenAI. We inject a mock httpx transport.
        from tokencap.interceptor.openai import GuardedOpenAI

        client = openai.OpenAI(
            api_key="sk-fake",
            http_client=httpx.Client(transport=_mock_openai_transport()),
        )
        assert isinstance(client, GuardedOpenAI)
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
