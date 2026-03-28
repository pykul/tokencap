"""Core data types for tokencap.

Pure dataclasses with no business logic. Every other module imports from here.
This file has no imports from any other tokencap module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetKey:
    """Uniquely identifies a budget counter in the backend store."""

    dimension: str
    identifier: str


@dataclass
class BudgetState:
    """Current snapshot of a single budget dimension. Read-only view."""

    key: BudgetKey
    limit: int
    used: int
    remaining: int
    pct_used: float


@dataclass
class CheckResult:
    """Outcome of a check_and_increment call."""

    allowed: bool
    states: dict[str, BudgetState]
    violated: list[str]


@dataclass
class TokenUsage:
    """Actual token counts extracted from a provider response."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        """Input + output tokens only.

        Cache tokens are tracked separately but excluded from the enforcement
        total to avoid double-counting on Anthropic prompt cache hits.
        """
        return self.input_tokens + self.output_tokens
