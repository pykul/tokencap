"""OpenAI provider: token estimation, usage extraction, and pricing."""

from __future__ import annotations

from typing import Any

from tokencap.core.types import TokenUsage

try:
    import tiktoken

    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False

# Per-million-token rates: (input_rate, output_rate)
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-4": (30.0, 60.0),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.0, 60.0),
    "o1-mini": (3.0, 12.0),
    "o3": (10.0, 40.0),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
}


class OpenAIProvider:
    """OpenAI provider implementing the Provider protocol."""

    def estimate_tokens(self, request_kwargs: dict[str, Any]) -> int:
        """Estimate tokens from request kwargs. Never raises."""
        try:
            messages = request_kwargs.get("messages", [])
            text = str(messages)
            if _TIKTOKEN_AVAILABLE:
                model = request_kwargs.get("model", "gpt-4o")
                try:
                    enc = tiktoken.encoding_for_model(model)
                except KeyError:
                    enc = tiktoken.get_encoding("cl100k_base")
                return len(enc.encode(text))
            return len(text) // 4
        except Exception:
            return 0

    def extract_usage(self, response: Any) -> TokenUsage:
        """Extract token usage from an OpenAI response. Never raises.

        If the response is a raw response wrapper (from with_raw_response),
        calls .parse() first to get the parsed completion with usage data.
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
                input_tokens=getattr(usage, "prompt_tokens", 0),
                output_tokens=getattr(usage, "completion_tokens", 0),
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
            rates = _PRICING.get(model)
            if rates is None:
                return 0.0
            input_rate, output_rate = rates
            return (
                usage.input_tokens * input_rate + usage.output_tokens * output_rate
            ) / 1_000_000
        except Exception:
            return 0.0
