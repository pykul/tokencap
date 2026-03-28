"""Tests for tokencap.providers.anthropic and tokencap.providers.openai."""

from __future__ import annotations

from unittest.mock import MagicMock

from tokencap.core.types import TokenUsage
from tokencap.providers.anthropic import AnthropicProvider
from tokencap.providers.openai import OpenAIProvider


class TestAnthropicProvider:
    """Tests for AnthropicProvider."""

    def test_estimate_tokens_fallback(self) -> None:
        """Falls back to character-count estimation."""
        provider = AnthropicProvider()
        kwargs = {"messages": [{"role": "user", "content": "Hello world"}]}
        result = provider.estimate_tokens(kwargs)
        assert result > 0
        assert isinstance(result, int)

    def test_extract_usage_normal(self) -> None:
        """Extracts all usage fields from a response with usage attr."""
        provider = AnthropicProvider()
        response = MagicMock()
        del response.parse  # not a raw response wrapper
        response.usage.input_tokens = 100
        response.usage.output_tokens = 200
        response.usage.cache_read_input_tokens = 10
        response.usage.cache_creation_input_tokens = 5
        usage = provider.extract_usage(response)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 200
        assert usage.cache_read_tokens == 10
        assert usage.cache_write_tokens == 5

    def test_extract_usage_missing_cache_fields(self) -> None:
        """Missing cache fields default to 0."""
        provider = AnthropicProvider()
        response = MagicMock(spec=[])
        response.usage = MagicMock(spec=[])
        response.usage.input_tokens = 100
        response.usage.output_tokens = 200
        usage = provider.extract_usage(response)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 200
        assert usage.cache_read_tokens == 0
        assert usage.cache_write_tokens == 0

    def test_extract_usage_no_usage_attr(self) -> None:
        """Response with no .usage returns TokenUsage(0, 0)."""
        provider = AnthropicProvider()
        response = MagicMock(spec=[])
        usage = provider.extract_usage(response)
        assert usage == TokenUsage(input_tokens=0, output_tokens=0)

    def test_extract_usage_raw_response(self) -> None:
        """Raw response wrapper with .parse() returns correct usage."""
        provider = AnthropicProvider()
        parsed = MagicMock()
        parsed.usage.input_tokens = 80
        parsed.usage.output_tokens = 40
        parsed.usage.cache_read_input_tokens = 0
        parsed.usage.cache_creation_input_tokens = 0
        raw_response = MagicMock()
        raw_response.parse.return_value = parsed
        # raw_response itself has no .usage
        del raw_response.usage
        usage = provider.extract_usage(raw_response)
        assert usage == TokenUsage(input_tokens=80, output_tokens=40)

    def test_get_model(self) -> None:
        """Extracts model from kwargs."""
        provider = AnthropicProvider()
        assert provider.get_model({"model": "claude-sonnet-4-6"}) == "claude-sonnet-4-6"

    def test_get_model_missing(self) -> None:
        """Returns empty string when model not in kwargs."""
        provider = AnthropicProvider()
        assert provider.get_model({}) == ""

    def test_token_cost_known_model(self) -> None:
        """Known model returns non-zero cost."""
        provider = AnthropicProvider()
        usage = TokenUsage(input_tokens=1000, output_tokens=1000)
        cost = provider.token_cost_usd("claude-sonnet-4-6", usage)
        assert cost > 0.0

    def test_token_cost_versioned_model(self) -> None:
        """Version-suffixed model strips date and finds base pricing."""
        provider = AnthropicProvider()
        usage = TokenUsage(input_tokens=1000, output_tokens=1000)
        base_cost = provider.token_cost_usd("claude-sonnet-4-6", usage)
        versioned_cost = provider.token_cost_usd("claude-sonnet-4-6-20251022", usage)
        assert versioned_cost == base_cost
        assert versioned_cost > 0.0

    def test_token_cost_unknown_model(self) -> None:
        """Unknown model returns 0.0."""
        provider = AnthropicProvider()
        usage = TokenUsage(input_tokens=1000, output_tokens=1000)
        assert provider.token_cost_usd("unknown-model", usage) == 0.0


class TestOpenAIProvider:
    """Tests for OpenAIProvider."""

    def test_estimate_tokens_fallback(self) -> None:
        """Falls back to character-count estimation when tiktoken unavailable."""
        provider = OpenAIProvider()
        kwargs = {"messages": [{"role": "user", "content": "Hello world"}]}
        result = provider.estimate_tokens(kwargs)
        assert result > 0
        assert isinstance(result, int)

    def test_extract_usage_normal(self) -> None:
        """Extracts prompt_tokens and completion_tokens."""
        provider = OpenAIProvider()
        response = MagicMock()
        del response.parse  # not a raw response wrapper
        response.usage.prompt_tokens = 100
        response.usage.completion_tokens = 200
        usage = provider.extract_usage(response)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 200

    def test_extract_usage_no_usage(self) -> None:
        """Response with no .usage returns TokenUsage(0, 0)."""
        provider = OpenAIProvider()
        response = MagicMock(spec=[])
        usage = provider.extract_usage(response)
        assert usage == TokenUsage(input_tokens=0, output_tokens=0)

    def test_extract_usage_raw_response(self) -> None:
        """Raw response wrapper with .parse() returns correct usage."""
        provider = OpenAIProvider()
        parsed = MagicMock()
        parsed.usage.prompt_tokens = 80
        parsed.usage.completion_tokens = 40
        raw_response = MagicMock()
        raw_response.parse.return_value = parsed
        del raw_response.usage
        usage = provider.extract_usage(raw_response)
        assert usage == TokenUsage(input_tokens=80, output_tokens=40)

    def test_get_model(self) -> None:
        """Extracts model from kwargs."""
        provider = OpenAIProvider()
        assert provider.get_model({"model": "gpt-4o"}) == "gpt-4o"

    def test_token_cost_known_model(self) -> None:
        """Known model returns non-zero cost."""
        provider = OpenAIProvider()
        usage = TokenUsage(input_tokens=1000, output_tokens=1000)
        cost = provider.token_cost_usd("gpt-4o", usage)
        assert cost > 0.0

    def test_token_cost_unknown_model(self) -> None:
        """Unknown model returns 0.0."""
        provider = OpenAIProvider()
        usage = TokenUsage(input_tokens=1000, output_tokens=1000)
        assert provider.token_cost_usd("unknown-model", usage) == 0.0
