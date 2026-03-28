"""OpenAI provider: token estimation and usage extraction."""

from __future__ import annotations

from typing import Any

from tokencap.core.types import TokenUsage

try:
    import tiktoken

    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False


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
