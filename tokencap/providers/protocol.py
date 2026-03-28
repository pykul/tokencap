"""Provider Protocol: token estimation and usage extraction.

All provider-specific logic lives in the provider implementations.
The interceptor functions in interceptor/base.py never import anthropic
or openai directly — they use this protocol.
"""

from __future__ import annotations

from typing import Any, Protocol

from tokencap.core.types import TokenUsage


class Provider(Protocol):
    """Protocol for LLM provider token estimation and usage extraction."""

    def estimate_tokens(self, request_kwargs: dict[str, Any]) -> int:
        """Estimate token count from request kwargs before the API call.

        May undercount. Actual usage is reconciled post-call via force_increment.
        Must never raise. Return a conservative estimate on any failure.
        """
        ...

    def extract_usage(self, response: Any) -> TokenUsage:
        """Extract actual token usage from the provider response object.

        Must handle all response types the provider returns (sync, streaming).
        Must never raise. Return TokenUsage(0, 0) on any failure.
        """
        ...

    def get_model(self, request_kwargs: dict[str, Any]) -> str:
        """Extract the model name string from request kwargs.

        Returns an empty string on failure. Never raises.
        """
        ...

    def token_cost_usd(self, model: str, usage: TokenUsage) -> float:
        """Compute dollar cost for display purposes only.

        Never used for enforcement decisions.
        Returns 0.0 for unknown models. Never raises.
        """
        ...
