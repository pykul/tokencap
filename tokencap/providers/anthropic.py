"""Anthropic provider: token estimation, usage extraction, and pricing."""

from __future__ import annotations

import re
from typing import Any

from tokencap.core.types import TokenUsage

# Per-million-token rates: (input_rate, output_rate)
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (0.80, 4.0),
    "claude-3-opus": (15.0, 75.0),
    "claude-3-sonnet": (3.0, 15.0),
    "claude-3-haiku": (0.25, 1.25),
}

# Matches a trailing date suffix like -20251022
_VERSION_SUFFIX = re.compile(r"-\d{8}$")


def _strip_version(model: str) -> str:
    """Strip trailing date suffix from versioned model names."""
    return _VERSION_SUFFIX.sub("", model)


class AnthropicProvider:
    """Anthropic provider implementing the Provider protocol."""

    def estimate_tokens(self, request_kwargs: dict[str, Any]) -> int:
        """Estimate tokens from request kwargs. Never raises."""
        try:
            messages = request_kwargs.get("messages", [])
            return sum(len(str(m)) for m in messages) // 4
        except Exception:
            return 0

    def extract_usage(self, response: Any) -> TokenUsage:
        """Extract token usage from an Anthropic response. Never raises.

        If the response is a raw response wrapper (from with_raw_response),
        calls .parse() first to get the parsed message with usage data.
        """
        try:
            obj = response
            if hasattr(obj, "parse") and callable(obj.parse):
                try:
                    obj = obj.parse()
                except Exception:
                    pass
            usage = getattr(obj, "usage", None)
            if usage is None:
                return TokenUsage(input_tokens=0, output_tokens=0)
            return TokenUsage(
                input_tokens=getattr(usage, "input_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0),
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0),
            )
        except Exception:
            return TokenUsage(input_tokens=0, output_tokens=0)

    def get_model(self, request_kwargs: dict[str, Any]) -> str:
        """Extract model name from request kwargs. Never raises."""
        try:
            return str(request_kwargs.get("model", ""))
        except Exception:
            return ""

    def token_cost_usd(self, model: str, usage: TokenUsage) -> float:
        """Compute dollar cost for display. Never raises."""
        try:
            rates = _PRICING.get(model) or _PRICING.get(_strip_version(model))
            if rates is None:
                return 0.0
            input_rate, output_rate = rates
            return (
                usage.input_tokens * input_rate + usage.output_tokens * output_rate
            ) / 1_000_000
        except Exception:
            return 0.0
